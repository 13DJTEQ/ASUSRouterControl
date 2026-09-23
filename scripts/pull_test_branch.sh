#!/usr/bin/env bash
# Pull the BW/Keychain Connect test branch and print Mac next steps.
#
# Usage (from ~/ASUSRouterControl):
#   bash scripts/pull_test_branch.sh
#   BRANCH=other-branch bash scripts/pull_test_branch.sh
#   bash scripts/pull_test_branch.sh other-branch
#   bash scripts/pull_test_branch.sh --no-next
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEFAULT_BRANCH="cursor/bw-keychain-mp-only-8890"
SHOW_NEXT=1
BRANCH="${BRANCH:-}"

for arg in "$@"; do
  case "$arg" in
    --no-next)
      SHOW_NEXT=0
      ;;
    -h|--help)
      echo "Usage: bash scripts/pull_test_branch.sh [BRANCH] [--no-next]"
      echo "Default branch: ${DEFAULT_BRANCH}"
      echo "Override with BRANCH=... or a positional BRANCH argument."
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
    *)
      if [[ -n "$BRANCH" ]]; then
        echo "Multiple branch arguments: already '${BRANCH}', also '${arg}'" >&2
        exit 2
      fi
      BRANCH="$arg"
      ;;
  esac
done

BRANCH="${BRANCH:-$DEFAULT_BRANCH}"

echo "Repo: ${ROOT}"
echo "Fetching origin…"
git fetch origin

echo "Checking out ${BRANCH}…"
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  git checkout "${BRANCH}"
elif git show-ref --verify --quiet "refs/remotes/origin/${BRANCH}"; then
  git checkout -B "${BRANCH}" "origin/${BRANCH}"
else
  echo "Branch not found locally or on origin: ${BRANCH}" >&2
  exit 1
fi

echo "Fast-forward pull…"
git pull --ff-only "origin" "${BRANCH}"

SHORT_SHA="$(git rev-parse --short HEAD)"
CURRENT_BRANCH="$(git branch --show-current)"
echo
echo "Ready: ${CURRENT_BRANCH} @ ${SHORT_SHA}"

if [[ "$SHOW_NEXT" -eq 1 ]]; then
  echo
  echo "Next Mac test steps:"
  echo "  1. Confirm branch tip: git status && git log -1 --oneline"
  echo "  2. If vault is locked, unlock once in Terminal: bw unlock"
  echo "     Then export BW_SESSION from that unlock (or re-run sync after unlock)."
  echo "  3. Sync router creds into DEV env: bash scripts/bw_sync_router_env.sh"
  echo "  4. Rebuild DEV app: make build-dev-app"
  echo "  5. Launch: open \"testbuilds/ASUSRouterControl DEV.app\""
  echo "  6. Optional verify: make verify-dev-app"
fi
