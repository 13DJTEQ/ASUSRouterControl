#!/usr/bin/env bash
# hermes-desktop-repair.sh — Fast→Slow Hermes Desktop repair (dry-run default).
# Canonical on-Mac location: ~/Desktop/hermes-desktop-ops/
# Uses lib/live_progress.sh (LIVE.log / LIVE.status; heartbeats after >20s).
# Wraps official hermes CLI + launchd. No raw token handling.
#
# Usage (Mac A):
#   /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh
#   /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh --apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/live_progress.sh
source "${SCRIPT_DIR}/lib/live_progress.sh"
_live_init "repair"

DESKTOP="${_LIVE_DESKTOP}"
OPS_DIR="${_LIVE_OPS}"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
APPLY=0
SKIP_ARCHIVE=0
FORCE_ARCHIVE=0
GATEWAY_LABEL="ai.hermes.gateway"
# Archives newer than this many seconds count as "recent" (default 6h).
ARCHIVE_MAX_AGE_SECS="${HERMES_REPAIR_ARCHIVE_MAX_AGE_SECS:-21600}"
PHASE_TOTAL=9
OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
IS_DARWIN=0
[[ "${OS_NAME}" == "Darwin" ]] && IS_DARWIN=1

usage() {
  cat <<'EOF'
Usage: hermes-desktop-repair.sh [--dry-run|--apply] [--skip-archive|--force-archive]

  --dry-run         Print planned stages only (default; no mutations)
  --apply           Execute repair stages (brief gateway stop, update, restart)
  --skip-archive    Do not require/run collector (use only if archive already done)
  --force-archive   Always run hermes-desktop-archive.sh before repair (apply only)

Fast→Slow stages:
  1 preflight
  2 require recent Desktop archive (or run collector)
  3 stop gateway briefly (Darwin launchd)
  4 hermes doctor
  5 Nous re-auth guidance if refresh token revoked
  6 ensure venv python for Desktop/gateway
  7 hermes update if needed
  8 restart ai.hermes.gateway
  9 verify :9130 + doctor + chat ping

macOS-only steps no-op/warn on Linux. Safe default is dry-run.
EOF
}

die() {
  live_warn "ERROR: $*"
  live_status "phase=error" "msg=$*"
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --skip-archive) SKIP_ARCHIVE=1; shift ;;
    --force-archive) FORCE_ARCHIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

MODE="dry-run"
[[ "${APPLY}" -eq 1 ]] && MODE="apply"

darwin_only() {
  local label="$1"
  shift
  if [[ "${IS_DARWIN}" -ne 1 ]]; then
    live_warn "skip macOS-only (${label}) on ${OS_NAME}/${ARCH_NAME}"
    return 0
  fi
  "$@"
}

run_or_echo() {
  local label="$1"
  shift
  if [[ "${APPLY}" -eq 1 ]]; then
    live_note "+ ${label}: $*"
    "$@"
  else
    live_note "[dry-run] ${label}: $*"
  fi
}

# Long-running hermes commands get heartbeats via live_run_capture.
run_hermes_capture() {
  local label="$1"
  shift
  local out
  out="$(mktemp "${TMPDIR:-/tmp}/hermes-repair.XXXXXX")"
  if [[ "${APPLY}" -eq 1 ]]; then
    live_run_capture "${label}" "${out}" "$@" || true
    if [[ -s "${out}" ]]; then
      # Keep LIVE.log readable: last ~30 lines of captured output
      live_note "--- ${label} output (tail) ---"
      tail -n 30 "${out}" | while IFS= read -r line || [[ -n "${line}" ]]; do
        live_note "  ${line}"
      done
    fi
  else
    live_note "[dry-run] ${label}: $*"
  fi
  rm -f "${out}"
}

probe_port_9130() {
  if have nc; then
    if nc -z -w 2 127.0.0.1 9130 >/dev/null 2>&1; then
      echo "open"
      return 0
    fi
    echo "closed"
    return 1
  elif have python3; then
    if python3 - <<'PY' >/dev/null 2>&1
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", 9130))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
    then
      echo "open"
      return 0
    fi
    echo "closed"
    return 1
  fi
  echo "unknown"
  return 2
}

