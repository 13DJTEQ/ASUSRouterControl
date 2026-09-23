#!/usr/bin/env bash
# hermes-desktop-archive.sh — non-destructive Hermes Desktop incident collector.
# Canonical on-Mac location: ~/Desktop/hermes-desktop-ops/
# Output on Desktop:
#   hermes-incident-<host>-<ts>.zip
#   hermes-incident-<host>-<ts>.report.md   ← paste into MOE plan
#   hermes-incident-<host>-<ts>.report.json ← machine-readable plan feed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="${HOME}/Desktop"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
HOST_SHORT="$(hostname -s 2>/dev/null || hostname | cut -d. -f1)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
INCIDENT_ID="hermes-incident-${HOST_SHORT}-${TS}"
OUT_ZIP="${DESKTOP}/${INCIDENT_ID}.zip"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/${INCIDENT_ID}.XXXXXX")"
REPORT_DIR="${STAGING}/report"
COLLECT_DIR="${STAGING}/collect"
LOG_TAIL_LINES="${HERMES_ARCHIVE_LOG_LINES:-400}"
MAX_LOG_BYTES="${HERMES_ARCHIVE_MAX_LOG_BYTES:-1048576}"

mkdir -p "${DESKTOP}" "${REPORT_DIR}" "${COLLECT_DIR}"/{doctor,gateway,install,launchd,ports,logs,crashes,auth,env}

cleanup() {
  rm -rf "${STAGING}"
}
trap cleanup EXIT

