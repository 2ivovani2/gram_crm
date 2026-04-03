#!/usr/bin/env bash
# Wait until an HTTP endpoint returns 2xx, or exit 1 on timeout.
#
# Usage: bash scripts/wait_for_http.sh <url> [timeout_seconds]
#
# Example:
#   bash scripts/wait_for_http.sh http://localhost:8000/health/ 60

set -Eeuo pipefail

URL="${1:?Usage: wait_for_http.sh <url> [timeout_seconds]}"
MAX="${2:-60}"

for i in $(seq 1 "$MAX"); do
    if curl -sf --max-time 2 "$URL" -o /dev/null 2>/dev/null; then
        echo "    [OK] $URL responded (${i}s)"
        exit 0
    fi
    sleep 1
done

echo "    [ERR] $URL did not respond after ${MAX}s" >&2
exit 1
