"""
Handler for ChatJoinRequest updates.

When auto mode is enabled for a client, each assigned worker has a unique
Telegram invite link (WorkLink.url = https://t.me/+xxxx, creates_join_request=True).

When a user clicks that link → Telegram sends a ChatJoinRequest update.
This handler:
  1. Identifies which WorkLink the request belongs to (by invite URL in DB)
  2. Increments WorkLink.attracted_count atomically
  3. Recalculates worker (and referrer) balance
  4. Updates LinkAssignment.last_count_updated_at (inactivity tracking)
  5. Approves the join request so the user enters the channel immediately

If the invite link is not found in our DB (e.g. a manually created link),
the handler auto-approves anyway so no user gets stuck waiting.
"""
import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

router = Router(name="join_request")


@router.chat_join_request()
async def handle_chat_join_request(update: ChatJoinRequest) -> None:
    """
    Fired when someone requests to join via an invite link with creates_join_request=True.
    Always approves. Counts toward the matching WorkLink if the URL is ours.
    """
    invite_url = update.invite_link.invite_link if update.invite_link else None

    if invite_url:
        try:
            work_link_pk = await sync_to_async(_find_work_link_pk)(invite_url)
            if work_link_pk:
                await sync_to_async(_increment_count)(work_link_pk)
                logger.info(
                    "join_request: counted user=%s via work_link=%s",
                    update.from_user.id, work_link_pk,
                )
            else:
                logger.debug(
                    "join_request: url not in DB, approve-only url=%s", invite_url
                )
        except Exception as exc:
            logger.error(
                "join_request: error counting url=%s user=%s: %s",
                invite_url, update.from_user.id, exc,
            )

    # Always approve — never leave user waiting
    try:
        await update.approve()
    except Exception as exc:
        logger.warning(
            "join_request: approve failed user=%s chat=%s: %s",
            update.from_user.id, update.chat.id, exc,
        )


def _find_work_link_pk(invite_url: str):
    """Return pk of the active WorkLink whose url matches invite_url, or None."""
    from apps.users.models import WorkLink
    wl = WorkLink.objects.filter(url=invite_url, is_active=True).values_list("pk", flat=True).first()
    return wl  # pk or None


def _increment_count(work_link_pk: int) -> None:
    """
    Atomically increment WorkLink.attracted_count and recalculate related balances.
    Uses select_for_update to handle concurrent join requests on the same link safely.
    """
    from django.db import transaction
    from django.db.models import Sum
    from django.utils import timezone

    from apps.users.models import WorkLink
    from apps.users.services import UserService
    from apps.clients.models import LinkAssignment

    with transaction.atomic():
        wl = WorkLink.objects.select_for_update().filter(pk=work_link_pk, is_active=True).first()
        if not wl:
            return

        wl.attracted_count += 1
        wl.save(update_fields=["attracted_count"])

        # Touch assignment inactivity tracker
        try:
            assignment = LinkAssignment.objects.filter(work_link=wl, is_active=True).first()
            if assignment:
                assignment.last_count_updated_at = timezone.now()
                assignment.save(update_fields=["last_count_updated_at"])
        except Exception as exc:
            logger.warning("_increment_count: failed to touch assignment pk=%s: %s", work_link_pk, exc)

    # Recalculate worker balance — also syncs User.attracted_count = active_link.attracted_count
    UserService.recalculate_balance(wl.user)

    # Compute total across ALL WorkLinks for milestone tracking (does NOT update attracted_count —
    # recalculate_balance already handled that correctly to reflect active link only)
    total = (
        WorkLink.objects
        .filter(user_id=wl.user_id)
        .aggregate(total=Sum("attracted_count"))["total"] or 0
    )

    # Update conversion milestone timestamps (first_activity_at, reached_60_at)
    from apps.stats.services import update_user_metrics
    update_user_metrics(wl.user_id, total)