note() { printf '[archive] %s\n' "$*"; }
warn() { printf '[archive] WARN: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

run_capture() {
  local outfile="$1"
  shift
  {
    echo "\$ $*"
    "$@" 2>&1 || echo "EXIT:$?"
  } >"${outfile}" 2>&1 || true
}

safe_copy_tail() {
  local src="$1"
  local dest="$2"
  [[ -f "${src}" ]] || return 0
  local size
  size="$(wc -c <"${src}" | tr -d ' ')"
  if [[ "${size}" -gt "${MAX_LOG_BYTES}" ]]; then
    tail -c "${MAX_LOG_BYTES}" "${src}" >"${dest}" 2>/dev/null || true
    echo "(truncated to last ${MAX_LOG_BYTES} bytes; original ${size})" >>"${dest}"
  else
    tail -n "${LOG_TAIL_LINES}" "${src}" >"${dest}" 2>/dev/null || cp "${src}" "${dest}" 2>/dev/null || true
  fi
}

redact_file() {
  local f="$1"
  [[ -f "${f}" ]] || return 0
  have sed || return 0
  sed -E \
    -e 's/(api[_-]?key["[:space:]:＝=]+)[^[:space:]"]+/\1***REDACTED***/Ig' \
    -e 's/(authorization["[:space:]:＝=]+Bearer[[:space:]]+)[^[:space:]"]+/\1***REDACTED***/Ig' \
    -e 's/(refresh_token["[:space:]:＝=]+)[^[:space:]"]+/\1***REDACTED***/Ig' \
    -e 's/(access_token["[:space:]:＝=]+)[^[:space:]"]+/\1***REDACTED***/Ig' \
    -e 's/(client_secret["[:space:]:＝=]+)[^[:space:]"]+/\1***REDACTED***/Ig' \
    -e 's/(sk-[a-zA-Z0-9_-]{8,})/***REDACTED***/g' \
    -e 's/(OPENROUTER_API_KEY=).*/\1***REDACTED***/g' \
    -e 's/(ANTHROPIC_API_KEY=).*/\1***REDACTED***/g' \
    "${f}" >"${f}.redacted" 2>/dev/null && mv "${f}.redacted" "${f}" || true
}

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
note "incident=${INCIDENT_ID}"
note "os=${OS_NAME} arch=${ARCH_NAME} hermes_home=${HERMES_HOME}"

{
  echo "incident_id=${INCIDENT_ID}"
  echo "hostname=${HOST_SHORT}"
  echo "collected_at_utc=${TS}"
  echo "os=${OS_NAME}"
  echo "arch=${ARCH_NAME}"
  echo "hermes_home=${HERMES_HOME}"
  echo "desktop=${DESKTOP}"
  echo "script_dir=${SCRIPT_DIR}"
  echo "user=${USER:-unknown}"
  echo "home=${HOME}"
} >"${COLLECT_DIR}/env/preflight.txt"

HERMES_BIN=""
if have hermes; then
  HERMES_BIN="$(command -v hermes)"
fi
{
  echo "hermes_in_path=$([[ -n "${HERMES_BIN}" ]] && echo yes || echo no)"
  echo "hermes_bin=${HERMES_BIN:-}"
  if [[ -n "${HERMES_BIN}" ]]; then
    ls -la "${HERMES_BIN}" 2>&1 || true
    if [[ -L "${HERMES_BIN}" ]]; then
      echo "symlink_target=$(readlink "${HERMES_BIN}" 2>/dev/null || true)"
      echo "symlink_resolved=$(readlink -f "${HERMES_BIN}" 2>/dev/null || realpath "${HERMES_BIN}" 2>/dev/null || true)"
    fi
  fi
  echo "--- PATH ---"
  echo "${PATH}"
} >"${COLLECT_DIR}/install/which-hermes.txt"

if [[ -n "${HERMES_BIN}" ]]; then
  run_capture "${COLLECT_DIR}/doctor/version.txt" "${HERMES_BIN}" --version
  run_capture "${COLLECT_DIR}/doctor/doctor.txt" "${HERMES_BIN}" doctor
  run_capture "${COLLECT_DIR}/gateway/status.txt" "${HERMES_BIN}" gateway status
  if "${HERMES_BIN}" backup --help >/dev/null 2>&1; then
    note "running hermes backup --quick"
    run_capture "${COLLECT_DIR}/doctor/backup-quick.txt" \
      "${HERMES_BIN}" backup --quick -o "${COLLECT_DIR}/hermes-backup-quick.zip" || true
    if [[ ! -f "${COLLECT_DIR}/hermes-backup-quick.zip" ]]; then
      run_capture "${COLLECT_DIR}/doctor/backup-full.txt" \
        "${HERMES_BIN}" backup -o "${COLLECT_DIR}/hermes-backup-full.zip" || true
    fi
  fi
else
  warn "hermes not on PATH — collecting filesystem evidence only"
  echo "hermes CLI not found on PATH" >"${COLLECT_DIR}/doctor/missing.txt"
fi

{
  echo "=== system python3 ==="
  if have python3; then
    command -v python3
    python3 --version 2>&1 || true
    python3 -c 'import sys; print(sys.executable); print(sys.version)' 2>&1 || true
    python3 -c 'import hermes_cli' 2>&1 || echo "hermes_cli_import: FAIL"
  else
    echo "python3 not found"
  fi
  echo "=== /usr/bin/python3 ==="
  if [[ -x /usr/bin/python3 ]]; then
    /usr/bin/python3 --version 2>&1 || true
    /usr/bin/python3 -c 'import hermes_cli' 2>&1 || echo "hermes_cli_import_system: FAIL"
  fi
  echo "=== hermes venv python ==="
  for candidate in \
    "${HERMES_HOME}/hermes-agent/venv/bin/python" \
    "${HERMES_HOME}/hermes-agent/venv/bin/python3"; do
    if [[ -x "${candidate}" ]]; then
      echo "found=${candidate}"
      "${candidate}" --version 2>&1 || true
      "${candidate}" -c 'import hermes_cli; print("hermes_cli_import_venv: OK")' 2>&1 \
        || echo "hermes_cli_import_venv: FAIL"
    fi
  done
  echo "=== hermes-agent tree ==="
  if [[ -d "${HERMES_HOME}/hermes-agent" ]]; then
    ls -la "${HERMES_HOME}/hermes-agent" 2>&1 | head -n 40 || true
  else
    echo "missing ${HERMES_HOME}/hermes-agent"
  fi
} >"${COLLECT_DIR}/install/python-probe.txt"

if [[ -f "${HERMES_HOME}/config.yaml" ]]; then
  if have grep; then
    grep -E '^(model|provider|providers|fallback|gateway|tts|memory|agent|platform)' \
      "${HERMES_HOME}/config.yaml" >"${COLLECT_DIR}/install/config-fingerprint.yaml" 2>/dev/null \
      || head -n 80 "${HERMES_HOME}/config.yaml" >"${COLLECT_DIR}/install/config-fingerprint.yaml" 2>/dev/null || true
  else
    head -n 80 "${HERMES_HOME}/config.yaml" >"${COLLECT_DIR}/install/config-fingerprint.yaml" 2>/dev/null || true
  fi
  redact_file "${COLLECT_DIR}/install/config-fingerprint.yaml"
fi

AUTH_JSON="${HERMES_HOME}/auth.json"
AUTH_STATUS="missing"
NOUS_PRESENT="false"
RT_NULL="unknown"
QUARANTINE_HINT="unknown"
if [[ -f "${AUTH_JSON}" ]]; then
  AUTH_STATUS="present"
  if have python3; then
    AUTH_JSON_PATH="${AUTH_JSON}" python3 - <<'PY' >"${COLLECT_DIR}/auth/fingerprint.json" 2>"${COLLECT_DIR}/auth/fingerprint.err" || true
import json, os, re, sys
path = os.environ["AUTH_JSON_PATH"]
try:
    data = json.load(open(path))
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)