find_recent_archive() {
  local f newest="" newest_mtime=0 mtime now
  now="$(date +%s)"
  shopt -s nullglob
  for f in "${DESKTOP}"/hermes-incident-*.zip \
           "${DESKTOP}"/hermes-incident-*.tar.gz \
           "${DESKTOP}"/hermes-incident-*.manifest.json; do
    [[ -f "${f}" ]] || continue
    if stat -c %Y "${f}" >/dev/null 2>&1; then
      mtime="$(stat -c %Y "${f}")"
    else
      mtime="$(stat -f %m "${f}" 2>/dev/null || echo 0)"
    fi
    if (( now - mtime <= ARCHIVE_MAX_AGE_SECS )) && (( mtime >= newest_mtime )); then
      newest_mtime="${mtime}"
      newest="${f}"
    fi
  done
  shopt -u nullglob
  echo "${newest}"
}

detect_rt_revoked() {
  # Returns 0 if refresh token looks revoked / null / quarantine.
  local auth_json="${HERMES_HOME}/auth.json"
  local report_json
  report_json="$(ls -t "${DESKTOP}"/hermes-incident-*.report.json 2>/dev/null | head -1 || true)"

  if [[ -n "${report_json}" && -f "${report_json}" ]] && have python3; then
    if python3 - <<PY 2>/dev/null
import json, sys
d = json.load(open("${report_json}", encoding="utf-8"))
# Prefer explicit auth flags from collector
auth = d.get("auth") or {}
if auth.get("refresh_token_null") is True or str(auth.get("refresh_token_null")).lower() == "true":
    sys.exit(0)
if auth.get("quarantine_or_revoke_hint") is True or str(auth.get("quarantine_or_revoke_hint")).lower() == "true":
    sys.exit(0)
# Hypotheses may list H2 as primary
primary = (d.get("primary_hypothesis") or "")
if primary.startswith("H2"):
    sys.exit(0)
for h in d.get("hypotheses") or []:
    if h.get("id", "").startswith("H2") and int(h.get("score") or 0) >= 5:
        sys.exit(0)
sys.exit(1)
PY
    then
      return 0
    fi
  fi

  if [[ -f "${auth_json}" ]] && have python3; then
    if AUTH_JSON_PATH="${auth_json}" python3 - <<'PY' 2>/dev/null
import json, os, re, sys
path = os.environ["AUTH_JSON_PATH"]
text = open(path, encoding="utf-8", errors="replace").read()
rt_null = bool(re.search(r'"refresh_token"\s*:\s*null', text)) or bool(
    re.search(r'"refresh_token_fp"\s*:\s*null', text)
)
quarantine = bool(re.search(r"quarantine|revok|invalid_grant|relogin_required", text, re.I))
sys.exit(0 if (rt_null or quarantine) else 1)
PY
    then
      return 0
    fi
  fi
  return 1
}

find_venv_python() {
  local c
  for c in \
    "${HERMES_HOME}/venv/bin/python" \
    "${HERMES_HOME}/venv/bin/python3" \
    "${HERMES_HOME}/hermes-agent/venv/bin/python" \
    "${HERMES_HOME}/hermes-agent/venv/bin/python3"; do
    if [[ -x "${c}" ]]; then
      echo "${c}"
      return 0
    fi
  done
  return 1
}

gateway_stop_brief() {
  local uid label
  uid="$(id -u)"
  label="gui/${uid}/${GATEWAY_LABEL}"
  if ! have launchctl; then
    live_warn "launchctl missing — cannot stop gateway"
    return 0
  fi
  live_note "stopping gateway briefly: launchctl bootout ${label} (best-effort)"
  if [[ "${APPLY}" -eq 1 ]]; then
    launchctl bootout "${label}" 2>/dev/null || \
      launchctl unload "${HOME}/Library/LaunchAgents/${GATEWAY_LABEL}.plist" 2>/dev/null || \
      live_warn "gateway stop: already unloaded or bootout failed (ok)"
    sleep 2
  else
    live_note "[dry-run] launchctl bootout ${label}"
  fi
}

