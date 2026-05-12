# Dev Log — Super Smart Spammer (AutoSending)

## Project Overview
Telegram multi-account automation system with web UI.

---

## 2026-05-11 — Project Kickoff

### Architecture Decisions
- **Backend**: Python 3.10+ with FastAPI (REST API) + Telethon (Telegram MTProto client)
- **Frontend**: Next.js 14 (App Router) + TailwindCSS + shadcn/ui components
- **Database**: SQLite via SQLAlchemy ORM (zero external deps, portable)
- **Auth Flow**: Phone → SMS code → (optional 2FA) → saved Telethon session file
- **Parallelism**: asyncio tasks per account, each running its own campaign loop
- **Sessions**: Telethon `.session` files stored in `data/sessions/`
- **Logging**: Python `logging` module → structured to `data/logs/app.log` + stdout

### Why Telethon over Pyrogram?
Telethon has better async support and more stable MTProto implementation for multi-account usage.

### Why FastAPI?
FastAPI gives us async-native API with auto-docs (/docs) and Pydantic validation out of the box.

### Why Next.js?
App Router + Server Components = fast UI, API routes co-located, easy deployment.

---

## Implementation Steps

### Step 1: Project Structure
```
autosending/
├── backend/
│   ├── main.py              # FastAPI app + all endpoints
│   ├── telegram_manager.py  # Telethon client management
│   ├── scheduler.py         # Campaign execution engine
│   ├── database.py          # SQLAlchemy models + CRUD
│   ├── config.py            # Env config + logging setup
│   └── requirements.txt
├── frontend/
│   ├── src/app/             # Next.js App Router pages
│   ├── src/components/      # UI components
│   ├── src/lib/             # API client, utils
│   └── ...
├── data/
│   ├── sessions/            # Telethon .session files
│   ├── logs/                # App logs
│   └── database.db          # SQLite DB
├── .env
├── README.md
├── start.sh                 # One-command start
└── dev_log.md
```

### Step 2: Database Schema
- **accounts**: api_id, api_hash, phone, username, first_name, last_name, bio, session_file, status
- **channels**: url, title, channel_id, is_active
- **message_templates**: content, category, use_count, is_active
- **campaigns**: name, min_delay, max_delay, comment_on_posts, status, total_sent
- **activity_logs**: account_id, campaign_id, channel_url, message, action_type, status, error_message

### Step 3: Auth Flow
1. POST /api/accounts — store phone + API credentials, status=pending
2. POST /api/accounts/{id}/send-code — Telethon sends SMS
3. POST /api/accounts/{id}/verify-code — verify, save session, status=online

### Step 4: Campaign Execution
- Each campaign: N accounts × M channels, sending random message from pool
- Each account runs in its own `asyncio.create_task()`
- Random delay [min_delay, max_delay] between rounds
- FloodWait, PeerFlood, BannedInChannel errors handled gracefully

### Step 5: Frontend Pages
- `/` — Dashboard (stats, live activity feed)
- `/accounts` — Multi-account manager with auth wizard
- `/channels` — Channel list manager
- `/messages` — Message template pool
- `/campaigns` — Create/start/stop campaigns
- `/help` — Built-in user guide

---

## 2026-05-12 — Human-like Behaviour & Anti-ban Hardening

### Files Changed
- `backend/scheduler.py`
- `backend/telegram_manager.py`

---

### 1. Normal-distribution delays (scheduler.py)

Replaced all `random.uniform()` timing calls with a new `_human_delay(low, high)` helper that uses a Gaussian distribution clamped to `[low, high]`. This means most delays cluster around the midpoint rather than being spread flat — exactly how a real person behaves.

```python
def _human_delay(low: float, high: float) -> float:
    mid = (low + high) / 2
    sigma = (high - low) / 6
    return max(low, min(high, random.gauss(mid, sigma)))
```

Applied to:
- Inter-channel pause: `_human_delay(5, 30)` (was `uniform(4, 10)`)
- Post-join pause: `_human_delay(5, 15)` (was `uniform(3, 6)`)
- Between-round delay: `_human_delay(min_delay, max_delay)` (was `uniform(...)`)

---

### 2. Typing simulation before every send (telegram_manager.py)

Before `client.send_message()` the bot now shows "typing…" for 1–3 seconds. This is the single most effective signal to Telegram that the client is human.

```python
try:
    async with client.action(entity, "typing"):
        await asyncio.sleep(random.uniform(1, 3))
except Exception:
    pass  # best-effort, never block the send
```

---

### 3. FloodWait with random jitter (scheduler.py)

Both join-FloodWait and send-FloodWait now add a random 5–30 second buffer on top of Telegram's required wait, so accounts don't all resume at the exact same moment:

```python
jitter = random.uniform(5, 30)
flood_cooldowns[url] = now + secs + jitter
```

---

### 4. PeerFlood with jitter (scheduler.py)

Account-level PeerFlood pause changed from a flat 10 minutes to `600 + uniform(0, 180)` seconds. Prevents all banned accounts from retrying simultaneously.

```python
pause = 600 + random.uniform(0, 180)
peer_flood_until = now + pause
```

---

### 5. Exponential backoff for generic errors (scheduler.py)

Added `_expo_backoff(retries)` helper and `error_retries: Dict[str, int]` per-worker state. On any non-classified error, the channel is put on an exponentially increasing cooldown (10s → 20s → 40s → … capped at 600s). Counter resets on success.

```python
def _expo_backoff(retries: int, base: float = 10.0, cap: float = 600.0) -> float:
    raw = base * math.pow(2, retries)
    jitter = random.uniform(0.8, 1.2)
    return min(cap, raw * jitter)

# On generic error:
retries = error_retries.get(url, 0)
backoff = _expo_backoff(retries)
error_retries[url] = retries + 1
flood_cooldowns[url] = now + backoff
```

---

### 6. Randomized account stagger (scheduler.py)

Startup offset changed from a fixed 15 s step to `i * uniform(10, 25)`. Each run the accounts spread differently, avoiding Telegram pattern detection.

```python
# Before: start_offset=i * 15
start_offset=i * random.uniform(10, 25),
```

---

## Status: ✅ IMPLEMENTATION COMPLETE

### Verified:
- [x] No hardcoded API keys (all via .env / DB)
- [x] Logging to file + console
- [x] asyncio parallel execution
- [x] Random message selection with rotation
- [x] FloodWait + PeerFlood error handling with jitter
- [x] Frontend dark theme, modern UI
- [x] Built-in Help page
- [x] Human-like typing simulation
- [x] Normal-distribution delays
- [x] Exponential backoff for transient errors
- [x] Randomized account startup stagger
