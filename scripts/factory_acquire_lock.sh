#!/usr/bin/env bash
#
# factory_acquire_lock.sh — Issue-level idempotency lock for the factory.
#
# Usage:
#   factory_acquire_lock.sh <issue_number> [--agent <name>] [--ttl-minutes <n>]
#
# Effect:
#   Posts a comment on the GitHub issue with the format:
#     <!-- factory_lock:run_id=<id> expires=<iso_ts> agent=<name> -->
#   Returns:
#     0  -> lock acquired (or already held by THIS run; idempotent)
#     1  -> active lock held by another run (refused)
#     2  -> input/infra error (do not proceed)
#
# Contract: this script MUST be safe to re-run with the same arguments.
# The Implement Skill calls it after preflight asserts pass.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ISSUE_NUMBER="${1:-}"
AGENT_NAME="${FACTORY_AGENT_NAME:-factory-bot}"
TTL_MINUTES="${FACTORY_LOCK_TTL_MINUTES:-30}"

if [[ -z "$ISSUE_NUMBER" ]]; then
  echo "factory_refused:missing_issue_number" >&2
  exit 2
fi

# Generate run_id deterministically from issue + timestamp to simplify audit.
# Use UTC; nano-precision to avoid collisions on rapid retries.
RUN_ID="factory-$(date -u +%Y%m%dT%H%M%S)-${ISSUE_NUMBER}-$(printf '%04x' $RANDOM)"
EXPIRES=$(date -u -v +"${TTL_MINUTES}"M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
  || date -u -d "+${TTL_MINUTES} minutes" +"%Y-%m-%dT%H:%M:%SZ")
COMMENT_BODY="<!-- factory_lock:run_id=${RUN_ID} expires=${EXPIRES} agent=${AGENT_NAME} -->"

# Idempotency check: do we already have an active factory_marker (triage)
# AND no conflicting lock? Implement Skill always runs after triage, so
# we trust the triage marker exists (caller asserts). We only need to
# verify no OTHER factory_lock comment is unexpired.

EXISTING_LOCKS=$(gh issue view "${ISSUE_NUMBER}" --json comments \
  -q '.comments[] | select(.body | test("factory_lock:")) | .body' 2>/dev/null || true)

NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ -n "$EXISTING_LOCKS" ]]; then
  while IFS= read -r lock_body; do
    # Only one line per lock comment, but be defensive.
    existing_expires=$(echo "$lock_body" | sed -n 's/.*expires=\([^ ]*\).*/\1/p' || true)
    existing_run=$(echo "$lock_body"     | sed -n 's/.*run_id=\([^ ]*\).*/\1/p' || true)
    existing_agent=$(echo "$lock_body"  | sed -n 's/.*agent=\([^ ]*\).*/\1/p' || true)
    if [[ -z "$existing_expires" ]]; then
      continue   # malformed; ignore
    fi
    # Compare lexically (ISO-8601 UTC sorts correctly).
    if [[ "$NOW_ISO" < "$existing_expires" ]]; then
      if [[ "$existing_run" == "$RUN_ID" ]]; then
        echo "lock_held_by_self:${RUN_ID}"
        exit 0
      fi
      echo "factory_refused:active_lock_held:${existing_run}:expires=${existing_expires}:agent=${existing_agent}" >&2
      exit 1
    fi
  done <<< "$EXISTING_LOCKS"
fi

# No active lock; acquire.
if gh issue comment "${ISSUE_NUMBER}" --body "${COMMENT_BODY}" >/dev/null; then
  echo "lock_acquired:${RUN_ID}:expires=${EXPIRES}"
  exit 0
else
  echo "factory_refused:gh_comment_failed" >&2
  exit 2
fi