def scrub(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in ("token", "secret", "password", "api_key", "apikey", "authorization")):
                if v is None or v == "" or v == "null":
                    out[k] = None
                else:
                    out[k] = f"<redacted len={len(str(v))}>"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj[:50]]
    return obj

text = open(path, encoding="utf-8", errors="replace").read()
nous = bool(re.search(r"nous|portal", text, re.I))
rt_null = bool(re.search(r'"refresh_token"\s*:\s*null', text)) or bool(
    re.search(r'"refresh_token_fp"\s*:\s*null', text)
)
quarantine = bool(re.search(r"quarantine|revok|invalid_grant|relogin_required", text, re.I))
print(json.dumps({
    "nous_mentioned": nous,
    "refresh_token_null": rt_null,
    "quarantine_or_revoke_hint": quarantine,
    "top_level_keys": list(data.keys()) if isinstance(data, dict) else [],
    "structure": scrub(data),
}, indent=2))
PY
    if [[ -s "${COLLECT_DIR}/auth/fingerprint.json" ]]; then
      NOUS_PRESENT="$(python3 -c 'import json; d=json.load(open("'"${COLLECT_DIR}/auth/fingerprint.json"'")); print(str(d.get("nous_mentioned", False)).lower())' 2>/dev/null || echo unknown)"
      RT_NULL="$(python3 -c 'import json; d=json.load(open("'"${COLLECT_DIR}/auth/fingerprint.json"'")); print(str(d.get("refresh_token_null", False)).lower())' 2>/dev/null || echo unknown)"
      QUARANTINE_HINT="$(python3 -c 'import json; d=json.load(open("'"${COLLECT_DIR}/auth/fingerprint.json"'")); print(str(d.get("quarantine_or_revoke_hint", False)).lower())' 2>/dev/null || echo unknown)"
    fi
  else
    echo '{"error":"python3 unavailable for auth fingerprint"}' >"${COLLECT_DIR}/auth/fingerprint.json"
  fi
else
  echo '{"auth_json":"missing"}' >"${COLLECT_DIR}/auth/fingerprint.json"
fi
{
  echo "auth_json=${AUTH_STATUS}"
  echo "nous_mentioned=${NOUS_PRESENT}"
  echo "refresh_token_null=${RT_NULL}"
  echo "quarantine_or_revoke_hint=${QUARANTINE_HINT}"
} >"${COLLECT_DIR}/auth/summary.txt"

if [[ "${OS_NAME}" == "Darwin" ]]; then
  run_capture "${COLLECT_DIR}/launchd/list-hermes.txt" launchctl list
  grep -i hermes "${COLLECT_DIR}/launchd/list-hermes.txt" >"${COLLECT_DIR}/launchd/hermes-lines.txt" 2>/dev/null || true
  for label in ai.hermes.gateway com.hermes.gateway; do
    run_capture "${COLLECT_DIR}/launchd/print-${label}.txt" \
      launchctl print "gui/$(id -u)/${label}" || true
  done
  [[ -f "${HOME}/Library/LaunchAgents/ai.hermes.gateway.plist" ]] \
    && cp "${HOME}/Library/LaunchAgents/ai.hermes.gateway.plist" \
      "${COLLECT_DIR}/launchd/ai.hermes.gateway.plist" 2>/dev/null || true
  [[ -f "${HOME}/Library/LaunchAgents/com.hermes.gateway.plist" ]] \
    && cp "${HOME}/Library/LaunchAgents/com.hermes.gateway.plist" \
      "${COLLECT_DIR}/launchd/com.hermes.gateway.plist" 2>/dev/null || true
else
  echo "non-Darwin: launchd skipped" >"${COLLECT_DIR}/launchd/skipped.txt"
fi

