#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"

# Kept as a compatibility entrypoint for the documented bootstrap flow. Zone
# and record reconciliation is intentionally one atomic, create-first process.
exec "${root_dir}/infra/kubernetes/apps/vpn/ensure-private-app-records.sh"
