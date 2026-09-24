#!/usr/bin/env bash
# Mac Connect test orchestrator — one-liner for local Mac Connect verification.
#
# Chains pull → optional unit → bw_sync → probe → build → verify → optional logs.
# Never prompts for Bitwarden master password (no getpass). Bitwarden-first;
# Keychain mirror is written by bw_sync for vault-locked Connect.
#
# Usage (from repo root on Dave's Mac):
#   make mac-test
#   bash scripts/mac_test_connect.sh
#   BRANCH=other bash scripts/mac_test_connect.sh --skip-unit --logs
#   bash scripts/mac_test_connect.sh --dry-run
#
# Flags:
#   --skip-pull     Skip git fetch/checkout/pull
#   --skip-unit     Skip validate.sh / pytest even if .venv exists
#   --skip-sync     Skip bw_sync_router_env.sh
#   --skip-probe    Skip probe_router_login.sh
#   --skip-app      Skip build-dev-app + verify-dev-app (Linux/CI partial runs)
#   --logs          After success (or after app steps), run pull_connect_logs.sh
#   --dry-run       Print planned steps; do not execute
#   -h, --help      Show this help
#
# Env:
#   BRANCH          Override test branch (default: cursor/bw-keychain-mp-only-8890)
#
# Exit codes:
#   0  success
#   1  step failure
#   2  vault locked / need stock bw unlock (from bw_sync)
#   3  usage / bad args
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEFAULT_BRANCH="cursor/bw-keychain-mp-only-8890"
BRANCH="${BRANCH:-$DEFAULT_BRANCH}"

SKIP_PULL=0
SKIP_UNIT=0
SKIP_SYNC=0
SKIP_PROBE=0
SKIP_APP=0
WANT_LOGS=0
DRY_RUN=0

usage() {
  sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --skip-pull)  SKIP_PULL=1 ;;
    --skip-unit)  SKIP_UNIT=1 ;;
    --skip-sync)  SKIP_SYNC=1 ;;
    --skip-probe) SKIP_PROBE=1 ;;
    --skip-app)   SKIP_APP=1 ;;
    --logs)       WANT_LOGS=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg" >&2
      echo "Run: bash scripts/mac_test_connect.sh --help" >&2
      exit 3
      ;;
    *)
      echo "Unexpected argument: $arg (use BRANCH=... for branch override)" >&2
      exit 3
      ;;
  esac
done

_step_num=0
_step_total=0

_count_steps() {
  local n=0
  [[ "$SKIP_PULL" -eq 0 ]] && n=$((n + 1))
  [[ "$SKIP_UNIT" -eq 0 ]] && n=$((n + 1))
  [[ "$SKIP_SYNC" -eq 0 ]] && n=$((n + 1))
  [[ "$SKIP_PROBE" -eq 0 ]] && n=$((n + 1))
  if [[ "$SKIP_APP" -eq 0 ]]; then
    n=$((n + 2)) # build + verify
  fi
  [[ "$WANT_LOGS" -eq 1 ]] && n=$((n + 1))
  _step_total=$n
}

_banner() {
  _step_num=$((_step_num + 1))
  echo
  echo "======== [mac-test ${_step_num}/${_step_total}] $1 ========"
}

_fail_unlock_hint() {
  echo >&2
  echo "Vault locked (or no Keychain BW_SESSION / mirror). Unlock once in Terminal:" >&2
  echo "  1. bw unlock" >&2
  echo "  2. export BW_SESSION=\"<token from unlock --raw>\"" >&2
  echo "  3. bash scripts/bw_sync_router_env.sh" >&2
  echo "  4. make mac-test SKIP_PULL=1" >&2
  echo "     (or: bash scripts/mac_test_connect.sh --skip-pull)" >&2
  echo "This orchestrator never prompts for the Bitwarden master password." >&2
}

_run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: $*"
    return 0
  fi
  "$@"
}

_is_darwin() {
  [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]]
}

_venv_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
    echo "$ROOT/.venv/bin/python3"
  else
    return 1
  fi
}

_count_steps

echo "ASUSRouterControl Mac Connect orchestrator"
echo "Repo: ${ROOT}"
echo "Branch: ${BRANCH}"
echo "Dry-run: ${DRY_RUN}"

