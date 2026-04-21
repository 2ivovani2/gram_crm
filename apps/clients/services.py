"""
Business logic for client/link/assignment management.

Key operations:
  AssignmentService.auto_assign(client_link, invite_url=None)
    → find best available worker → create LinkAssignment + replace WorkLink
    → if invite_url provided, use it as WorkLink.url (auto mode)
    → if none found → return None (caller notifies admins)

  AssignmentService.unassign(assignment, reason)
    → mark assignment inactive, archive WorkLink, optionally notify worker

  AssignmentService.deactivate_link(client_link, note)
    → deactivate link, unassign active worker, notify assigned worker + admins

  JoinService.submit(user, message)
    → create JoinRequest (if no pending exists)

  JoinService.approve(request, admin_user)
    → activate user, close request

  JoinService.reject(request, admin_user)
    → close request with REJECTED status

  AutoModeService.check_permissions(client)
    → async-safe sync wrapper: checks bot admin status + can_invite_users in channel
    → saves result to client.bot_check_status / bot_check_detail / bot_check_at

  AutoModeService.create_invite_link_sync(chat_id, label)
    → creates Telegram invite link with creates_join_request=True
    → returns invite URL string, or None on failure

Auto-assignment rule:
  Pick an ACTIVE worker (role=WORKER or CURATOR) using two-step selection:
    1. Find the minimum active-assignment count across all eligible workers.
    2. Collect all workers sharing that minimum load.
    3. Choose one at random from that pool (fair distribution, no deterministic tie-breaking).
  In manual mode: worker gets WorkLink with URL = ClientLink.url.
  In auto mode:   worker gets WorkLink with URL = unique Telegram invite link.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

import datetime

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Join Request Service ──────────────────────────────────────────────────────

class JoinServiceError(Exception):
    pass


class JoinService:

    @staticmethod
    def get_pending(user) -> Optional["JoinRequest"]:
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(user=user, status=JoinRequestStatus.PENDING).first()

    @staticmethod
    def get_any_request(user) -> Optional["JoinRequest"]:
        """Return the latest request (any status) for display."""
        from apps.users.models import JoinRequest
        return JoinRequest.objects.filter(user=user).order_by("-created_at").first()

    @staticmethod
    def submit(user, message: str = "") -> "JoinRequest":
        """Create a new join request. Raises JoinServiceError if one already pending."""
        from apps.users.models import JoinRequest, JoinRequestStatus
        if JoinRequest.objects.filter(user=user, status=JoinRequestStatus.PENDING).exists():
            raise JoinServiceError("У вас уже есть активная заявка на рассмотрении.")
        if user.is_activated:
            raise JoinServiceError("Вы уже активированы.")
        return JoinRequest.objects.create(user=user, message=message)

    @staticmethod
    @transaction.atomic
    def approve(request: "JoinRequest", admin_user) -> None:
        from apps.users.models import JoinRequestStatus
        if not request.is_pending:
            raise JoinServiceError("Заявка уже обработана.")
        request.user.activate()
        request.status = JoinRequestStatus.APPROVED
        request.reviewed_by = admin_user
        request.reviewed_at = timezone.now()
        request.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    @staticmethod
    @transaction.atomic
    def reject(request: "JoinRequest", admin_user) -> None:
        from apps.users.models import JoinRequestStatus
        if not request.is_pending:
            raise JoinServiceError("Заявка уже обработана.")
        request.status = JoinRequestStatus.REJECTED
        request.reviewed_by = admin_user
        request.reviewed_at = timezone.now()
        request.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    @staticmethod
    def get_pending_list():
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(
            status=JoinRequestStatus.PENDING
        ).select_related("user").order_by("-created_at")

    @staticmethod
    def count_pending() -> int:
        from apps.users.models import JoinRequest, JoinRequestStatus
        return JoinRequest.objects.filter(status=JoinRequestStatus.PENDING).count()


# ─── Assignment Service ────────────────────────────────────────────────────────

class AssignmentService:

    # Selection rule: prefer workers with 0 active assignments, then fewest assignments.
    # Workers with ACTIVE status, not blocked. WORKER or CURATOR roles.
    INACTIVITY_DAYS = 3

    @staticmethod
    def _find_best_worker():
        """
        Pick a worker with the lowest current active-assignment count.
        When multiple workers share the minimum load, choose one at random
        (fair distribution — no deterministic tie-breaking by pk / created_at).
        """
        from apps.users.models import User, UserRole, UserStatus
        from django.db.models import Count, Q

        candidates = list(
            User.objects
            .filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
                is_blocked_bot=False,
                is_activated=True,
            )
            .annotate(active_assignments=Count(
                "link_assignments",
                filter=Q(link_assignments__is_active=True),
            ))
            .order_by("active_assignments")   # only sort by load; no secondary key
        )

        if not candidates:
            return None

        min_load = candidates[0].active_assignments
        pool = [w for w in candidates if w.active_assignments == min_load]
        return random.choice(pool)

    @staticmethod
    @transaction.atomic
    def auto_assign(client_link: "ClientLink", invite_url: Optional[str] = None) -> Optional["LinkAssignment"]:
        """
        Find best available worker, assign to client_link.
        Returns the new LinkAssignment, or None if no worker found.

        invite_url: if provided (auto mode), use this as the worker's WorkLink URL
                    instead of client_link.url. Should be a unique Telegram invite link.
        """
        from apps.clients.models import LinkAssignment
        from apps.users.services import UserService

        # Sanity: only one active assignment per link
        if client_link.assignments.filter(is_active=True).exists():
            logger.warning("auto_assign called but link %s already has active assignment", client_link.pk)
            return client_link.assignments.filter(is_active=True).first()

        worker = AssignmentService._find_best_worker()
        if not worker:
            return None

        # In auto mode: WorkLink URL = unique invite link; in manual mode: client link URL
        url_for_worker = invite_url if invite_url else client_link.url
        work_link, _ = UserService.replace_work_link(worker, url_for_worker)

        assignment = LinkAssignment.objects.create(
            client_link=client_link,
            worker=worker,
            work_link=work_link,
            last_count_updated_at=timezone.now(),
        )
        logger.info("auto_assign: link %s → worker %s (assignment %s, auto=%s)",
                    client_link.pk, worker.pk, assignment.pk, bool(invite_url))
        return assignment

    @staticmethod
    @transaction.atomic
    def manual_assign(client_link: "ClientLink", worker, invite_url: Optional[str] = None) -> "LinkAssignment":
        """
        Admin manually assigns a specific worker to a link.

        invite_url: if provided (auto mode), use as WorkLink URL instead of client_link.url.
        """
        from apps.clients.models import LinkAssignment, UnassignReason
        from apps.users.services import UserService

        # Unassign current worker if any
        existing = client_link.assignments.filter(is_active=True).first()
        if existing:
            AssignmentService.unassign(existing, UnassignReason.REASSIGNED)

        url_for_worker = invite_url if invite_url else client_link.url
        work_link, _ = UserService.replace_work_link(worker, url_for_worker)
        assignment = LinkAssignment.objects.create(
            client_link=client_link,
            worker=worker,
            work_link=work_link,
            last_count_updated_at=timezone.now(),
        )
        return assignment

    @staticmethod
    @transaction.atomic
    def unassign(assignment: "LinkAssignment", reason: str) -> None:
        """Unassign worker from link, archive their WorkLink."""
        from apps.users.services import UserService
        from apps.clients.models import UnassignReason

        assignment.is_active = False
        assignment.unassigned_at = timezone.now()
        assignment.unassign_reason = reason
        assignment.save(update_fields=["is_active", "unassigned_at", "unassign_reason"])

        # Archive the WorkLink (freeze attracted_count), give worker empty URL
        if reason != UnassignReason.LINK_DEACTIVATED:
            UserService.replace_work_link(assignment.worker, "")
        else:
            # On deactivation just clear URL without creating a new active link
            UserService.clear_work_url(assignment.worker)

        logger.info("unassign: assignment %s, reason=%s", assignment.pk, reason)

    @staticmethod
    @transaction.atomic
    def deactivate_link(client_link: "ClientLink", note: str = "") -> list:
        """
        Deactivate a client link.
        - Set link status to INACTIVE
        - Unassign active worker (reason: LINK_DEACTIVATED)
        Returns list of worker telegram_ids that were unassigned (for notifications).
        """
        from apps.clients.models import UnassignReason

        client_link.deactivate(note=note)

        unassigned_workers = []
        active = client_link.assignments.filter(is_active=True).select_related("worker")
        for assignment in active:
            unassigned_workers.append(assignment.worker.telegram_id)
            AssignmentService.unassign(assignment, UnassignReason.LINK_DEACTIVATED)

        return unassigned_workers

    @staticmethod
    def touch_count_updated(assignment: "LinkAssignment") -> None:
        """Call this whenever admin updates attracted_count for the assigned worker."""
        assignment.last_count_updated_at = timezone.now()
        assignment.save(update_fields=["last_count_updated_at"])

    @staticmethod
    def get_inactive_assignments(days: int = INACTIVITY_DAYS):
        """Return active assignments where last_count_updated_at is older than `days` days."""
        from apps.clients.models import LinkAssignment
        cutoff = timezone.now() - datetime.timedelta(days=days)
        return (
            LinkAssignment.objects
            .filter(
                is_active=True,
                last_count_updated_at__lt=cutoff,
                last_count_updated_at__isnull=False,
            )
            .select_related("worker", "client_link__client")
        )


# ─── Channel input parser ──────────────────────────────────────────────────────

def _parse_channel_input(text: str):
    """
    Parse a free-form channel reference into (identifier, kind).

    kind:
      'username'  — @handle or t.me/handle → pass as "@handle" to getChat
      'numeric'   — plain numeric chat_id   → pass as int to getChat
      'invite'    — t.me/+ private link     → cannot resolve via getChat
      'invalid'   — could not parse

    Examples:
      "@gramly"            → ("@gramly", "username")
      "t.me/gramly"        → ("@gramly", "username")
      "https://t.me/gramly"→ ("@gramly", "username")
      "gramly"             → ("@gramly", "username")
      "-1001234567890"     → (-1001234567890, "numeric")
      "t.me/+AbCdEfG"      → ("t.me/+AbCdEfG", "invite")
    """
    import re
    text = text.strip()
    if not text:
        return ("", "invalid")

    # Private invite links — cannot resolve with getChat
    if "t.me/+" in text or "t.me/joinchat/" in text:
        return (text, "invite")

    # Strip URL scheme and t.me/ prefix, but stop at first /
    cleaned = text
    for prefix in ("https://", "http://"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned.lower().startswith("t.me/"):
        cleaned = cleaned[len("t.me/"):]
    cleaned = cleaned.strip("/")

    # @username (possibly with @ already)
    if cleaned.startswith("@"):
        username = cleaned.lstrip("@")
        if username:
            return ("@" + username, "username")

    # Numeric Chat ID (possibly negative, e.g. -1001234567890)
    try:
        return (int(cleaned), "numeric")
    except ValueError:
        pass

    # Bare word — treat as username if it looks like one
    if re.match(r'^[A-Za-z0-9_]{4,}$', cleaned):
        return ("@" + cleaned, "username")

    return (text, "invalid")


async def _async_resolve_channel(identifier) -> dict:
    """
    Call bot.get_chat(identifier) to resolve a username or numeric chat_id.
    Returns {ok, chat_id, username, detail}.
    Works for public channels (@username) and any chat the bot is member of (numeric id).
    Does NOT work for private invite links.
    """
    bot = _make_auto_bot()
    try:
        chat = await bot.get_chat(identifier)
        if chat.username:
            display = f"@{chat.username}"
        elif chat.title:
            display = chat.title
        else:
            display = str(chat.id)
        return {"ok": True, "chat_id": chat.id, "username": display}
    except Exception as exc:
        err = str(exc).lower()
        if "chat not found" in err or "bad request" in err:
            detail = (
                "Канал не найден. Убедитесь, что ссылка или @username правильные "
                "и что бот уже добавлен в канал."
            )
        elif "forbidden" in err:
            detail = (
                "Нет доступа к каналу. "
                "Добавьте бота в канал как администратора и попробуйте снова."
            )
        else:
            detail = f"Не удалось найти канал: {exc}"
        return {"ok": False, "chat_id": None, "username": "", "detail": detail}
    finally:
        await bot.session.close()


# ─── Auto-Mode Service ─────────────────────────────────────────────────────────

class AutoModeService:
    """
    Handles Telegram invite link generation and bot permission checks.

    Auto mode flow (new UX):
      1. Admin pastes channel link / @username / Chat ID in the auto-mode panel
      2. resolve_and_setup(client, channel_input) → parse → resolve → check → enable
      3. On success: channel_id saved, bot_check_status=ok, auto_mode=True
      4. On assignment: create_invite_link_sync(channel_id, label) → unique URL
      5. WorkLink.url = that unique URL (instead of ClientLink.url)
      6. Bot handles chat_join_request events → increments WorkLink.attracted_count
    """

    @staticmethod
    def resolve_and_setup(client: "Client", channel_input: str) -> dict:
        """
        All-in-one: parse user input → resolve channel via Telegram API →
        check bot permissions → enable auto_mode if OK.

        Accepts:
          @username, t.me/channel, https://t.me/channel, or numeric Chat ID.
          Private invite links (t.me/+...) cannot be resolved — returns invite_link=True.

        Returns:
          {ok: bool, invite_link: bool, detail: str}
        """
        import asyncio
        from apps.clients.models import BotCheckStatus

        identifier, kind = _parse_channel_input(channel_input)

        if kind == "invalid":
            return {
                "ok": False,
                "invite_link": False,
                "detail": (
                    "Не удалось распознать ввод. "
                    "Вставьте @username, ссылку t.me/channel или числовой Chat ID."
                ),
            }

        if kind == "invite":
            return {
                "ok": False,
                "invite_link": True,
                "detail": (
                    "Это приватная ссылка-приглашение — по ней нельзя автоматически определить ID канала. "
                    "Раскройте блок «Приватный канал» ниже и введите Chat ID вручную "
                    "(например: −1001234567890). "
                    "Его можно узнать через бота @userinfobot — перешлите ему любое сообщение из канала."
                ),
            }

        # Step 1: Resolve channel → get chat_id and display name
        try:
            resolve = asyncio.run(_async_resolve_channel(identifier))
        except Exception as exc:
            return {"ok": False, "invite_link": False, "detail": f"Ошибка подключения: {exc}"}

        if not resolve["ok"]:
            client.bot_check_status = BotCheckStatus.NO_ACCESS
            client.bot_check_detail = resolve["detail"]
            client.bot_check_at = timezone.now()
            client.auto_mode = False
            client.save(update_fields=["bot_check_status", "bot_check_detail", "bot_check_at", "auto_mode"])
            return {"ok": False, "invite_link": False, "detail": resolve["detail"]}

        # Save resolved channel info immediately so error states show the channel name
        client.channel_id = resolve["chat_id"]
        client.channel_username = resolve["username"]
        client.save(update_fields=["channel_id", "channel_username"])

        # Step 2: Check bot permissions in the resolved channel
        try:
            perm_result = asyncio.run(_async_check_permissions(resolve["chat_id"]))
        except Exception as exc:
            client.bot_check_status = BotCheckStatus.NO_ACCESS
            client.bot_check_detail = f"Ошибка проверки прав: {exc}"
            client.bot_check_at = timezone.now()
            client.save(update_fields=["bot_check_status", "bot_check_detail", "bot_check_at"])
            return {"ok": False, "invite_link": False, "detail": f"Ошибка проверки прав: {exc}"}

        _save_check_result(client, perm_result)

        if perm_result["ok"]:
            client.auto_mode = True
            client.save(update_fields=["auto_mode"])

        return {
            "ok": perm_result["ok"],
            "invite_link": False,
            "detail": perm_result.get("detail", ""),
        }

    @staticmethod
    def recheck_and_enable(client: "Client") -> dict:
        """
        Re-run permission check on the already-saved client.channel_id.
        Enables auto_mode if the check passes.
        Called when admin clicks «Проверить снова» after fixing bot permissions.
        Returns {ok: bool, invite_link: bool, detail: str}
        """
        import asyncio
        from apps.clients.models import BotCheckStatus

        if not client.channel_id:
            return {
                "ok": False,
                "invite_link": False,
                "detail": "Chat ID канала не указан. Сначала подключите канал.",
            }

        try:
            perm_result = asyncio.run(_async_check_permissions(client.channel_id))
        except Exception as exc:
            return {"ok": False, "invite_link": False, "detail": f"Ошибка: {exc}"}

        _save_check_result(client, perm_result)

        if perm_result["ok"]:
            client.auto_mode = True
            client.save(update_fields=["auto_mode"])

        return {
            "ok": perm_result["ok"],
            "invite_link": False,
            "detail": perm_result.get("detail", ""),
        }

    @staticmethod
    def check_permissions(client: "Client") -> dict:
        """
        Synchronous wrapper: checks bot permissions in client.channel_id.
        Saves status to client.bot_check_status / bot_check_detail / bot_check_at.
        Returns dict: {ok: bool, status: str, detail: str}
        """
        import asyncio
        from apps.clients.models import BotCheckStatus

        if not client.channel_id:
            result = {
                "ok": False,
                "status": BotCheckStatus.NO_ACCESS,
                "detail": "Chat ID не указан. Введите Telegram Chat ID канала.",
            }
            _save_check_result(client, result)
            return result

        try:
            result = asyncio.run(_async_check_permissions(client.channel_id))
        except Exception as exc:
            logger.warning("AutoModeService.check_permissions asyncio.run failed: %s", exc)
            result = {
                "ok": False,
                "status": BotCheckStatus.NO_ACCESS,
                "detail": f"Ошибка проверки: {exc}",
            }

        _save_check_result(client, result)
        return result

    @staticmethod
    def create_invite_link_sync(chat_id: int, label: str = "") -> Optional[str]:
        """
        Create a unique Telegram invite link with creates_join_request=True.
        Returns the invite URL string, or None on failure.
        Fails silently — caller falls back to manual link URL.
        """
        import asyncio
        try:
            return asyncio.run(_async_create_invite_link(chat_id, label))
        except Exception as exc:
            logger.warning("AutoModeService.create_invite_link_sync failed chat_id=%s: %s", chat_id, exc)
            return None

    @staticmethod
    def revoke_invite_link_sync(chat_id: int, invite_link: str) -> None:
        """
        Revoke a previously created invite link (e.g. on worker unassignment).
        Fails silently — the assignment is already unassigned regardless.
        """
        import asyncio
        try:
            asyncio.run(_async_revoke_invite_link(chat_id, invite_link))
        except Exception as exc:
            logger.warning("AutoModeService.revoke_invite_link_sync failed: %s", exc)


def _make_auto_bot():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from django.conf import settings
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _async_check_permissions(chat_id: int) -> dict:
    from apps.clients.models import BotCheckStatus
    bot = _make_auto_bot()
    try:
        # Try to get bot's own info
        bot_info = await bot.get_me()
        member = await bot.get_chat_member(chat_id, bot_info.id)

        # Also get chat info for username
        try:
            chat = await bot.get_chat(chat_id)
            chat_username = f"@{chat.username}" if chat.username else chat.title or str(chat_id)
        except Exception:
            chat_username = str(chat_id)

        if member.status not in ("administrator", "creator"):
            return {
                "ok": False,
                "status": BotCheckStatus.NOT_ADMIN,
                "detail": (
                    f"Бот не является администратором в {chat_username}. "
                    "Добавьте бота в канал и назначьте администратором."
                ),
                "chat_username": chat_username,
            }

        # Check can_invite_users permission
        can_invite = getattr(member, "can_invite_users", False)
        if not can_invite:
            return {
                "ok": False,
                "status": BotCheckStatus.NO_PERMISSIONS,
                "detail": (
                    f"Бот — администратор в {chat_username}, но у него нет права "
                    "«Добавление участников» (can_invite_users). "
                    "Включите это право в настройках администраторов канала."
                ),
                "chat_username": chat_username,
            }

        return {
            "ok": True,
            "status": BotCheckStatus.OK,
            "detail": f"Бот — администратор в {chat_username} с правом создавать ссылки-приглашения.",
            "chat_username": chat_username,
        }

    except Exception as exc:
        err = str(exc)
        if "chat not found" in err.lower() or "bad request" in err.lower():
            detail = (
                f"Канал с ID {chat_id} не найден. "
                "Проверьте Chat ID — он должен быть числом, например -1001234567890."
            )
        elif "forbidden" in err.lower() or "member" in err.lower():
            detail = (
                f"Бот не имеет доступа к каналу {chat_id}. "
                "Убедитесь, что бот добавлен в канал."
            )
        else:
            detail = f"Не удалось подключиться к каналу: {exc}"
        return {
            "ok": False,
            "status": BotCheckStatus.NO_ACCESS,
            "detail": detail,
        }
    finally:
        await bot.session.close()


async def _async_create_invite_link(chat_id: int, label: str = "") -> Optional[str]:
    bot = _make_auto_bot()
    try:
        link = await bot.create_chat_invite_link(
            chat_id,
            name=label[:32] if label else None,   # Telegram label limit: 32 chars
            creates_join_request=True,             # each click → ChatJoinRequest event
        )
        return link.invite_link
    finally:
        await bot.session.close()


async def _async_revoke_invite_link(chat_id: int, invite_link: str) -> None:
    bot = _make_auto_bot()
    try:
        await bot.revoke_chat_invite_link(chat_id, invite_link)
    finally:
        await bot.session.close()


def _save_check_result(client: "Client", result: dict) -> None:
    from apps.clients.models import BotCheckStatus
    client.bot_check_status = result.get("status", BotCheckStatus.NO_ACCESS)
    client.bot_check_detail = result.get("detail", "")
    if result.get("chat_username"):
        client.channel_username = result["chat_username"]
    client.bot_check_at = timezone.now()
    # If status is no longer OK, disable auto mode to avoid silent failures
    if not result.get("ok") and client.auto_mode:
        client.auto_mode = False
        logger.info("AutoModeService: auto_mode disabled for client %s (check failed)", client.pk)
    client.save(update_fields=[
        "bot_check_status", "bot_check_detail", "bot_check_at",
        "channel_username", "auto_mode",
    ])
