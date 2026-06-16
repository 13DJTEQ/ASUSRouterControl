#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "WARNING: scripts/verify_test_app.sh is deprecated and now delegates to scripts/verify_dev_app.sh." >&2

if [[ -n "${VERIFY_TEST_APP_TIMEOUT_SECONDS:-}" && -z "${VERIFY_DEV_APP_TIMEOUT_SECONDS:-}" ]]; then
  export VERIFY_DEV_APP_TIMEOUT_SECONDS="${VERIFY_TEST_APP_TIMEOUT_SECONDS}"
fi
if [[ -n "${VERIFY_TEST_APP_FRESHNESS_SECONDS:-}" && -z "${VERIFY_DEV_APP_FRESHNESS_SECONDS:-}" ]]; then
  export VERIFY_DEV_APP_FRESHNESS_SECONDS="${VERIFY_TEST_APP_FRESHNESS_SECONDS}"
fi
if [[ -n "${VERIFY_TEST_APP_LAUNCH_TIMEOUT_SECONDS:-}" && -z "${VERIFY_DEV_APP_LAUNCH_TIMEOUT_SECONDS:-}" ]]; then
  export VERIFY_DEV_APP_LAUNCH_TIMEOUT_SECONDS="${VERIFY_TEST_APP_LAUNCH_TIMEOUT_SECONDS}"
fi
if [[ -n "${VERIFY_TEST_APP_DB_PATH:-}" && -z "${VERIFY_DEV_APP_DB_PATH:-}" ]]; then
  export VERIFY_DEV_APP_DB_PATH="${VERIFY_TEST_APP_DB_PATH}"
fi
if [[ -n "${VERIFY_TEST_APP_LOG_PATH:-}" && -z "${VERIFY_DEV_APP_LOG_PATH:-}" ]]; then
  export VERIFY_DEV_APP_LOG_PATH="${VERIFY_TEST_APP_LOG_PATH}"
fi

exec bash "${SCRIPT_DIR}/verify_dev_app.sh"