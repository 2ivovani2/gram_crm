"""
Campaign execution engine.

Architecture:
- One asyncio Task per account per campaign
- No upfront "join all" phase — join on demand, send immediately
- FloodWait → per-channel cooldown timer, NOT a skip/drop
- Membership cache in telegram_manager persists across campaign runs
- Account-level PeerFlood → pause entire worker, retry after 10 min
"""

import asyncio
import json
import logging
import math
import os
import random
import time
import urllib.request
from datetime import datetime
from typing import Dict, Set

from database import Account, ActivityLog, Campaign, SessionLocal
import telegram_manager as tm

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def _alert(text: str, chat_id: int | None):
    """Send a Telegram message to the user who owns the campaign. Fire-and-forget."""
    if not _BOT_TOKEN or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        await asyncio.to_thread(urllib.request.urlopen, req, 5)
    except Exception as e:
        logger.warning(f"Alert failed → chat_id={chat_id}: {e}")

logger = logging.getLogger(__name__)

_running: Dict[int, asyncio.Task] = {}
_stop_events: Dict[int, asyncio.Event] = {}

# campaign_id → latest round summary, updated every round
# {"round": int, "sent": int, "cooldown": int, "skip_forever": int, "active": int, "ts": float}
_round_stats: Dict[int, dict] = {}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _log(account_id, campaign_id, channel_url, message, action_type, status, error=None):
    db = SessionLocal()
    try:
        # Resolve user_id from account so the log is visible in the API
        acc = db.query(Account).filter(Account.id == account_id).first()
        user_id = acc.user_id if acc else None

        db.add(ActivityLog(
            user_id=user_id,
            account_id=account_id,
            campaign_id=campaign_id,
            channel_url=channel_url,
            message=(message or "")[:200],
            action_type=action_type,
            status=status,
            error_message=(error or "")[:400],
        ))
        db.commit()
    finally:
        db.close()


def _increment_sent(account_id, campaign_id):
    db = SessionLocal()
    try:
        acc  = db.query(Account).filter(Account.id == account_id).first()
        camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if acc:
            acc.messages_sent += 1
            acc.last_used = datetime.utcnow()
        if camp:
            camp.total_sent += 1
        db.commit()
    finally:
        db.close()


# ── Sleep & timing helpers ─────────────────────────────────────────────────────

