"""
Telegram client lifecycle management via Telethon.

Responsibilities:
- Auth flow (send code → verify → save session)
- Profile updates (name, bio)
- Sending messages / commenting on posts
- Managing active client pool
- Membership cache to avoid re-joining channels
"""

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import Dict, Optional, Set

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InviteRequestSentError,
    PeerFloodError,
    SessionPasswordNeededError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
    UserNotParticipantError,
)
from telethon.tl.types import Channel, Chat
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

logger = logging.getLogger(__name__)

SESSION_DIR = Path("data/sessions")


# ── Membership persistence helpers ─────────────────────────────────────────────

def _persist_membership_sync(account_id: int, url: str) -> None:
    from database import SessionLocal, AccountMembership
    from sqlalchemy.exc import IntegrityError
    db = SessionLocal()
    try:
        db.add(AccountMembership(account_id=account_id, channel_url=url))
        db.commit()
    except IntegrityError:
        db.rollback()  # already exists — unique constraint
    except Exception as e:
        db.rollback()
        logger.warning(f"membership persist failed [{account_id}] {url}: {e}")
    finally:
        db.close()


def _load_memberships_sync(account_id: int) -> set:
    from database import SessionLocal, AccountMembership
    db = SessionLocal()
    try:
        rows = db.query(AccountMembership.channel_url).filter(
            AccountMembership.account_id == account_id
        ).all()
        return {r.channel_url for r in rows}
    except Exception as e:
        logger.warning(f"membership load failed [{account_id}]: {e}")
        return set()
    finally:
        db.close()

# phone → {client, phone_code_hash, api_id, api_hash}
_pending_auth: Dict[str, dict] = {}

# account_id → TelegramClient
_active_clients: Dict[int, TelegramClient] = {}

# account_id → set of channel URLs confirmed joined this process lifetime
_member_cache: Dict[int, Set[str]] = {}

_INVITE_RE = re.compile(r"(?:t\.me/\+|t\.me/joinchat/)([A-Za-z0-9_-]+)", re.IGNORECASE)


# ── Session helpers ────────────────────────────────────────────────────────────

def _session_path(phone: str) -> str:
    clean = re.sub(r"\D", "", phone)
    return str(SESSION_DIR / f"session_{clean}")


# ── Auth flow ──────────────────────────────────────────────────────────────────

async def send_auth_code(api_id: int, api_hash: str, phone: str) -> dict:
    # Close any previous pending auth client for this phone (re-send case)
    if phone in _pending_auth:
        try:
            await _pending_auth[phone]["client"].disconnect()
        except Exception:
            pass
        del _pending_auth[phone]

    path = _session_path(phone)
    session_file = Path(path + ".session")

    # Remove stale/invalid session file so Telethon starts clean
    if session_file.exists():
        try:
            session_file.unlink()
            logger.info(f"Removed stale session file for {phone}")
        except Exception as e:
            logger.warning(f"Could not remove session file for {phone}: {e}")

    client = TelegramClient(path, api_id, api_hash, receive_updates=False)
    await client.connect()

    if await client.is_user_authorized():
        await client.disconnect()
        return {"status": "already_authorized"}

    result = await client.send_code_request(phone)
    logger.info(f"Auth code sent to {phone}, type={type(result.type).__name__}")
    _pending_auth[phone] = {
        "client": client,
        "phone_code_hash": result.phone_code_hash,
        "api_id": api_id,
        "api_hash": api_hash,
    }
    return {"status": "code_sent", "code_type": type(result.type).__name__}


async def verify_auth_code(phone: str, code: str, password: Optional[str] = None) -> dict:
    if phone not in _pending_auth:
        raise ValueError("No pending auth session for this phone. Send the code first.")

    data = _pending_auth[phone]
    client: TelegramClient = data["client"]

    try:
        await client.sign_in(phone, code, phone_code_hash=data["phone_code_hash"])
    except SessionPasswordNeededError:
        if not password:
            raise ValueError("2FA_REQUIRED")
        await client.sign_in(password=password)

    me = await client.get_me()
    session_file = _session_path(phone) + ".session"
    del _pending_auth[phone]
    # Disconnect auth client so the session SQLite file is released.
    # get_client() will open a fresh connection when needed — two open
    # connections to the same SQLite file cause "database is locked".
    await client.disconnect()

    return {
        "user_id": me.id,
        "username": me.username,
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "session_file": session_file,
    }


