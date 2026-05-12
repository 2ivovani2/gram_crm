#!/bin/bash
# Production start — builds both services and runs them in the background.
# Logs go to data/logs/. Use scripts/stop.sh to shut down.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Deps ──────────────────────────────────────────────────────────────────────
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -r backend/requirements.txt

[ -d frontend/node_modules ] || { cd frontend && npm install && cd "$ROOT"; }

mkdir -p data/sessions data/logs

# ── Kill any stale processes ───────────────────────────────────────────────────
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true
sleep 1

# ── Backend ───────────────────────────────────────────────────────────────────
nohup venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 1 --log-level info \
  --app-dir backend \
  > data/logs/backend.log 2>&1 &
echo $! > data/backend.pid

# ── Frontend ──────────────────────────────────────────────────────────────────
(
  cd "$ROOT/frontend"
  npm run build > "$ROOT/data/logs/frontend-build.log" 2>&1
  nohup npm run start >> "$ROOT/data/logs/frontend.log" 2>&1 &
  echo $! > "$ROOT/data/frontend.pid"
) &
BUILD_PID=$!

echo ""
echo "Building frontend… (this takes ~30s)"
wait $BUILD_PID

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AutoSending is running"
echo ""
echo "  Dashboard : http://localhost:3000"
echo "  API docs  : http://localhost:8000/docs"
echo ""
echo "  Logs: tail -f data/logs/backend.log"
echo "  Stop: scripts/stop.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