# --- 1. pull ---
if [[ "$SKIP_PULL" -eq 0 ]]; then
  _banner "pull_test_branch (--no-next)"
  _run env BRANCH="$BRANCH" bash "$ROOT/scripts/pull_test_branch.sh" --no-next "$BRANCH"
else
  echo "(skip pull)"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  SHORT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
  echo "At: ${CURRENT_BRANCH} @ ${SHORT_SHA}"
fi

# --- 2. optional unit ---
if [[ "$SKIP_UNIT" -eq 0 ]]; then
  _banner "unit (validate.sh if .venv present)"
  if _venv_python >/dev/null 2>&1; then
    # Prefer venv python on PATH for validate.sh (uses python3 from PATH).
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "DRY-RUN: PATH=.venv/bin:\$PATH bash scripts/validate.sh"
    else
      # validate.sh invokes python3; ensure venv wins when present.
      export PATH="${ROOT}/.venv/bin:${PATH}"
      bash "$ROOT/scripts/validate.sh"
    fi
  else
    echo "No .venv — skipping unit validation (pass --skip-unit to silence)."
    echo "Create with: make setup"
  fi
else
  echo "(skip unit)"
fi

# --- 3. bw_sync ---
if [[ "$SKIP_SYNC" -eq 0 ]]; then
  _banner "bw_sync_router_env (non-interactive)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: bash scripts/bw_sync_router_env.sh"
  else
    set +e
    bash "$ROOT/scripts/bw_sync_router_env.sh"
    sync_rc=$?
    set -e
    if [[ "$sync_rc" -eq 2 ]]; then
      _fail_unlock_hint
      exit 2
    fi
    if [[ "$sync_rc" -ne 0 ]]; then
      echo "bw_sync failed (exit ${sync_rc})." >&2
      _fail_unlock_hint
      exit "$sync_rc"
    fi
  fi
else
  echo "(skip sync)"
fi

# --- 4. probe ---
if [[ "$SKIP_PROBE" -eq 0 ]]; then
  _banner "probe_router_login (non-interactive)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: NONINTERACTIVE=1 bash scripts/probe_router_login.sh </dev/null"
  else
    # Never fall into interactive password / getpass.
    set +e
    NONINTERACTIVE=1 bash "$ROOT/scripts/probe_router_login.sh" </dev/null
    probe_rc=$?
    set -e
    if [[ "$probe_rc" -ne 0 ]]; then
      echo "Router HTTP probe failed (exit ${probe_rc})." >&2
      echo "Confirm LAN reachability and: bash scripts/bw_sync_router_env.sh" >&2
      exit "$probe_rc"
    fi
  fi
else
  echo "(skip probe)"
fi

# --- 5–6. build + verify ---
if [[ "$SKIP_APP" -eq 0 ]]; then
  if ! _is_darwin && [[ "$DRY_RUN" -eq 0 ]]; then
    echo "Not Darwin — skipping build-dev-app / verify-dev-app." >&2
    echo "On the Mac: make mac-test   (or pass --skip-app on Linux)." >&2
    exit 1
  fi

  _banner "make build-dev-app"
  _run make -C "$ROOT" build-dev-app

  _banner "make verify-dev-app"
  # Avoid double-build: verify normally rebuilds; we already built above.
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: VERIFY_DEV_APP_SKIP_BUILD=1 make verify-dev-app"
  else
    VERIFY_DEV_APP_SKIP_BUILD=1 make -C "$ROOT" verify-dev-app
  fi
else
  echo "(skip app build/verify)"
fi

# --- 7. optional logs ---
if [[ "$WANT_LOGS" -eq 1 ]]; then
  _banner "pull_connect_logs (redacted)"
  _run bash "$ROOT/scripts/pull_connect_logs.sh"
fi

echo
echo "======== mac-test OK ========"
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "Branch: $(git branch --show-current 2>/dev/null || echo unknown) @ $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi
echo "Connect in DEV.app with blank password (Keychain mirror) when vault is locked."
echo "Logs later: bash scripts/pull_connect_logs.sh"
exit 0
