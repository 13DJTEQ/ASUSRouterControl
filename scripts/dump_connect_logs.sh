#!/usr/bin/env bash
# Compatibility wrapper — prefer scripts/pull_connect_logs.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "${ROOT}/scripts/pull_connect_logs.sh" "$@"
