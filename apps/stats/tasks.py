"""
Celery tasks for stats:
  - send_daily_broadcast_task       — sends daily stats to all active workers + curators
  - send_admin_reminder_task        — scheduled reminder at 13:00 and 20:00 МСК
  - check_missing_daily_report_task — every 15 min:
      * 23:01–00:59 МСК: if no report for the control date → urgent reminder to admins
      * 01:00–01:59 МСК: if still no report → create MissedDay record (idempotent)
      * outside both windows → no-op

control_date logic:
  Between 00:00–01:59 МСК we're still monitoring the PREVIOUS calendar day.
  At 02:00+ МСК we switch to monitoring today.
"""
import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from celery import shared_task

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")


def _make_bot():
    """Create a fresh Bot instance for use in async tasks."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from django.conf import settings
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _send_to_admins(text: str) -> None:
    """Send a message to all non-blocked admins."""
    from apps.users.models import User, UserRole
    admins = list(
        User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )
    if not admins:
        return
    bot = _make_bot()
    try:
        for tg_id in admins:
            try:
                await bot.send_message(tg_id, text)
            except Exception as exc:
                logger.warning("_send_to_admins: failed to send to %s: %s", tg_id, exc)
    finally:
        await bot.session.close()


@shared_task(name="apps.stats.tasks.send_daily_broadcast_task", bind=True, ignore_result=True)
def send_daily_broadcast_task(self, report_id: int) -> None:
    """
    Send daily report stats to all active workers and curators.
    Guarded by report.broadcast_sent flag — idempotent.
    """
    from apps.stats.models import DailyReport
    from apps.users.models import User, UserRole, UserStatus

    try:
        report = DailyReport.objects.get(pk=report_id)
    except DailyReport.DoesNotExist:
        logger.warning("send_daily_broadcast_task: report %s not found", report_id)
        return

    if report.broadcast_sent:
        logger.info("send_daily_broadcast_task: already sent for report %s", report_id)
        return

    recipients = list(
        User.objects.filter(
            role__in=[UserRole.WORKER, UserRole.CURATOR],
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        ).values_list("telegram_id", flat=True)
    )

    if not recipients:
        DailyReport.objects.filter(pk=report_id).update(broadcast_sent=True)
        return

    total_worker_payout = report.total_worker_payout
    text = (
        f"📊 <b>Данные за {report.date.strftime('%d.%m.%Y')}:</b>\n\n"
        f"📝 Заявок за сегодня: <b>{report.total_applications} шт.</b>\n"
        f"💰 Заработано за сегодня: <b>{total_worker_payout:.2f} руб.</b>"
    )

    async def _send():
        from aiogram.exceptions import TelegramForbiddenError
        bot = _make_bot()
        blocked = []
        try:
            for tg_id in recipients:
                try:
                    await bot.send_message(tg_id, text)
                    await asyncio.sleep(0.05)
                except TelegramForbiddenError:
                    blocked.append(tg_id)
                except Exception as exc:
                    logger.warning("daily_broadcast: failed to send to %s: %s", tg_id, exc)
        finally:
            await bot.session.close()
        return blocked

    blocked_ids = asyncio.run(_send())

    if blocked_ids:
        from apps.users.models import User as UserModel
        UserModel.objects.filter(telegram_id__in=blocked_ids).update(is_blocked_bot=True)

    DailyReport.objects.filter(pk=report_id).update(broadcast_sent=True)
    logger.info("send_daily_broadcast_task: sent to %d recipients", len(recipients))


@shared_task(name="apps.stats.tasks.send_admin_reminder_task", bind=True, ignore_result=True)
def send_admin_reminder_task(self) -> None:
    """
    Send a gentle reminder to all admins at 13:00 and 20:00 МСК.
    Skipped if DailyReport for today already exists.
    """
    from apps.stats.services import DailyReportService
    if DailyReportService.exists_for_today():
        return

    asyncio.run(_send_to_admins(
        "⏰ <b>Напоминание:</b> к 23:00 МСК нужно внести данные за сегодня!"
    ))


@shared_task(name="apps.stats.tasks.check_missing_daily_report_task", bind=True, ignore_result=True)
def check_missing_daily_report_task(self) -> None:
    """
    Runs every 15 min via Celery Beat.

    Reminder window  23:01–00:59 МСК:
        If no DailyReport for control_date → send urgent reminder to all admins.

    Mark-missed window  01:00–01:59 МСК:
        If still no report → MissedDay.get_or_create(date=control_date).
        Unique constraint makes this idempotent (safe to run 4x/hour).

    control_date:
        00:00–01:59 МСК → yesterday (the day that just ended without a report)
        02:00+     МСК → today
    """
    now_msk = datetime.datetime.now(tz=_MSK)
    hour, minute = now_msk.hour, now_msk.minute
    today = now_msk.date()

    # The calendar day we are monitoring for a report
    control_date = (today - datetime.timedelta(days=1)) if hour < 2 else today

    from apps.stats.models import DailyReport, MissedDay
    has_report = DailyReport.objects.filter(date=control_date).exists()

    # ── Reminder window: 23:01–00:59 МСК ─────────────────────────────────────
    in_reminder_window = (hour == 23 and minute >= 1) or hour == 0

    if in_reminder_window:
        if has_report:
            return  # report submitted, stop spamming
        asyncio.run(_send_to_admins(
            "🚨 <b>ВНИМАНИЕ!</b> Данные за сегодня ещё не внесены!\n\n"
            "Внеси данные, чтобы рассылка ушла пользователям."
        ))
        return

    # ── Mark-missed window: 01:00–01:59 МСК ──────────────────────────────────
    if hour == 1 and not has_report:
        _, created = MissedDay.objects.get_or_create(date=control_date)
        if created:
            logger.warning(
                "check_missing_daily_report_task: day %s marked as MISSED (no report submitted)",
                control_date,
            )

    # Outside both windows → no-op