# ── Client pool ────────────────────────────────────────────────────────────────

async def get_client(account) -> Optional[TelegramClient]:
    aid = account.id

    if aid in _active_clients:
        c = _active_clients[aid]
        if c.is_connected():
            return c
        del _active_clients[aid]

    path = _session_path(account.phone)
    client = TelegramClient(path, account.api_id, account.api_hash, receive_updates=False)
    await client.connect()

    if not await client.is_user_authorized():
        logger.warning(f"Account {account.phone} session not authorized")
        await client.disconnect()
        # Mark account as error in DB so the UI reflects reality immediately
        try:
            from database import SessionLocal, Account as AccountModel
            _db = SessionLocal()
            _acc = _db.query(AccountModel).filter(AccountModel.id == account.id).first()
            if _acc and _acc.status not in ("error", "pending"):
                _acc.status = "error"
                _db.commit()
        except Exception as _e:
            logger.warning(f"Failed to mark account {account.phone} as error: {_e}")
        finally:
            _db.close()
        return None

    _active_clients[aid] = client
    return client


async def reconnect_client(account) -> Optional[TelegramClient]:
    """Force-close and reconnect the client. Call after 'disconnected' errors."""
    aid = account.id
    if aid in _active_clients:
        try:
            await _active_clients[aid].disconnect()
        except Exception:
            pass
        del _active_clients[aid]
    # Member cache stays — we're still a member of those channels
    logger.info(f"Reconnecting client for {account.phone}")
    return await get_client(account)


async def disconnect_client(account_id: int):
    if account_id in _active_clients:
        try:
            await _active_clients[account_id].disconnect()
        except Exception:
            pass
        del _active_clients[account_id]


def delete_session_file(phone: str):
    session_file = Path(_session_path(phone) + ".session")
    if session_file.exists():
        try:
            session_file.unlink()
            logger.info(f"Deleted session file for {phone}")
        except Exception as e:
            logger.warning(f"Could not delete session file for {phone}: {e}")


# ── Profile ────────────────────────────────────────────────────────────────────

async def update_profile(account, first_name: str = "", last_name: str = "", bio: str = ""):
    client = await get_client(account)
    if not client:
        raise RuntimeError("Client not authorized")
    await client(UpdateProfileRequest(first_name=first_name, last_name=last_name, about=bio))
    logger.info(f"Profile updated for {account.phone}")


async def set_profile_photo(account, photo_bytes):
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    client = await get_client(account)
    if not client:
        raise RuntimeError("Client not authorized")
    uploaded = await client.upload_file(photo_bytes, file_name="photo.jpg")
    await client(UploadProfilePhotoRequest(file=uploaded))
    logger.info(f"Profile photo updated for {account.phone}")


