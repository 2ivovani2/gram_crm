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
        await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
    except Exception as e:
        logger.warning(f"Alert failed → chat_id={chat_id}: {e}")

logger = logging.getLogger(__name__)

_running: Dict[int, asyncio.Task] = {}
_stop_events: Dict[int, asyncio.Event] = {}

# campaign_id → latest round summary, updated every round
# {"round": int, "sent": int, "cooldown": int, "skip_forever": int, "active": int, "ts": float}
_round_stats: Dict[int, dict] = {}

# campaign_id → {"sent": int, "joined": int} — reset every 30-min alert cycle
_campaign_counters: Dict[int, dict] = {}


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


def _get_caps(tg_user_id: int | None, messages_sent: int) -> tuple[int, int]:
    """
    Return (max_joins, max_sends) based on account age.
    Primary signal: Telegram user ID (sequential → lower = older).
    Fallback: messages_sent counter when tg_user_id is not yet known.
    """
    if tg_user_id:
        # Telegram IDs are sequential. All accounts are from 2023+.
        # < 6B    → early 2023   (Ветеран)
        # 6B–7B   → late 2023 / early 2024 (Опытный)
        # 7B–7.5B → 2024         (Растущий)
        # > 7.5B  → 2025+        (Новичок)
        if tg_user_id < 6_000_000_000:
            return 14, 55
        elif tg_user_id < 7_000_000_000:
            return 12, 45
        elif tg_user_id < 7_500_000_000:
            return 10, 35
        else:
            return 8, 25
    # No tg_user_id yet — fall back to internal sent counter
    if messages_sent >= 5000:
        return 14, 55
    elif messages_sent >= 2000:
        return 12, 45
    elif messages_sent >= 500:
        return 10, 35
    else:
        return 8, 25


# ── Message rotation ───────────────────────────────────────────────────────────

def _pick_message(messages: list, used: list) -> str:
    if len(used) >= len(messages):
        used.clear()
    available = [i for i in range(len(messages)) if i not in used]
    idx = random.choice(available)
    used.append(idx)
    return messages[idx]["content"]


# ── 30-min progress alert loop ────────────────────────────────────────────────