async def _sleep(seconds: float, stop: asyncio.Event):
    """Interruptible sleep — checks stop every 0.5 s."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not stop.is_set():
        await asyncio.sleep(min(0.5, end - time.monotonic()))


def _human_delay(low: float, high: float) -> float:
    """Normally distributed delay clamped to [low, high]. More natural than uniform."""
    mid = (low + high) / 2
    sigma = (high - low) / 6
    return max(low, min(high, random.gauss(mid, sigma)))


def _expo_backoff(retries: int, base: float = 10.0, cap: float = 600.0) -> float:
    """Exponential backoff with jitter: base * 2^retries ± 20%, capped."""
    raw = base * math.pow(2, retries)
    jitter = random.uniform(0.8, 1.2)
    return min(cap, raw * jitter)


# ── Message rotation ───────────────────────────────────────────────────────────

def _pick_message(messages: list, used: list) -> str:
    if len(used) >= len(messages):
        used.clear()
    available = [i for i in range(len(messages)) if i not in used]
    idx = random.choice(available)
    used.append(idx)
    return messages[idx]["content"]


# ── Per-account worker ─────────────────────────────────────────────────────────

async def _account_worker(
    account_id: int,
    channels: list,
    messages: list,
    campaign_id: int,
    min_delay: float,
    max_delay: float,
    comment_mode: bool,
    stop: asyncio.Event,
    start_offset: float,
    user_tg_id: int | None = None,
):
    # Stagger accounts so they don't all hit Telegram at the same moment
    if start_offset > 0:
        logger.info(f"[Cmp{campaign_id}][Acc{account_id}] waiting {start_offset:.0f}s before start")
        await _sleep(start_offset, stop)
    if stop.is_set():
        return

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return
        phone = account.phone
        # Keep a plain snapshot for reconnection after db.close()
        acc_snap = type("A", (), {
            "id": account.id, "phone": account.phone,
            "api_id": account.api_id, "api_hash": account.api_hash,
        })()

        client = await tm.get_client(account)
        if not client:
            logger.error(f"[Cmp{campaign_id}][{phone}] not authorized — skipping")
            account.status = "error"
            db.commit()
            await _alert(
                f"❌ <b>Сессия недействительна</b> — <code>{phone}</code>\n"
                f"Кампания #{campaign_id}: аккаунт не авторизован. "
                f"Удали и добавь заново через «Добавить аккаунт».",
                user_tg_id,
            )
            return

        account.status = "working"
        db.commit()
        logger.info(f"[Cmp{campaign_id}][{phone}] worker started — {len(channels)} channels, {len(messages)} templates")

    finally:
        db.close()

    # Per-worker state
    # channel_url → monotonic timestamp after which it's safe to retry
    flood_cooldowns: Dict[str, float] = {}
    # permanently skip: expired / approval-required / broadcast-only / banned
    skip_forever: Set[str] = set()
    # channel_url → consecutive generic error count (for exponential backoff)
    error_retries: Dict[str, int] = {}

    used_msg_indices: list = []
    peer_flood_until: float = 0.0  # account-level flood, entire worker pauses
    zero_send_rounds: int = 0      # consecutive rounds with 0 sent
    reconnect_attempts: int = 0    # total reconnects this session

    # Work/rest rotation — randomized per worker so accounts don't all rest simultaneously
    _work_session_end = time.monotonic() + random.uniform(40 * 60, 80 * 60)
    rest_num = 0

    round_num = 0

    try:
        while not stop.is_set():
            round_num += 1
            now = time.monotonic()

            # ── Work/rest rotation ─────────────────────────────────────────
            if now >= _work_session_end:
                rest_secs = random.uniform(25 * 60, 50 * 60)
                rest_num += 1
                logger.info(f"[Cmp{campaign_id}][{phone}] Rest #{rest_num} — sleeping {rest_secs/60:.0f} min")
                await _alert(
                    f"😴 <b>Аккаунт отдыхает</b> — <code>{phone}</code>\n"
                    f"Кампания #{campaign_id}. Пауза {rest_secs/60:.0f} мин, затем продолжит.",
                    user_tg_id,
                )
                await _sleep(rest_secs, stop)
                if stop.is_set():
                    break
                _work_session_end = time.monotonic() + random.uniform(40 * 60, 80 * 60)
                logger.info(f"[Cmp{campaign_id}][{phone}] Resuming after rest #{rest_num}")

            # Account-level PeerFlood cooldown
            if peer_flood_until > now:
                wait = peer_flood_until - now
                logger.warning(f"[Cmp{campaign_id}][{phone}] PeerFlood cooldown — sleeping {wait:.0f}s")
                await _sleep(wait, stop)
                if stop.is_set():
                    break

            sent_this_round = 0
            skipped_cooldown = 0
            skipped_forever = 0

            for ch in channels:
                if stop.is_set():
                    break

                url = ch["url"]
                now = time.monotonic()

                if url in skip_forever:
                    skipped_forever += 1
                    continue

                if flood_cooldowns.get(url, 0) > now:
                    remaining = flood_cooldowns[url] - now
                    skipped_cooldown += 1
                    logger.debug(f"[Cmp{campaign_id}][{phone}] ⏳ {url} — cooldown {remaining:.0f}s")
                    continue

                # ── Ensure joined ──────────────────────────────────────────
                if not tm.is_cached_member(account_id, url):
                    join_res = await tm.ensure_joined(client, account_id, url)

                    if join_res["ok"]:
                        _log(account_id, campaign_id, url, None, "join_channel", "success")
                    elif join_res["reason"] == "disconnected":
                        logger.warning(f"[Cmp{campaign_id}][{phone}] disconnected — reconnecting")
                        break  # break channel loop → reconnect at top of round loop
                    elif join_res["reason"] == "flood_wait":
                        secs = join_res["seconds"]
                        jitter = random.uniform(5, 30)
                        flood_cooldowns[url] = now + secs + jitter
                        _log(account_id, campaign_id, url, None, "join_channel",
                             "flood_wait", f"FloodWait {secs}s — retry in {secs + jitter:.0f}s")
                        skipped_cooldown += 1
                        continue
                    elif join_res["reason"] in ("expired", "approval_required", "private", "not_channel"):
                        skip_forever.add(url)
                        _log(account_id, campaign_id, url, None, "join_channel",
                             "error", join_res["reason"])
                        skipped_forever += 1
                        continue
                    else:
                        _log(account_id, campaign_id, url, None, "join_channel",
                             "error", join_res.get("detail", join_res["reason"]))
                        # Don't permanently skip on generic errors — retry next round
                        continue

                    # Human-like pause after joining a new channel
                    await _sleep(_human_delay(5, 15), stop)
                    if stop.is_set():
                        break

                # ── Send ───────────────────────────────────────────────────
                if not messages:
                    break

                msg_text = _pick_message(messages, used_msg_indices)
                send_res = await tm.send_message_to(client, account_id, url, msg_text, comment_mode)

                if send_res["ok"]:
                    sent_this_round += 1
                    error_retries.pop(url, None)  # reset backoff on success
                    _log(account_id, campaign_id, url, msg_text, "send_message", "success")
                    _increment_sent(account_id, campaign_id)

                elif send_res["reason"] == "disconnected":
                    logger.warning(f"[Cmp{campaign_id}][{phone}] disconnected — reconnecting")
                    break  # break channel loop → reconnect at top of round loop

                elif send_res["reason"] == "flood_wait":
                    secs = send_res["seconds"]
                    jitter = random.uniform(5, 30)
                    flood_cooldowns[url] = now + secs + jitter
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "flood_wait", f"FloodWait {secs}s — cooldown {secs + jitter:.0f}s")

                elif send_res["reason"] == "peer_flood":
                    # Account-level flood — pause with jitter so accounts don't all wake together
                    pause = 600 + random.uniform(0, 180)
                    peer_flood_until = now + pause
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "flood_wait", f"PeerFlood — worker paused {pause:.0f}s")
                    await _alert(
                        f"🚫 <b>PeerFlood</b> — аккаунт <code>{phone}</code>\n"
                        f"Кампания #{campaign_id}. Пауза {pause:.0f}с (~{pause/60:.0f} мин)",
                        user_tg_id,
                    )
                    break  # exit channel loop, sleep handled at top

                elif send_res["reason"] == "banned":
                    skip_forever.add(url)
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "error", "banned in channel")

                elif send_res["reason"] == "forbidden":
                    skip_forever.add(url)
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "error", "write forbidden (broadcast channel)")

                elif send_res["reason"] == "not_member":
                    # Evicted or session mismatch — will re-join next iteration
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "error", "not a member — will rejoin")

                else:
                    # Exponential backoff for generic/transient errors
                    retries = error_retries.get(url, 0)
                    backoff = _expo_backoff(retries)
                    error_retries[url] = retries + 1
                    flood_cooldowns[url] = now + backoff
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "error", f"{send_res.get('detail', send_res['reason'])} — backoff {backoff:.0f}s (retry #{retries + 1})")

                # Human-like inter-channel pause (normally distributed 5–30s)
                await _sleep(_human_delay(5, 30), stop)

            # ── Reconnect if client dropped ────────────────────────────────
            if not client.is_connected():
                reconnect_attempts += 1
                logger.warning(f"[Cmp{campaign_id}][{phone}] client disconnected, reconnecting (#{reconnect_attempts})")
                await _alert(
                    f"🔌 <b>Клиент отключился</b> — <code>{phone}</code>\n"
                    f"Кампания #{campaign_id}, раунд {round_num}. Переподключаю…",
                    user_tg_id,
                )
                new_client = await tm.reconnect_client(acc_snap)
                if new_client:
                    client = new_client
                    logger.info(f"[Cmp{campaign_id}][{phone}] reconnected OK (#{reconnect_attempts})")
                    await _alert(
                        f"✅ <b>Переподключился</b> — <code>{phone}</code>. Продолжаю рассылку.",
                        user_tg_id,
                    )
                else:
                    logger.error(f"[Cmp{campaign_id}][{phone}] reconnect failed — stopping worker")
                    await _alert(
                        f"❌ <b>Не удалось переподключиться</b> — <code>{phone}</code>\n"
                        f"Кампания #{campaign_id} остановлена. Проверь сессию аккаунта.",
                        user_tg_id,
                    )
                    return

            active_channels = len(channels) - len(skip_forever)
            on_cooldown = sum(1 for cd in flood_cooldowns.values() if cd > time.monotonic())
            logger.info(
                f"[Cmp{campaign_id}][{phone}] Round {round_num}: "
                f"sent={sent_this_round} cooldown={on_cooldown} "
                f"skip_forever={skipped_forever} active={active_channels}"
            )
            prev = _round_stats.get(campaign_id, {})
            _round_stats[campaign_id] = {
                "round":        max(round_num, prev.get("round", 0)),
                "sent":         prev.get("sent", 0) + sent_this_round,
                "cooldown":     on_cooldown,
                "skip_forever": len(skip_forever),
                "active":       active_channels,
                "ts":           time.time(),
            }

            # Alert if 0 sends for too many rounds in a row (not counting cooldown-only rounds)
            if sent_this_round == 0 and on_cooldown < len(channels) * 0.9:
                zero_send_rounds += 1
                if zero_send_rounds == 5:
                    await _alert(
                        f"⚠️ <b>Низкая эффективность</b> — <code>{phone}</code>\n"
                        f"Кампания #{campaign_id}: 5 раундов без отправок.\n"
                        f"Активных: {active_channels}, FloodWait: {on_cooldown}, Пропущено: {len(skip_forever)}",
                        user_tg_id,
                    )
            else:
                zero_send_rounds = 0

            if stop.is_set():
                break

            # Between-round delay — normally distributed for human-like variance
            delay = _human_delay(min_delay, max_delay)
            logger.info(f"[Cmp{campaign_id}][{phone}] sleeping {delay:.0f}s before next round")
            await _sleep(delay, stop)

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(f"[Cmp{campaign_id}][{phone}] fatal worker error")
    finally:
        db2 = SessionLocal()
        try:
            acc = db2.query(Account).filter(Account.id == account_id).first()
            if acc and acc.status == "working":
                acc.status = "online"
            db2.commit()
        finally:
            db2.close()
        logger.info(f"[Cmp{campaign_id}][{phone}] worker stopped")


# ── Campaign runner ────────────────────────────────────────────────────────────

async def _run_campaign(campaign_id: int):
    db = SessionLocal()
    try:
        camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not camp:
            return

        camp.status = "running"
        camp.started_at = datetime.utcnow()
        db.commit()

        accounts_snap = [{"id": a.id} for a in camp.accounts if a.is_active]
        channels_snap = [{"url": c.url}  for c in camp.channels  if c.is_active]
        messages_snap = [{"content": m.content} for m in camp.messages if m.is_active]
        min_d, max_d = camp.min_delay, camp.max_delay
        comment = camp.comment_on_posts
        # User's Telegram ID — used to send alerts directly to the campaign owner
        user_tg_id = camp.user.telegram_id if camp.user else None
        camp_name  = camp.name
    finally:
        db.close()

    stop = _stop_events.setdefault(campaign_id, asyncio.Event())

    # Stagger accounts with random offsets so they don't hit Telegram simultaneously
    tasks = [
        asyncio.create_task(
            _account_worker(
                a["id"], channels_snap, messages_snap,
                campaign_id, min_d, max_d, comment, stop,
                start_offset=i * random.uniform(10, 25),
                user_tg_id=user_tg_id,
            )
        )
        for i, a in enumerate(accounts_snap)
    ]

    try:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Task was hard-cancelled — cancel workers and wait for them to clean up
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        # Always runs: after graceful stop, hard cancel, or exception
        db2 = SessionLocal()
        try:
            camp = db2.query(Campaign).filter(Campaign.id == campaign_id).first()
            if camp and camp.status == "running":
                camp.status = "stopped"
                camp.stopped_at = datetime.utcnow()
            db2.commit()
        finally:
            db2.close()

        _running.pop(campaign_id, None)
        _stop_events.pop(campaign_id, None)
        stats = _round_stats.pop(campaign_id, {})
        total = stats.get("sent", 0)
        logger.info(f"Campaign {campaign_id} finished — total sent: {total}")
        asyncio.create_task(_alert(
            f"🏁 <b>«{camp_name}» завершена</b>\n"
            f"Отправлено: <b>{total}</b> сообщений\n"
            f"Пропущено навсегда: {stats.get('skip_forever', '?')}",
            user_tg_id,
        ))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_campaign(campaign_id: int) -> bool:
    if campaign_id in _running:
        return False
    stop = asyncio.Event()
    _stop_events[campaign_id] = stop
    task = asyncio.create_task(_run_campaign(campaign_id))
    _running[campaign_id] = task
    logger.info(f"Campaign {campaign_id} started")
    return True


def stop_campaign(campaign_id: int) -> bool:
    found = False
    if campaign_id in _stop_events:
        _stop_events[campaign_id].set()
        found = True
    if campaign_id in _running:
        _running[campaign_id].cancel()
        found = True
    return found


def is_running(campaign_id: int) -> bool:
    task = _running.get(campaign_id)
    return task is not None and not task.done()


def get_round_stats(campaign_id: int) -> dict:
    return _round_stats.get(campaign_id, {})
