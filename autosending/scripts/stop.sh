#!/bin/bash
# Stop all AutoSending background processes.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Stopping AutoSending..."

for f in backend frontend; do
  pid_file="$ROOT/data/${f}.pid"
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    kill "$pid" 2>/dev/null && echo "  stopped $f (pid $pid)" || echo "  $f already stopped"
    rm -f "$pid_file"
  fi
done

# Fallback: kill by port
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true

echo "Done."
