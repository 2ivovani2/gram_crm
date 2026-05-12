# AutoSending — Telegram Automation Platform

A full-stack web application for managing multiple Telegram accounts and running parallel message campaigns.

## Features

- **Multi-account management** — Add unlimited Telegram accounts via API ID/Hash + phone auth
- **Profile editor** — Set name and bio for each account directly from the dashboard
- **Channel manager** — Add/remove/pause target channels and groups
- **Message pool** — Create a bank of message templates with smart rotation (no repeat spam)
- **Parallel campaigns** — Each account runs as an independent asyncio task simultaneously
- **Comment mode** — Reply to the latest post instead of sending to the chat
- **Live dashboard** — Real-time stats, activity feed, and charts
- **Structured logging** — All activity logged to files and console
- **Built-in help** — Full documentation accessible from the sidebar

## Tech Stack

| Layer    | Technology |
|----------|-----------|
| Backend  | Python 3.10+, FastAPI, Uvicorn |
| Telegram | Telethon (MTProto) |
| Database | SQLite via SQLAlchemy ORM |
| Frontend | Next.js 14 (App Router), React 18 |
| Styling  | TailwindCSS, Lucide icons, Recharts |

## Quick Start

### Option A — Dev mode (with hot reload)

```bash
./dev.sh
```

### Option B — Production (background processes)

```bash
./start.sh
```

Open **http://localhost:3000** in your browser.

To stop: `./stop.sh`

### Manual setup

```bash
# Backend
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir backend

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

## Project Structure

```
autosending/
├── backend/
│   ├── main.py              # FastAPI app + all REST endpoints
│   ├── telegram_manager.py  # Telethon client pool + auth
│   ├── scheduler.py         # Async campaign execution engine
│   ├── database.py          # SQLAlchemy models
│   ├── config.py            # Logging setup
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── page.jsx          # Dashboard
│       ├── accounts/         # Account manager + auth wizard
│       ├── channels/         # Channel list
│       ├── messages/         # Message template pool
│       ├── campaigns/        # Campaign creator + runner
│       └── help/             # Built-in documentation
├── data/
│   ├── sessions/            # Telethon .session files
│   ├── logs/                # Rotating log files
│   └── database.db          # SQLite database (auto-created)
├── .env                     # Environment configuration
├── start.sh                 # Production startup
├── stop.sh                  # Stop all services
├── dev.sh                   # Development mode
└── dev_log.md               # Development history
```

## Workflow

1. **Add Account** → enter API ID + Hash + phone → receive SMS code → verify
2. **Add Channels** → paste @username or t.me/link
3. **Add Messages** → write 3–5+ template variations
4. **Create Campaign** → select accounts, channels, messages → configure delays
5. **Start** → all accounts run in parallel, sending random messages with smart rotation

## Anti-Ban Notes

- Use delays of 30–120 seconds between rounds
- Accounts should be at least 1–2 months old
- FloodWait errors are handled automatically
- See the Help page for full anti-ban guide

## API Documentation

FastAPI auto-generates interactive docs at **http://localhost:8000/docs**

## License

MIT — Use responsibly and in accordance with Telegram's Terms of Service.