gateway_restart() {
  local uid label plist
  uid="$(id -u)"
  label="gui/${uid}/${GATEWAY_LABEL}"
  plist="${HOME}/Library/LaunchAgents/${GATEWAY_LABEL}.plist"
  if ! have launchctl; then
    live_warn "launchctl missing — cannot restart gateway"
    return 0
  fi
  if [[ "${APPLY}" -eq 1 ]]; then
    local out
    out="$(mktemp "${TMPDIR:-/tmp}/hermes-kick.XXXXXX")"
    if [[ -f "${plist}" ]]; then
      live_note "bootstrap/kickstart ${label}"
      launchctl bootstrap "gui/${uid}" "${plist}" 2>/dev/null || true
    fi
    live_run_capture "launchctl kickstart -k ${label}" "${out}" \
      launchctl kickstart -k "${label}" || true
    rm -f "${out}"
  else
    live_note "[dry-run] launchctl bootstrap gui/${uid} ${plist} (if needed)"
    live_note "[dry-run] launchctl kickstart -k ${label}"
  fi
}

# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

live_note "hermes-desktop-repair mode=${MODE} os=${OS_NAME} arch=${ARCH_NAME}"
live_note "hermes_home=${HERMES_HOME}"
live_note "live progress: steps >${HERMES_PROGRESS_AFTER_SECS}s emit heartbeats every ${HERMES_PROGRESS_EVERY_SECS}s"
live_status "phase=start" "mode=${MODE}" "os=${OS_NAME}" "arch=${ARCH_NAME}"

if [[ "${IS_DARWIN}" -ne 1 ]]; then
  live_warn "non-Darwin host — macOS-only steps (launchd gateway) will no-op/warn"
elif [[ "${ARCH_NAME}" != "arm64" ]]; then
  live_warn "expected Darwin arm64; continuing on ${ARCH_NAME}"
fi

# --- 1. preflight ---
live_phase 1 "${PHASE_TOTAL}" "preflight"
live_status "phase=preflight" "mode=${MODE}"
live_note "PATH hermes=$(command -v hermes 2>/dev/null || echo missing)"
if have hermes; then
  if [[ "${APPLY}" -eq 1 ]]; then
    run_hermes_capture "hermes --version" hermes --version
  else
    live_note "[dry-run] hermes --version"
    hermes --version 2>/dev/null || live_warn "hermes --version failed (still dry-run)"
  fi
else
  live_warn "hermes not on PATH — later stages limited"
fi
PORT_STATE="$(probe_port_9130 || true)"
live_note "port 9130: ${PORT_STATE}"

# --- 2. archive ---
live_phase 2 "${PHASE_TOTAL}" "archive"
live_status "phase=archive" "mode=${MODE}"
RECENT="$(find_recent_archive)"
if [[ "${SKIP_ARCHIVE}" -eq 1 ]]; then
  live_note "archive check skipped (--skip-archive)"
elif [[ "${FORCE_ARCHIVE}" -eq 1 ]]; then
  live_note "force archive requested"
  if [[ "${APPLY}" -eq 1 ]]; then
    [[ -x "${SCRIPT_DIR}/hermes-desktop-archive.sh" ]] || die "missing hermes-desktop-archive.sh"
    live_run "hermes-desktop-archive.sh" /bin/bash "${SCRIPT_DIR}/hermes-desktop-archive.sh"
  else
    live_note "[dry-run] /bin/bash ${SCRIPT_DIR}/hermes-desktop-archive.sh"
  fi
elif [[ -n "${RECENT}" ]]; then
  live_note "recent archive present (<=${ARCHIVE_MAX_AGE_SECS}s): $(basename "${RECENT}")"
