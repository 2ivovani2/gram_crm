"""
Celery tasks for client/link/assignment management.

  check_worker_inactivity_task — daily at 09:00 МСК.
    Finds active LinkAssignments where last_count_updated_at < now - 3 days.
    For each: unassigns worker, notifies worker via bot, notifies all admins.
"""
import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _make_bot():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from django.conf import settings
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@shared_task(name="apps.clients.tasks.check_worker_inactivity_task", bind=True, ignore_result=True)
def check_worker_inactivity_task(self) -> None:
    """
    Daily task: find workers with no attracted_count updates for 3+ days
    and unassign them from their client links.
    """
    from apps.clients.services import AssignmentService
    from apps.clients.models import UnassignReason

    inactive = list(AssignmentService.get_inactive_assignments(days=3))
    if not inactive:
        logger.info("check_worker_inactivity_task: no inactive assignments found")
        return

    logger.info("check_worker_inactivity_task: found %d inactive assignments", len(inactive))

    unassigned = []
    for assignment in inactive:
        worker = assignment.worker
        client_link = assignment.client_link
        try:
            AssignmentService.unassign(assignment, UnassignReason.INACTIVITY)
            unassigned.append({
                "worker_tg_id": worker.telegram_id,
                "worker_name": worker.display_name,
                "client_nick": client_link.client.nick,
                "link_url": client_link.url,
            })
            logger.info(
                "check_worker_inactivity_task: unassigned worker %s from link %s (inactivity)",
                worker.pk, client_link.pk,
            )
        except Exception as exc:
            logger.error(
                "check_worker_inactivity_task: error unassigning assignment %s: %s",
                assignment.pk, exc,
            )

    if not unassigned:
        return

    async def _notify():
        from apps.users.models import User, UserRole
        bot = _make_bot()
        try:
            # Notify each unassigned worker
            for item in unassigned:
                try:
                    await bot.send_message(
                        item["worker_tg_id"],
                        f"⚠️ <b>Ссылка снята из-за неактивности</b>\n\n"
                        f"Клиент: <b>{item['client_nick']}</b>\n"
                        f"URL: <code>{item['link_url']}</code>\n\n"
                        "Вы не обновляли количество заявок более 3 дней.\n"
                        "Свяжитесь с администратором для получения новой ссылки.",
                    )
                except Exception as exc:
                    logger.warning("check_worker_inactivity_task: worker notify failed %s: %s",
                                   item["worker_tg_id"], exc)

            # Notify admins with summary
            admin_ids = list(
                User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
                .values_list("telegram_id", flat=True)
            )
            if admin_ids:
                lines = [f"⚠️ <b>Неактивные воркеры — снято {len(unassigned)} назначений</b>\n"]
                for item in unassigned:
                    lines.append(f"• {item['worker_name']} → {item['client_nick']}")
                text = "\n".join(lines)
                for tg_id in admin_ids:
                    try:
                        await bot.send_message(tg_id, text)
                    except Exception as exc:
                        logger.warning("check_worker_inactivity_task: admin notify failed %s: %s",
                                       tg_id, exc)
        finally:
            await bot.session.close()

    asyncio.run(_notify())
    logger.info("check_worker_inactivity_task: done, unassigned %d workers", len(unassigned))
