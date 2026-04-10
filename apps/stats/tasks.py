"""
Celery tasks for stats:
  - send_daily_broadcast_task   — sends daily stats to all active workers + curators
  - send_admin_reminder_task    — scheduled reminder "к 23:00 МСК внеси данные"
  - check_missing_daily_report_task — every 15 min: if after 23:01 МСК and no report → urgent
"""
import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from celery import shared_task

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")


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
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.exceptions import TelegramForbiddenError
        from django.conf import settings

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
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

    # Mark bot-blocked users
    if blocked_ids:
        from apps.users.models import User as UserModel
        UserModel.objects.filter(telegram_id__in=blocked_ids).update(is_blocked_bot=True)

    DailyReport.objects.filter(pk=report_id).update(broadcast_sent=True)
    logger.info("send_daily_broadcast_task: sent to %d recipients", len(recipients))


@shared_task(name="apps.stats.tasks.send_admin_reminder_task", bind=True, ignore_result=True)
def send_admin_reminder_task(self) -> None:
    """
    Send a gentle reminder to all admins: "к 23:00 МСК нужно внести данные".
    Called at 13:00 МСК and 20:00 МСК.
    Skipped if DailyReport for today already exists.
    """
    from apps.stats.services import DailyReportService
    if DailyReportService.exists_for_today():
        return

    from apps.users.models import User, UserRole
    admins = list(
        User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )
    if not admins:
        return

    text = "⏰ <b>Напоминание:</b> к 23:00 МСК нужно внести данные за сегодня!"

    async def _send():
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from django.conf import settings

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            for tg_id in admins:
                try:
                    await bot.send_message(tg_id, text)
                except Exception as exc:
                    logger.warning("admin_reminder: failed to send to %s: %s", tg_id, exc)
        finally:
            await bot.session.close()

    asyncio.run(_send())


@shared_task(name="apps.stats.tasks.check_missing_daily_report_task", bind=True, ignore_result=True)
def check_missing_daily_report_task(self) -> None:
    """
    Runs every 15 min. If current time is after 23:01 МСК (20:01 UTC)
    and DailyReport for today is missing → send urgent reminder to all admins.
    Idempotent: sends at most once per calendar day via cache flag.
    """
    now_msk = datetime.datetime.now(tz=_MSK)

    # Only trigger between 23:01 and 06:00 МСК (next day up to 06:00)
    hour, minute = now_msk.hour, now_msk.minute
    after_deadline = (hour == 23 and minute >= 1) or (0 <= hour < 6)
    if not after_deadline:
        return

    from apps.stats.services import DailyReportService
    if DailyReportService.exists_for_today():
        return

    from apps.users.models import User, UserRole
    admins = list(
        User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
        .values_list("telegram_id", flat=True)
    )
    if not admins:
        return

    text = (
        "🚨 <b>ВНИМАНИЕ!</b> Данные за сегодня ещё не внесены!\n\n"
        "Внеси данные, чтобы рассылка ушла пользователям."
    )

    async def _send():
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from django.conf import settings

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            for tg_id in admins:
                try:
                    await bot.send_message(tg_id, text)
                except Exception as exc:
                    logger.warning("urgent_reminder: failed to send to %s: %s", tg_id, exc)
        finally:
            await bot.session.close()

    asyncio.run(_send())