{
  echo "=== probe 127.0.0.1:9130 ==="
  if have nc; then
    if nc -z -w 1 127.0.0.1 9130 >/dev/null 2>&1; then
      echo "port_9130=open"
    else
      echo "port_9130=closed"
    fi
  elif have python3; then
    python3 - <<'PY'
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", 9130))
    print("port_9130=open")
except Exception:
    print("port_9130=closed")
finally:
    s.close()
PY
  else
    echo "port_9130=unknown (no nc/python3)"
  fi
  if have lsof; then
    echo "=== lsof :9130 ==="
    lsof -nP -iTCP:9130 -sTCP:LISTEN 2>&1 || true
  fi
} >"${COLLECT_DIR}/ports/9130.txt"

PORT_9130="$(grep -E '^port_9130=' "${COLLECT_DIR}/ports/9130.txt" | tail -n1 | cut -d= -f2 || echo unknown)"

if [[ -d "${HERMES_HOME}/logs" ]]; then
  find "${HERMES_HOME}/logs" -type f \( -name '*.log' -o -name '*.txt' \) 2>/dev/null | head -n 40 | while read -r f; do
    base="$(basename "${f}")"
    safe_copy_tail "${f}" "${COLLECT_DIR}/logs/${base}"
    redact_file "${COLLECT_DIR}/logs/${base}"
  done
else
  echo "no ${HERMES_HOME}/logs" >"${COLLECT_DIR}/logs/missing.txt"
fi

if [[ "${OS_NAME}" == "Darwin" ]]; then
  DR="${HOME}/Library/Logs/DiagnosticReports"
  if [[ -d "${DR}" ]]; then
    find "${DR}" -type f \( -iname '*Hermes*' -o -iname '*hermes*' \) 2>/dev/null | head -n 10 | while read -r f; do
      safe_copy_tail "${f}" "${COLLECT_DIR}/crashes/$(basename "${f}")"
    done
  fi
  if [[ -d "${HOME}/Library/Logs" ]]; then
    find "${HOME}/Library/Logs" -maxdepth 3 -type f \( -iname '*Hermes*' -o -iname '*hermes*' \) 2>/dev/null | head -n 15 | while read -r f; do
      safe_copy_tail "${f}" "${COLLECT_DIR}/crashes/app-$(basename "${f}")"
      redact_file "${COLLECT_DIR}/crashes/app-$(basename "${f}")"
    done
  fi
fi

# Classify + write plan-pipe reports via helper
export INCIDENT_ID HOST_SHORT TS OS_NAME ARCH_NAME HERMES_HOME HERMES_BIN
export AUTH_STATUS NOUS_PRESENT RT_NULL QUARANTINE_HINT PORT_9130
export COLLECT_DIR REPORT_DIR DESKTOP
python3 "${SCRIPT_DIR}/lib/build_report.py"

# Sidecar copies on Desktop for easy plan paste (no unzip required)
cp "${REPORT_DIR}/report.md" "${DESKTOP}/${INCIDENT_ID}.report.md"
cp "${REPORT_DIR}/report.json" "${DESKTOP}/${INCIDENT_ID}.report.json"

note "writing ${OUT_ZIP}"
if have zip; then
  (cd "${STAGING}" && zip -r -q "${OUT_ZIP}" collect report)
else
  OUT_ZIP="${DESKTOP}/${INCIDENT_ID}.tar.gz"
  (cd "${STAGING}" && tar -czf "${OUT_ZIP}" collect report)
fi

PRIMARY="$(python3 -c 'import json; print(json.load(open("'"${REPORT_DIR}/report.json"'")).get("primary_hypothesis") or "unclassified")' 2>/dev/null || echo unclassified)"
cat >"${DESKTOP}/${INCIDENT_ID}.manifest.json" <<EOF
{
  "incident_id": "${INCIDENT_ID}",
  "archive": "$(basename "${OUT_ZIP}")",
  "report_md": "${INCIDENT_ID}.report.md",
  "report_json": "${INCIDENT_ID}.report.json",
  "collected_at_utc": "${TS}",
  "hostname": "${HOST_SHORT}",
  "primary_hypothesis": "${PRIMARY}",
  "plan_pipe_format": "hermes-desktop-ops/report-v1"
}
EOF

note "done"
note "archive: ${OUT_ZIP}"
note "report (plan feed): ${DESKTOP}/${INCIDENT_ID}.report.md"
note "report json: ${DESKTOP}/${INCIDENT_ID}.report.json"
printf '%s\n' "${OUT_ZIP}"