async def fetch_me(account) -> dict:
    client = await get_client(account)
    if not client:
        return {"status": "error", "reason": "not_authorized"}
    try:
        me = await client.get_me()
        return {
            "status": "online",
            "user_id": me.id,
            "username": me.username,
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ── Channel membership ─────────────────────────────────────────────────────────

def is_cached_member(account_id: int, url: str) -> bool:
    return url in _member_cache.get(account_id, set())


async def mark_joined(account_id: int, url: str):
    _member_cache.setdefault(account_id, set()).add(url)
    asyncio.create_task(asyncio.to_thread(_persist_membership_sync, account_id, url))


async def preload_memberships(account_id: int):
    """Restore persisted memberships into the in-memory cache on worker startup."""
    if account_id in _member_cache:
        return  # already loaded this session
    urls = await asyncio.to_thread(_load_memberships_sync, account_id)
    if urls:
        _member_cache.setdefault(account_id, set()).update(urls)
        logger.info(f"[{account_id}] Preloaded {len(urls)} memberships from DB")


async def ensure_joined(client: TelegramClient, account_id: int, url: str) -> dict:
    """
    Make sure the client is a member of the channel.

    Returns one of:
      {"ok": True}
      {"ok": False, "reason": "flood_wait",         "seconds": N}
      {"ok": False, "reason": "approval_required"}
      {"ok": False, "reason": "expired"}
      {"ok": False, "reason": "private"}
      {"ok": False, "reason": "error", "detail": str}
    """
    if is_cached_member(account_id, url):
        return {"ok": True}

    invite_match = _INVITE_RE.search(url)
    try:
        if invite_match:
            invite_hash = invite_match.group(1)
            try:
                await client(ImportChatInviteRequest(invite_hash))
                logger.info(f"[{account_id}] Joined via invite: {url}")
            except UserAlreadyParticipantError:
                logger.debug(f"[{account_id}] Already in {url}")
            except InviteRequestSentError:
                logger.warning(f"[{account_id}] Approval required: {url}")
                return {"ok": False, "reason": "approval_required"}
        else:
            try:
                entity = await client.get_entity(url)
                # Bots and users can't be joined as channels — skip permanently
                if not isinstance(entity, (Channel, Chat)):
                    logger.warning(f"[{account_id}] {url} is a bot/user, not a channel — skipping")
                    return {"ok": False, "reason": "not_channel"}
                await client(JoinChannelRequest(entity))
                logger.info(f"[{account_id}] Joined channel: {url}")
            except UserAlreadyParticipantError:
                logger.debug(f"[{account_id}] Already in {url}")
            except InviteRequestSentError:
                logger.warning(f"[{account_id}] Approval required: {url}")
                return {"ok": False, "reason": "approval_required"}
            except ChannelPrivateError:
                logger.warning(f"[{account_id}] Private channel, no access: {url}")
                return {"ok": False, "reason": "private"}

        await mark_joined(account_id, url)
        return {"ok": True}

    except FloodWaitError as e:
        logger.warning(f"[{account_id}] FloodWait {e.seconds}s on join {url}")
        return {"ok": False, "reason": "flood_wait", "seconds": e.seconds}
    except Exception as e:
        msg = str(e)
        if "Cannot send requests while disconnected" in msg or "disconnected" in msg.lower():
            logger.warning(f"[{account_id}] Client disconnected on join {url}")
            return {"ok": False, "reason": "disconnected"}
        if "expired" in msg.lower() or "invalid" in msg.lower():
            return {"ok": False, "reason": "expired"}
        logger.warning(f"[{account_id}] Could not join {url}: {e}")
        return {"ok": False, "reason": "error", "detail": msg}


# ── Sending ────────────────────────────────────────────────────────────────────

async def send_message_to(
    client: TelegramClient,
    account_id: int,
    url: str,
    text: str,
    comment_mode: bool = False,
) -> dict:
    """
    Send a message or comment. Returns:
      {"ok": True}
      {"ok": False, "reason": "flood_wait",    "seconds": N}
      {"ok": False, "reason": "peer_flood"}
      {"ok": False, "reason": "banned"}
      {"ok": False, "reason": "forbidden"}     # broadcast channel
      {"ok": False, "reason": "not_member"}
      {"ok": False, "reason": "error", "detail": str}
    """
    try:
        entity = await client.get_entity(url)

        # Simulate typing for 1–3 s so the action appears human to Telegram servers
        try:
            async with client.action(entity, "typing"):
                await asyncio.sleep(random.uniform(1, 3))
        except Exception:
            pass  # typing action is best-effort; never block the actual send

        if comment_mode:
            # Try to comment on the latest post in the channel's discussion group.
            # Falls back to direct send for groups (supergroups) that have no linked channel.
            msgs = await client.get_messages(entity, limit=5)
            commented = False
            for post in msgs:
                if post is None:
                    continue
                try:
                    await client.send_message(entity, text, comment_to=post.id)
                    commented = True
                    break
                except Exception:
                    continue
            if not commented:
                # No discussion group or comment failed for all posts — send directly.
                # For broadcast channels this will raise ChatWriteForbiddenError → "forbidden".
                await client.send_message(entity, text)
        else:
            await client.send_message(entity, text)

        return {"ok": True}

    except FloodWaitError as e:
        logger.warning(f"[{account_id}] FloodWait {e.seconds}s sending to {url}")
        return {"ok": False, "reason": "flood_wait", "seconds": e.seconds}
    except PeerFloodError:
        logger.error(f"[{account_id}] PeerFlood on {url}")
        return {"ok": False, "reason": "peer_flood"}
    except UserBannedInChannelError:
        logger.error(f"[{account_id}] Banned in {url}")
        return {"ok": False, "reason": "banned"}
    except (ChatWriteForbiddenError, ChatAdminRequiredError):
        logger.warning(f"[{account_id}] Write forbidden in {url} (broadcast/admin-only channel)")
        return {"ok": False, "reason": "forbidden"}
    except UserNotParticipantError:
        logger.warning(f"[{account_id}] Not a participant: {url}")
        _member_cache.get(account_id, set()).discard(url)
        return {"ok": False, "reason": "not_member"}
    except Exception as e:
        msg = str(e)
        if "Cannot send requests while disconnected" in msg or "disconnected" in msg.lower():
            logger.warning(f"[{account_id}] Client disconnected on send to {url}")
            return {"ok": False, "reason": "disconnected"}
        # Invite-link channels where get_entity fails after join — rare edge case
        if "not part of" in msg.lower() or "not participant" in msg.lower():
            _member_cache.get(account_id, set()).discard(url)
            return {"ok": False, "reason": "not_member"}
        # Broadcast/admin-only channels wrapped as generic exceptions
        if "admin privileges" in msg.lower() or "chat_admin_required" in msg.lower():
            logger.warning(f"[{account_id}] Admin-only channel {url} — skipping permanently")
            return {"ok": False, "reason": "forbidden"}
        logger.error(f"[{account_id}] send error → {url}: {e}")
        return {"ok": False, "reason": "error", "detail": msg}


async def search_public_channels(client: TelegramClient, query: str, limit: int = 50) -> list:
    """Search Telegram for public channels/groups matching query."""
    from telethon.tl.functions.contacts import SearchRequest
    from telethon.tl.types import Channel
    try:
        result = await client(SearchRequest(q=query, limit=min(limit, 100)))
    except Exception as e:
        logger.warning(f"Telegram channel search failed: {e}")
        return []
    channels = []
    for chat in result.chats:
        if not isinstance(chat, Channel):
            continue
        username = getattr(chat, "username", None)
        if not username:
            continue
        channels.append({
            "username":     username,
            "url":          f"@{username}",
            "title":        chat.title or username,
            "members":      getattr(chat, "participants_count", None),
            "is_broadcast": getattr(chat, "broadcast", False),
            "description":  None,
            "source":       "telegram",
        })
    return channels


async def import_session_file(session_bytes: bytes, api_id: int, api_hash: str) -> dict:
    """
    Validate a raw Telethon .session file and return account info.
    Writes to a temp path; on success caller should rename to proper session path.
    """
    import os, uuid
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    tmp_name = str(SESSION_DIR / f"_import_{uuid.uuid4().hex}")
    tmp_session = tmp_name + ".session"

    with open(tmp_session, "wb") as f:
        f.write(session_bytes)

    client = TelegramClient(tmp_name, api_id, api_hash, receive_updates=False)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"ok": False, "reason": "Сессия недействительна (not authorized)"}
        me = await client.get_me()

        # Terminate all other active sessions so no one else can use this account
        killed = 0
        try:
            auths = await client(GetAuthorizationsRequest())
            for auth in auths.authorizations:
                if not auth.current:
                    await client(ResetAuthorizationRequest(hash=auth.hash))
                    killed += 1
            logger.info(f"[import] Terminated {killed} other session(s) for {me.phone}")
        except Exception as e:
            logger.warning(f"[import] Could not terminate other sessions for {me.phone}: {e}")

        return {
            "ok":            True,
            "user_id":       me.id,
            "phone":         me.phone or str(me.id),
            "username":      me.username,
            "first_name":    me.first_name or "",
            "last_name":     me.last_name or "",
            "tmp_path":      tmp_session,
            "sessions_killed": killed,
        }
    except Exception as e:
        try:
            Path(tmp_session).unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "reason": str(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def resolve_channel_info(client: TelegramClient, channel_url: str) -> dict:
    try:
        entity = await client.get_entity(channel_url)
        return {
            "title": getattr(entity, "title", channel_url),
            "tg_id": str(entity.id),
        }
    except Exception:
        return {"title": None, "tg_id": None}
