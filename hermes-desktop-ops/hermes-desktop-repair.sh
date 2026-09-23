#!/usr/bin/env bash
# hermes-desktop-repair.sh — staged Hermes Desktop repair (dry-run default).
# Canonical on-Mac location: ~/Desktop/hermes-desktop-ops/
# Wraps official hermes CLI + launchd kickstart. No raw token handling.
#
# Usage:
#   ./hermes-desktop-repair.sh              # dry-run (default)
#   ./hermes-desktop-repair.sh --dry-run
#   ./hermes-desktop-repair.sh --apply
#   ./hermes-desktop-repair.sh --hypothesis H1_desktop_python_gateway --apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="${HOME}/Desktop"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
APPLY=0
HYPOTHESIS=""
GATEWAY_LABEL="ai.hermes.gateway"

usage() {
  cat <<'EOF'
Usage: hermes-desktop-repair.sh [--dry-run|--apply] [--hypothesis ID]

  --dry-run     Print planned stages only (default)
  --apply       Execute repair stages (mutates gateway / may re-auth)
  --hypothesis  Override hypothesis id (default: from newest Desktop report.json
                primary_hypothesis, else H1_desktop_python_gateway)

Supported paths:
  H1_desktop_python_gateway  — venv/python + gateway kickstart + :9130 check
  H2_nous_oauth_rt           — hermes auth add nous --type oauth (interactive)
  H3_gateway_ops             — gateway status + kickstart
  H4_resource_oom            — advisory only (no auto unload)
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --hypothesis)
      [[ $# -ge 2 ]] || die "--hypothesis needs an id"
      HYPOTHESIS="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

MODE="dry-run"
[[ "${APPLY}" -eq 1 ]] && MODE="apply"

find_newest_report_json() {
  local f
  # Prefer newest report.json on Desktop matching hermes-incident-*.report.json
  f="$(ls -t "${DESKTOP}"/hermes-incident-*.report.json 2>/dev/null | head -1 || true)"
  if [[ -z "${f}" ]]; then
    f="$(ls -t "${DESKTOP}"/hermes-incident-*.report_*.json 2>/dev/null | head -1 || true)"
  fi
  echo "${f}"
}

resolve_hypothesis() {
  if [[ -n "${HYPOTHESIS}" ]]; then
    echo "${HYPOTHESIS}"
    return
  fi
  local rj primary
  rj="$(find_newest_report_json)"
  if [[ -n "${rj}" && -f "${rj}" ]] && have python3; then
    primary="$(python3 -c "
import json,sys
p=sys.argv[1]
try:
  d=json.load(open(p,encoding='utf-8'))
  print(d.get('primary_hypothesis') or '')
except Exception:
  print('')
" "${rj}" 2>/dev/null || true)"
    if [[ -n "${primary}" ]]; then
      echo "NOTE: hypothesis from $(basename "${rj}"): ${primary}" >&2
      echo "${primary}"
      return
    fi
  fi
  echo "H1_desktop_python_gateway"
}

run_or_echo() {
  if [[ "${APPLY}" -eq 1 ]]; then
    echo "+ $*"
    "$@"
  else
    echo "[dry-run] $*"
  fi
}

probe_port() {
  if have nc; then
    if nc -z 127.0.0.1 9130 2>/dev/null; then
      echo "port 9130: open"
    else
      echo "port 9130: closed"
    fi
  elif have python3; then
    python3 - <<'PY' || true
import socket
s=socket.socket()
s.settimeout(1)
try:
  s.connect(("127.0.0.1",9130))
  print("port 9130: open")
except OSError:
  print("port 9130: closed")
finally:
  s.close()
PY
  else
    echo "port 9130: (no nc/python3 to probe)"
  fi
}

kickstart_gateway() {
  local uid label
  uid="$(id -u)"
  label="gui/${uid}/${GATEWAY_LABEL}"
  if have launchctl; then
    run_or_echo launchctl kickstart -k "${label}"
  else
    echo "NOTE: launchctl not available — skip kickstart"
  fi
}

stage_preflight() {
  echo "== Stage: preflight =="
  if have hermes; then
    run_or_echo hermes --version || true
    run_or_echo hermes doctor || true
    run_or_echo hermes gateway status || true
  else
    echo "WARN: hermes not on PATH"
  fi
  probe_port
}

stage_h1() {
  echo "== Stage: H1 desktop/python/gateway =="
  echo "Intent: ensure gateway uses venv Python (not system /usr/bin/python3); restart; recheck :9130"
  if [[ -x "${HERMES_HOME}/venv/bin/python" ]]; then
    echo "venv python: ${HERMES_HOME}/venv/bin/python"
    if [[ "${APPLY}" -eq 1 ]]; then
      "${HERMES_HOME}/venv/bin/python" -c "import hermes_cli; print('hermes_cli: OK')" 2>&1 || \
        echo "WARN: venv cannot import hermes_cli"
    else
      echo "[dry-run] ${HERMES_HOME}/venv/bin/python -c 'import hermes_cli'"
    fi
  else
    echo "WARN: missing ${HERMES_HOME}/venv/bin/python — Desktop may still be on system Python"
  fi
  if [[ -x /usr/bin/python3 ]]; then
    if [[ "${APPLY}" -eq 1 ]]; then
      /usr/bin/python3 -c "import hermes_cli" 2>&1 && echo "system python: hermes_cli unexpected OK" || \
        echo "system python: hermes_cli FAIL (expected on H1)"
    else
      echo "[dry-run] /usr/bin/python3 -c 'import hermes_cli'  # expect FAIL on H1"
    fi
  fi
  kickstart_gateway
  if have hermes; then
    run_or_echo hermes gateway status || true
  fi
  probe_port
  echo "Verify: Desktop app should use the same venv interpreter as CLI; if :9130 still closed, check launchd ProgramArguments."
}

stage_h2() {
  echo "== Stage: H2 Nous OAuth =="
  echo "Intent: hermes auth add nous --type oauth (interactive; no external token health-checks)"
  if have hermes; then
    run_or_echo hermes auth add nous --type oauth
  else
    die "hermes not on PATH; cannot re-auth"
  fi
}

stage_h3() {
  echo "== Stage: H3 gateway ops =="
  if have hermes; then
    run_or_echo hermes gateway status || true
  fi
  kickstart_gateway
  probe_port
}

stage_h4() {
  echo "== Stage: H4 resource/OOM (advisory) =="
  echo "Unload heavy local models / disable face render before chat; check Activity Monitor."
  echo "No automatic process kill in this script."
  if [[ "${APPLY}" -eq 1 ]]; then
    echo "Applied mode: advisory only — operator action required."
  else
    echo "[dry-run] advisory only"
  fi
}

HYPOTHESIS="$(resolve_hypothesis)"
echo "hermes-desktop-repair mode=${MODE} hypothesis=${HYPOTHESIS}"
echo "hermes_home=${HERMES_HOME}"
echo

stage_preflight
echo

case "${HYPOTHESIS}" in
  H1_desktop_python_gateway|H1) stage_h1 ;;
  H2_nous_oauth_rt|H2) stage_h2 ;;
  H3_gateway_ops|H3) stage_h3 ;;
  H4_resource_oom|H4) stage_h4 ;;
  *)
    echo "WARN: unknown hypothesis '${HYPOTHESIS}' — running H1 path"
    stage_h1
    ;;
esac

echo
echo "== Done (${MODE}) =="
if [[ "${APPLY}" -eq 0 ]]; then
  echo "Re-run with --apply to execute. Prefer archive first if you have not collected yet:"
  echo "  /bin/bash ${SCRIPT_DIR}/hermes-desktop-archive.sh"
fi
exit 0