else
  live_note "no recent Desktop archive — will run collector"
  if [[ "${APPLY}" -eq 1 ]]; then
    [[ -x "${SCRIPT_DIR}/hermes-desktop-archive.sh" ]] || die "missing hermes-desktop-archive.sh"
    live_run "hermes-desktop-archive.sh" /bin/bash "${SCRIPT_DIR}/hermes-desktop-archive.sh"
  else
    live_note "[dry-run] /bin/bash ${SCRIPT_DIR}/hermes-desktop-archive.sh"
    live_note "[dry-run] (or place a fresh hermes-incident-* on Desktop first)"
  fi
fi

# --- 3. stop gateway briefly ---
live_phase 3 "${PHASE_TOTAL}" "stop_gateway"
live_status "phase=stop_gateway" "mode=${MODE}"
darwin_only "stop_gateway" gateway_stop_brief

# --- 4. doctor ---
live_phase 4 "${PHASE_TOTAL}" "doctor"
live_status "phase=doctor" "mode=${MODE}"
if have hermes; then
  run_hermes_capture "hermes doctor" hermes doctor
else
  live_warn "skip doctor — hermes missing"
fi

# --- 5. Nous re-auth if RT revoked ---
live_phase 5 "${PHASE_TOTAL}" "nous_auth"
live_status "phase=nous_auth" "mode=${MODE}"
if detect_rt_revoked; then
  live_warn "refresh token looks revoked/null or H2 elevated — Nous re-auth needed"
  live_note "guidance: hermes auth add nous --type oauth  (interactive; no external token health-checks)"
  if [[ "${APPLY}" -eq 1 ]]; then
    if have hermes; then
      live_note "starting interactive Nous OAuth (operator must complete browser flow)"
      # Do not wrap in live_run — interactive TTY needed
      hermes auth add nous --type oauth || live_warn "Nous auth exited non-zero — re-run manually if needed"
    else
      die "hermes not on PATH; cannot re-auth Nous"
    fi
  else
    live_note "[dry-run] hermes auth add nous --type oauth"
  fi
else
  live_note "Nous RT looks OK / H2 not elevated — skip re-auth (guidance only if later portal fails)"
  live_note "if portal auth fails later: hermes auth add nous --type oauth"
fi

# --- 6. ensure venv python ---
live_phase 6 "${PHASE_TOTAL}" "venv_python"
live_status "phase=venv_python" "mode=${MODE}"
VENV_PY=""
if VENV_PY="$(find_venv_python)"; then
  live_note "venv python: ${VENV_PY}"
  if [[ "${APPLY}" -eq 1 ]]; then
    if "${VENV_PY}" -c "import hermes_cli; print('hermes_cli: OK')" 2>&1; then
      live_note "venv hermes_cli import OK"
    else
      live_warn "venv cannot import hermes_cli — hermes update / reinstall may be required"
    fi
  else
    live_note "[dry-run] ${VENV_PY} -c 'import hermes_cli'"
  fi
  if [[ -x /usr/bin/python3 ]]; then
    if /usr/bin/python3 -c "import hermes_cli" 2>/dev/null; then
      live_note "system /usr/bin/python3 unexpectedly has hermes_cli"
    else
      live_note "system /usr/bin/python3: hermes_cli FAIL (expected for H1 — Desktop must use venv)"
    fi
  fi
  live_note "ensure Desktop/gateway ProgramArguments use: ${VENV_PY}"
  if [[ "${IS_DARWIN}" -eq 1 ]]; then
    PLIST="${HOME}/Library/LaunchAgents/${GATEWAY_LABEL}.plist"
    if [[ -f "${PLIST}" ]]; then
      if grep -q "${VENV_PY}" "${PLIST}" 2>/dev/null || grep -q 'venv/bin/python' "${PLIST}" 2>/dev/null; then
        live_note "plist appears to reference venv python"
      else
        live_warn "plist may not reference venv python — inspect ${PLIST}"
        if [[ "${APPLY}" -eq 0 ]]; then
          live_note "[dry-run] would advise fixing ProgramArguments to ${VENV_PY}"
        fi
      fi
    else
      live_warn "missing ${PLIST}"
    fi
  fi
else
  live_warn "no hermes venv python under ${HERMES_HOME} — install/update hermes first"
fi

