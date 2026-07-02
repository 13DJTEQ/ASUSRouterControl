#!/usr/bin/env bash
#
# factory_release_lock.sh — Release a factory lock on an issue.
#
# Usage:
#   factory_release_lock.sh <issue_number> [--run-id <id>]
#
# Behavior:
#   - If --run-id matches the active lock held by this run, post a release
#     comment so auditors see the lifecycle.
#   - If the active lock is held by a different run, do NOT release
#     (refuse, return 1).
#   - If no active lock exists, exit 0 (idempotent).
#
# Always exits 0 on infrastructure failure so the parent pipeline
# does not abort on lock cleanup glitches; the lock will expire
# naturally within FACTORY_LOCK_TTL_MINUTES.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ISSUE_NUMBER="${1:-}"
TARGET_RUN_ID="${FACTORY_LOCK_RUN_ID:-}"

if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "factory_refused:missing_issue_number" >&2
  exit 0  # cleanup path; do NOT abort parent
fi

EXISTING_LOCKS=$(gh issue view "${ISSUE_NUMBER}" --json comments \
  -q '.comments[] | select(.body | test("factory_lock:")) | .body' 2>/dev/null || true)

if [[ -z "$EXISTING_LOCKS" ]]; then
  echo "no_lock_present"
  exit 0
fi

NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
RELEASE_BODY="<!-- factory_release:run_id=${TARGET_RUN_ID} released_at=${NOW_ISO} -->"

while IFS= read -r lock_body; do
  existing_expires=$(echo "$lock_body" | sed -n 's/.*expires=\([^ ]*\).*/\1/p' || true)
  existing_run=$(echo "$lock_body"     | sed -n 's/.*run_id=\([^ ]*\).*/\1/p' || true)

  # Expired locks no longer count.
  if [[ -n "$existing_expires" && "$NOW_ISO" > "$existing_expires" ]]; then
    continue
  fi

  if [[ -z "$TARGET_RUN_ID" || "$existing_run" == "$TARGET_RUN_ID" ]]; then
    if gh issue comment "${ISSUE_NUMBER}" --body "${RELEASE_BODY}" >/dev/null 2>&1; then
      echo "lock_released:${existing_run}"
      exit 0
    else
      echo "factory_warn:release_comment_failed_will_expire_naturally" >&2
      exit 0
    fi
  fi
done <<< "$EXISTING_LOCKS"

# No matching active lock to release.
echo "no_matching_active_lock"
exit 0
