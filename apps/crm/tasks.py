"""
CRM Celery tasks:

  crm_check_deadline_task     — runs at 00:05 MSK daily
                                Creates DeadlineMiss if entries are incomplete for yesterday.
                                Sends Telegram notification to workspace OWNER(s).

  crm_weekly_report_task      — runs every Monday at 08:00 MSK
                                Sends weekly summary to workspace OWNER(s) via Telegram.

  send_crm_report_notification_task — triggered after DailySummaryReport is created.
                                Sends report text to workspace OWNER(s) via Telegram.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from celery import shared_task

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")


def _make_bot():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from django.conf import settings
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _get_workspace_owner_ids(workspace) -> list:
    """Sync: return list of telegram_ids for active, non-blocked workspace owners."""
    from apps.crm.models import WorkspaceMembership, CRMRole
    return list(
        WorkspaceMembership.objects.filter(
            workspace=workspace,
            role=CRMRole.OWNER,
            is_active=True,
            user__is_blocked_bot=False,
        ).values_list("user__telegram_id", flat=True)
    )


async def _send_to_ids(telegram_ids: list, text: str) -> None:
    """Async: send `text` to each telegram_id in the list."""
    bot = _make_bot()
    try:
        for tg_id in telegram_ids:
            try:
                await bot.send_message(tg_id, text)
            except Exception as exc:
                logger.warning("CRM: failed to notify owner %s: %s", tg_id, exc)
    finally:
        await bot.session.close()


@shared_task(name="apps.crm.tasks.crm_check_deadline_task", bind=True, ignore_result=True)
def crm_check_deadline_task(self) -> None:
    """
    Runs at 00:05 MSK via Celery Beat.
    For each active workspace: checks if yesterday's entries are complete.
    If not → creates/updates DeadlineMiss, notifies OWNER via Telegram.
    """
    from apps.crm.models import Workspace
    from apps.crm.services import DeadlineService

    for workspace in Workspace.objects.filter(is_active=True):
        miss = DeadlineService.check_and_record(workspace)
        if miss:
            parts = []
            if miss.finance_missing:
                parts.append("💳 Финансовые данные (Cash Flow)")
            if miss.applications_missing:
                parts.append("📋 Данные по заявкам")

            text = (
                f"⚠️ <b>[{workspace.name} CRM] Дедлайн пропущен!</b>\n\n"
                f"Дата: <b>{miss.date.strftime('%d.%m.%Y')}</b>\n\n"
                f"Не внесено:\n" + "\n".join(f"  • {p}" for p in parts) + "\n\n"
                f"Данные можно внести задним числом в CRM."
            )
            owner_ids = _get_workspace_owner_ids(workspace)
            asyncio.run(_send_to_ids(owner_ids, text))
            logger.warning(
                "CRM deadline miss: workspace=%s date=%s finance=%s apps=%s",
                workspace.slug, miss.date, miss.finance_missing, miss.applications_missing,
            )


@shared_task(name="apps.crm.tasks.send_crm_report_notification_task", bind=True, ignore_result=True)
def send_crm_report_notification_task(self, report_id: int) -> None:
    """
    Sends the generated daily report to all workspace OWNER(s) via Telegram.
    Triggered automatically after both entries are submitted.
    """
    from apps.crm.models import DailySummaryReport
    try:
        report = DailySummaryReport.objects.select_related("workspace").get(pk=report_id)
    except DailySummaryReport.DoesNotExist:
        logger.warning("CRM: report %s not found", report_id)
        return

    if report.telegram_sent:
        return

    text = (
        f"✅ <b>[{report.workspace.name} CRM] Отчёт готов</b>\n\n"
        + report.report_text
    )

    owner_ids = _get_workspace_owner_ids(report.workspace)
    asyncio.run(_send_to_ids(owner_ids, text))
    DailySummaryReport.objects.filter(pk=report_id).update(telegram_sent=True)
    logger.info("CRM: report %s sent to workspace owners", report_id)


@shared_task(name="apps.crm.tasks.crm_weekly_report_task", bind=True, ignore_result=True)
def crm_weekly_report_task(self) -> None:
    """
    Runs every Monday at 08:00 MSK.
    Sends a weekly summary for last week to workspace OWNER(s).
    """
    from apps.crm.models import Workspace, DailySummaryReport, FinanceEntry, ApplicationEntry
    from django.db.models import Sum

    now_msk  = datetime.datetime.now(tz=_MSK)
    today    = now_msk.date()
    # "Last week" = Mon–Sun of the previous ISO week
    last_mon = today - datetime.timedelta(days=today.weekday() + 7)
    last_sun = last_mon + datetime.timedelta(days=6)

    for workspace in Workspace.objects.filter(is_active=True):
        fin_agg = FinanceEntry.objects.filter(
            workspace=workspace, date__gte=last_mon, date__lte=last_sun
        ).aggregate(
            total_income=Sum("income"),
            total_expenses=Sum("expenses"),
            total_pp=Sum("pp_earnings"),
            total_privat=Sum("privat_earnings"),
        )
        app_agg = ApplicationEntry.objects.filter(
            workspace=workspace, date__gte=last_mon, date__lte=last_sun
        ).aggregate(
            total_apps=Sum("applications_count"),
            total_apps_earn=Sum("applications_earnings"),
        )

        income   = fin_agg["total_income"]   or 0
        expenses = fin_agg["total_expenses"] or 0
        pp       = fin_agg["total_pp"]       or 0
        privat   = fin_agg["total_privat"]   or 0
        apps     = app_agg["total_apps"]     or 0
        apps_earn = app_agg["total_apps_earn"] or 0
        balance  = income - expenses
        sign     = "+" if balance >= 0 else ""

        text = (
            f"📅 <b>[{workspace.name}] Итоги недели</b>\n"
            f"<b>{last_mon.strftime('%d.%m')} – {last_sun.strftime('%d.%m.%Y')}</b>\n\n"
            f"💳 Заработок с ПП: <b>{pp:.2f} $</b>\n"
            f"🏦 Заработок с Привата: <b>{privat:.2f} $</b>\n"
            f"📋 Заявок за неделю: <b>{apps} шт.</b>\n"
            f"💰 Заработок с заявок: <b>{apps_earn:.2f} $</b>\n\n"
            f"⚖️ Сальдо недели:\n"
            f"   Поступления: +{income:.2f} $\n"
            f"   Расходы: -{expenses:.2f} $\n"
            f"   Итого: {sign}{balance:.2f} $"
        )

        owner_ids = _get_workspace_owner_ids(workspace)
        asyncio.run(_send_to_ids(owner_ids, text))
        logger.info("CRM weekly report sent for workspace %s (week %s)", workspace.slug, last_mon)
