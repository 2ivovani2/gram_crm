#!/bin/bash
# Development mode — backend with --reload, frontend with HMR, both in foreground.
# Press Ctrl-C to stop both.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Setup ─────────────────────────────────────────────────────────────────────
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r backend/requirements.txt

[ -d frontend/node_modules ] || { cd frontend && npm install && cd "$ROOT"; }

mkdir -p data/sessions data/logs

# ── Start ─────────────────────────────────────────────────────────────────────
echo ""
echo "Starting dev servers..."
echo "  Backend  →  http://localhost:8000  (auto-reload)"
echo "  Frontend →  http://localhost:3000  (HMR)"
echo "  API docs →  http://localhost:8000/docs"
echo ""

venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --reload --app-dir backend &
BACK=$!

cd frontend
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev &
FRONT=$!
cd "$ROOT"

trap "echo 'Stopping...'; kill $BACK $FRONT 2>/dev/null; wait" SIGINT SIGTERM
wait
