#!/usr/bin/env bash
# hermes-desktop-monitor.sh — check-in / tail while collector or bootstrap runs.
# Safe for zsh callers: invoke with /bin/bash.
#
# One-shot check-in:
#   /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --once
#
# Follow live log:
#   /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --tail
#
# Refresh every 5s:
#   /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --watch
#
# Fresh download while collector runs — paste as ONE line:
#   curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/hermes-desktop-monitor.sh -o ~/Desktop/hermes-desktop-monitor.sh && chmod +x ~/Desktop/hermes-desktop-monitor.sh && /bin/bash ~/Desktop/hermes-desktop-monitor.sh --once
set -euo pipefail

DESKTOP="${HOME}/Desktop"
OPS="${DESKTOP}/hermes-desktop-ops"
LIVE_LOG="${OPS}/LIVE.log"
LIVE_STATUS="${OPS}/LIVE.status"
DESKTOP_LIVE_LOG="${DESKTOP}/hermes-collect.live.log"
MODE="once"
WATCH_SECS=5
TAIL_LINES=40

usage() {
  cat <<'EOF'
Usage: hermes-desktop-monitor.sh [--once|--tail|--watch] [--interval N] [--lines N]

  --once       Print one check-in snapshot (default)
  --tail       Follow LIVE.log / Desktop live log (Ctrl-C to stop)
  --watch      Re-print snapshot every N seconds (default 5)
  --interval N Watch refresh seconds
  --lines N    Tail this many log lines in snapshot (default 40)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once) MODE="once"; shift ;;
    --tail|-f) MODE="tail"; shift ;;
    --watch) MODE="watch"; shift ;;
    --interval) WATCH_SECS="$2"; shift 2 ;;
    --lines) TAIL_LINES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

note() { printf '%s\n' "$*"; }
hr() { printf '%s\n' "------------------------------------------------------------"; }

find_pids() {
  # Match collector-related processes via ps+grep so the search pattern is not
  # itself matched when this monitor or a wrapper shell embeds the regex.
  # Tokens split intentionally so this script's source does not self-match.
  local a b c d e
  a="hermes-desktop-archive"
  b="bootstrap-desktop-collector"
  c="hermes"" ""backup"
  d="hermes"" ""doctor"
  e="hermes"" ""gateway"
  ps -eo pid=,args= 2>/dev/null \
    | grep -E "${a}\\.sh|${b}\\.sh|${c}|${d}|${e}" \
    | grep -v 'hermes-desktop-monitor' \
    | grep -v 'grep -E' \
    || true
}

staging_dirs() {
  local roots=("${TMPDIR:-/tmp}" /tmp)
  local r
  for r in "${roots[@]}"; do
    [[ -d "${r}" ]] || continue
    find "${r}" -maxdepth 3 -type d -name 'hermes-incident-*' 2>/dev/null | head -n 20 || true
  done
  if [[ "$(uname -s)" == "Darwin" ]]; then
    find /var/folders -maxdepth 6 -type d -name 'hermes-incident-*' 2>/dev/null | head -n 20 || true
  fi
}

latest_live_log() {
  if [[ -f "${LIVE_LOG}" ]]; then
    echo "${LIVE_LOG}"
  elif [[ -f "${DESKTOP_LIVE_LOG}" ]]; then
    echo "${DESKTOP_LIVE_LOG}"
  else
    echo ""
  fi
}

snapshot() {
  local now
  now="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  hr
  note "Hermes Desktop collector monitor — ${now}"
  hr
  note "Desktop: ${DESKTOP}"
  note "Ops dir: ${OPS} $([[ -d "${OPS}" ]] && echo '[present]' || echo '[MISSING]')"

  note ""
  note "== Processes =="
  local procs
  procs="$(find_pids)"
  if [[ -n "${procs}" ]]; then
    note "${procs}"
  else
    note "no collector/bootstrap/hermes backup|doctor processes found"
  fi

  note ""
  note "== LIVE status file =="
  if [[ -f "${LIVE_STATUS}" ]]; then
    cat "${LIVE_STATUS}"
    note ""
    note "mtime: $(ls -la "${LIVE_STATUS}" 2>/dev/null | awk '{print $5,$6,$7,$8,$9}')"
  else
    note "no ${LIVE_STATUS} yet — collector may be older build or still downloading"
  fi

  note ""
  note "== Staging dirs =="
  local stages
  stages="$(staging_dirs | sort -u)"
  if [[ -n "${stages}" ]]; then
    note "${stages}"
    while IFS= read -r d; do
      [[ -n "${d}" ]] || continue
      note "--- ${d} ---"
      du -sh "${d}" 2>/dev/null || true
      find "${d}" -type f 2>/dev/null | head -n 25 || true
    done <<<"${stages}"
  else
    note "no hermes-incident-* staging dirs found under /tmp"
  fi

  note ""
  note "== Desktop incident outputs =="
  ls -lt "${DESKTOP}"/hermes-incident-* 2>/dev/null | head -n 20 || note "none yet"

  note ""
  note "== Recent log tail =="
  local log
  log="$(latest_live_log)"
  if [[ -n "${log}" ]]; then
    note "log: ${log}"
    tail -n "${TAIL_LINES}" "${log}" 2>/dev/null || true
  else
    note "no LIVE.log yet"
    note "Tip: if bootstrap is stuck on curl/tar, Activity Monitor / ps above is the signal."
  fi

  note ""
  note "== Quick hints =="
  if echo "${procs}" | grep -q 'hermes backup'; then
    note "Stuck on hermes backup is common on large ~/.hermes — wait or check disk I/O."
  fi
  if echo "${procs}" | grep -qE 'curl|tar '; then
    note "Bootstrap still downloading/extracting the ops tarball."
  fi
  if [[ -z "${procs}" ]] && ls "${DESKTOP}"/hermes-incident-*.report.md >/dev/null 2>&1; then
    note "No running collector + report present → likely FINISHED. Open newest *.report.md"
  fi
  if [[ -z "${procs}" ]] && ! ls "${DESKTOP}"/hermes-incident-*.report.md >/dev/null 2>&1; then
    note "No process and no report → may have failed early. Re-run bootstrap or check Terminal scrollback."
  fi
  hr
}

do_tail() {
  local log
  log="$(latest_live_log)"
  snapshot
  if [[ -z "${log}" ]]; then
    note "Waiting for LIVE.log to appear at:"
    note "  ${LIVE_LOG}"
    note "  or ${DESKTOP_LIVE_LOG}"
    note "Polling every 2s..."
    while [[ -z "${log}" ]]; do
      sleep 2
      log="$(latest_live_log)"
      local procs
      procs="$(find_pids)"
      [[ -n "${procs}" ]] && note "[pulse] ${procs}" || note "[pulse] no collector procs yet"
    done
  fi
  note "Following ${log} — Ctrl-C to stop"
  tail -n "${TAIL_LINES}" -F "${log}"
}

case "${MODE}" in
  once) snapshot ;;
  watch)
    while true; do
      clear 2>/dev/null || true
      snapshot
      sleep "${WATCH_SECS}"
    done
    ;;
  tail) do_tail ;;
esac