# --- 7. hermes update if needed ---
live_phase 7 "${PHASE_TOTAL}" "hermes_update"
live_status "phase=hermes_update" "mode=${MODE}"
NEED_UPDATE=0
if [[ -z "${VENV_PY:-}" ]]; then
  NEED_UPDATE=1
elif [[ "${APPLY}" -eq 1 ]] && [[ -n "${VENV_PY}" ]]; then
  if ! "${VENV_PY}" -c "import hermes_cli" 2>/dev/null; then
    NEED_UPDATE=1
  fi
fi
# Also update when hermes reports outdated (best-effort dry check)
if have hermes && [[ "${NEED_UPDATE}" -eq 0 ]]; then
  if hermes update --help >/dev/null 2>&1; then
    live_note "hermes update available as command"
  fi
fi
if [[ "${NEED_UPDATE}" -eq 1 ]]; then
  live_note "hermes update indicated (missing/broken venv or hermes_cli)"
  if have hermes; then
    run_hermes_capture "hermes update" hermes update
  else
    live_warn "cannot hermes update — CLI missing from PATH"
  fi
else
  if [[ "${APPLY}" -eq 1 ]]; then
    live_note "venv OK — optional: hermes update (skipped unless HERMES_REPAIR_FORCE_UPDATE=1)"
    if [[ "${HERMES_REPAIR_FORCE_UPDATE:-0}" == "1" ]] && have hermes; then
      run_hermes_capture "hermes update (forced)" hermes update
    fi
  else
    live_note "[dry-run] hermes update  # only if venv/hermes_cli broken or HERMES_REPAIR_FORCE_UPDATE=1"
  fi
fi

# --- 8. restart gateway ---
live_phase 8 "${PHASE_TOTAL}" "restart_gateway"
live_status "phase=restart_gateway" "mode=${MODE}"
darwin_only "restart_gateway" gateway_restart
if have hermes; then
  run_hermes_capture "hermes gateway status" hermes gateway status
fi

# --- 9. verify ---
live_phase 9 "${PHASE_TOTAL}" "verify"
live_status "phase=verify" "mode=${MODE}"
if [[ "${APPLY}" -eq 1 ]]; then
  live_note "waiting briefly for gateway bind..."
  sleep 3
fi
PORT_STATE="$(probe_port_9130 || true)"
live_note "verify port 9130: ${PORT_STATE}"
if have hermes; then
  run_hermes_capture "hermes doctor (post)" hermes doctor
  # Chat ping — best-effort; hermes chat --help varies by version
  if hermes chat --help >/dev/null 2>&1; then
    if [[ "${APPLY}" -eq 1 ]]; then
      live_note "chat ping: best-effort non-interactive; else verify in Desktop UI"
      out="$(mktemp "${TMPDIR:-/tmp}/hermes-chat.XXXXXX")"
      live_run_capture "hermes chat ping" "${out}" hermes chat -m "ping" || true
      if ! grep -qiE 'ping|ok|reply|assistant|error' "${out}" 2>/dev/null; then
        hermes chat --message "ping" >"${out}" 2>&1 || \
          live_warn "chat ping not supported by this hermes build — verify in Desktop UI"
      fi
      tail -n 20 "${out}" 2>/dev/null | while IFS= read -r line || [[ -n "${line}" ]]; do
        live_note "  ${line}"
      done
      rm -f "${out}"
    else
      live_note "[dry-run] hermes chat -m 'ping'  # or Desktop UI send 'ping'"
    fi
  else
    live_note "hermes chat CLI unavailable — verify with Desktop UI chat ping after apply"
  fi
fi

live_status "phase=done" "mode=${MODE}" "port_9130=${PORT_STATE}"
live_note "done (${MODE}) port_9130=${PORT_STATE}"
if [[ "${APPLY}" -eq 0 ]]; then
  live_note "Re-run with --apply to execute. Prefer a fresh archive first if none recent:"
  live_note "  /bin/bash ${SCRIPT_DIR}/hermes-desktop-archive.sh"
  live_note "  /bin/bash ${SCRIPT_DIR}/hermes-desktop-repair.sh --apply"
fi
exit 0