async def _progress_alert_loop(
    campaign_id: int,
    camp_name: str,
    user_tg_id: int | None,
    stop: asyncio.Event,
):
    """Fire every 30 min with sent/joined totals since the last alert."""
    while not stop.is_set():
        await _sleep(30 * 60, stop)
        if stop.is_set():
            break
        c = _campaign_counters.get(campaign_id, {})
        sent  = c.get("sent",   0)
        joined = c.get("joined", 0)
        c["sent"]   = 0
        c["joined"] = 0
        if sent == 0 and joined == 0:
            msg = (
                f"😶 <b>«{camp_name}»</b> — итог за 30 мин\n"
                f"Активности не было — аккаунты отдыхают или в FloodWait."
            )
        else:
            msg = (
                f"📊 <b>«{camp_name}»</b> — итог за 30 мин\n"
                f"💬 Отправлено: <b>{sent}</b> сообщений\n"
                f"📥 Вступлений: <b>{joined}</b>"
            )
        asyncio.create_task(_alert(msg, user_tg_id))


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
        acc_messages_sent = account.messages_sent or 0
        acc_tg_user_id    = account.tg_user_id

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

    # Load persisted memberships so we skip re-joining channels the account is already in
    await tm.preload_memberships(account_id)

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

    # Session-health counters — reset each rest period
    flood_hits: int = 0   # FloodWaits received in current work session
    joins_done: int = 0   # new channel joins in current work session
    sends_done: int = 0   # messages sent in current work session
    peer_flood_count: int = 0  # total PeerFlood events (escalates rest time)

    # Per-work-session safety caps — adaptive based on account age (Telegram user ID)
    _MAX_JOINS, _MAX_SENDS = _get_caps(acc_tg_user_id, acc_messages_sent)
    _FLOOD_WARN = 2    # FloodWaits before slowing down + alert
    _FLOOD_REST = 4    # FloodWaits before emergency rest
    logger.info(
        f"[Cmp{campaign_id}][{phone}] caps — joins={_MAX_JOINS} sends={_MAX_SENDS} "
        f"(tg_id={acc_tg_user_id}, history={acc_messages_sent})"
    )

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
                flood_hits = 0; joins_done = 0; sends_done = 0
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
                    # Don't join new channels if we've hit the per-session cap
                    if joins_done >= _MAX_JOINS:
                        logger.debug(f"[Cmp{campaign_id}][{phone}] join cap ({_MAX_JOINS}), skipping {url}")
                        skipped_cooldown += 1
                        continue

                    join_res = await tm.ensure_joined(client, account_id, url)

                    if join_res["ok"]:
                        _log(account_id, campaign_id, url, None, "join_channel", "success")
                        _campaign_counters.setdefault(campaign_id, {"sent": 0, "joined": 0})["joined"] += 1
                        joins_done += 1
                    elif join_res["reason"] == "disconnected":
                        logger.warning(f"[Cmp{campaign_id}][{phone}] disconnected — reconnecting")
                        break  # break channel loop → reconnect at top of round loop
                    elif join_res["reason"] == "flood_wait":
                        secs = join_res["seconds"]
                        flood_hits += 1
                        jitter = random.uniform(secs * 0.15, secs * 0.35)
                        flood_cooldowns[url] = now + secs + jitter
                        _log(account_id, campaign_id, url, None, "join_channel",
                             "flood_wait", f"FloodWait {secs}s — retry in {secs + jitter:.0f}s")
                        if flood_hits >= _FLOOD_REST:
                            _rest = random.uniform(2 * 3600, 4 * 3600)
                            logger.warning(f"[Cmp{campaign_id}][{phone}] {flood_hits} floods → emergency rest {_rest/3600:.1f}h")
                            asyncio.create_task(_alert(
                                f"🆘 <b>Экстренный отдых</b> — <code>{phone}</code>\n"
                                f"Кампания #{campaign_id}: {flood_hits} FloodWait подряд.\n"
                                f"Пауза {_rest/3600:.1f}ч — защита сессии.",
                                user_tg_id,
                            ))
                            await _sleep(_rest, stop)
                            flood_hits = 0; joins_done = 0; sends_done = 0
                            _work_session_end = time.monotonic() + random.uniform(40 * 60, 80 * 60)
                            break
                        elif flood_hits == _FLOOD_WARN:
                            asyncio.create_task(_alert(
                                f"⚠️ <b>Много FloodWait</b> — <code>{phone}</code>\n"
                                f"Кампания #{campaign_id}: уже {flood_hits} подряд. "
                                f"Увеличиваю паузы между каналами.",
                                user_tg_id,
                            ))
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
                        continue

                    # Post-join pause — longer than inter-send, and scales up if floods accumulate
                    _join_pause = _human_delay(120, 300) if flood_hits >= _FLOOD_WARN else _human_delay(60, 150)
                    await _sleep(_join_pause, stop)
                    if stop.is_set():
                        break

                # ── Send ───────────────────────────────────────────────────
                if not messages:
                    break

                msg_text = _pick_message(messages, used_msg_indices)
                send_res = await tm.send_message_to(client, account_id, url, msg_text, comment_mode)

                if send_res["ok"]:
                    sent_this_round += 1
                    sends_done += 1
                    error_retries.pop(url, None)  # reset backoff on success
                    _log(account_id, campaign_id, url, msg_text, "send_message", "success")
                    _increment_sent(account_id, campaign_id)
                    _campaign_counters.setdefault(campaign_id, {"sent": 0, "joined": 0})["sent"] += 1
                    # Early rest when send cap reached mid-round
                    if sends_done >= _MAX_SENDS:
                        logger.info(f"[Cmp{campaign_id}][{phone}] send cap {_MAX_SENDS} — breaking round")
                        break

                elif send_res["reason"] == "disconnected":
                    logger.warning(f"[Cmp{campaign_id}][{phone}] disconnected — reconnecting")
                    break  # break channel loop → reconnect at top of round loop

                elif send_res["reason"] == "flood_wait":
                    secs = send_res["seconds"]
                    flood_hits += 1
                    jitter = random.uniform(secs * 0.15, secs * 0.35)
                    flood_cooldowns[url] = now + secs + jitter
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "flood_wait", f"FloodWait {secs}s — cooldown {secs + jitter:.0f}s")
                    if flood_hits >= _FLOOD_REST:
                        _rest = random.uniform(2 * 3600, 4 * 3600)
                        logger.warning(f"[Cmp{campaign_id}][{phone}] {flood_hits} floods → emergency rest {_rest/3600:.1f}h")
                        asyncio.create_task(_alert(
                            f"🆘 <b>Экстренный отдых</b> — <code>{phone}</code>\n"
                            f"Кампания #{campaign_id}: {flood_hits} FloodWait подряд.\n"
                            f"Пауза {_rest/3600:.1f}ч — защита сессии.",
                            user_tg_id,
                        ))
                        await _sleep(_rest, stop)
                        flood_hits = 0; joins_done = 0; sends_done = 0
                        _work_session_end = time.monotonic() + random.uniform(40 * 60, 80 * 60)
                        break
                    elif flood_hits == _FLOOD_WARN:
                        asyncio.create_task(_alert(
                            f"⚠️ <b>Много FloodWait</b> — <code>{phone}</code>\n"
                            f"Кампания #{campaign_id}: уже {flood_hits} подряд. "
                            f"Увеличиваю паузы между каналами.",
                            user_tg_id,
                        ))

                elif send_res["reason"] == "peer_flood":
                    # Escalating rest: 1st → 15-25 min, 2nd → 40-80 min, 3rd+ → 2-4 h
                    peer_flood_count += 1
                    if peer_flood_count == 1:
                        pause = random.uniform(900, 1500)
                    elif peer_flood_count == 2:
                        pause = random.uniform(2400, 4800)
                    else:
                        pause = random.uniform(7200, 14400)
                    peer_flood_until = now + pause
                    _log(account_id, campaign_id, url, msg_text, "send_message",
                         "flood_wait", f"PeerFlood #{peer_flood_count} — worker paused {pause:.0f}s")
                    asyncio.create_task(_alert(
                        f"🚫 <b>PeerFlood #{peer_flood_count}</b> — аккаунт <code>{phone}</code>\n"
                        f"Кампания #{campaign_id}. Пауза {pause/60:.0f} мин"
                        + (" ⚠️ аккаунт под угрозой, рекомендую снизить скорость" if peer_flood_count >= 2 else ""),
                        user_tg_id,
                    ))
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

            # ── Early rest if send cap hit ─────────────────────────────────
            if sends_done >= _MAX_SENDS:
                _rest = random.uniform(30 * 60, 70 * 60)
                logger.info(f"[Cmp{campaign_id}][{phone}] send cap {sends_done} → rest {_rest/60:.0f} min")
                asyncio.create_task(_alert(
                    f"⏸️ <b>Лимит сессии</b> — <code>{phone}</code>\n"
                    f"Кампания #{campaign_id}: отправлено {sends_done} за период. "
                    f"Отдых {_rest/60:.0f} мин.",
                    user_tg_id,
                ))
                await _sleep(_rest, stop)
                sends_done = 0; joins_done = 0; flood_hits = 0
                _work_session_end = time.monotonic() + random.uniform(40 * 60, 80 * 60)

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
            # Merge skip_forever URLs across all workers for this campaign
            merged_skipped = set(prev.get("skip_forever_urls", [])) | skip_forever
            _round_stats[campaign_id] = {
                "round":             max(round_num, prev.get("round", 0)),
                "sent":              prev.get("sent", 0) + sent_this_round,
                "cooldown":          on_cooldown,
                "skip_forever":      len(merged_skipped),
                "skip_forever_urls": list(merged_skipped),
                "active":            active_channels,
                "ts":                time.time(),
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

    _campaign_counters.setdefault(campaign_id, {"sent": 0, "joined": 0})

    asyncio.create_task(_alert(
        f"🚀 <b>«{camp_name}» запущена</b>\n"
        f"Аккаунтов: {len(accounts_snap)} · Каналов: {len(channels_snap)} · Шаблонов: {len(messages_snap)}",
        user_tg_id,
    ))

    progress_task = asyncio.create_task(
        _progress_alert_loop(campaign_id, camp_name, user_tg_id, stop)
    )

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

        progress_task.cancel()
        _campaign_counters.pop(campaign_id, None)
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
