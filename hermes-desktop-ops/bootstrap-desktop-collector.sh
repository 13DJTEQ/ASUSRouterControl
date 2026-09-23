#!/usr/bin/env bash
# bootstrap-desktop-collector.sh
# One-shot: download hermes-desktop-ops onto ~/Desktop and run the collector.
# Safe for zsh/bash. No prior checkout required.
#
# Mac one-liner — paste as ONE line; do not paste comment lines:
#   curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && ~/Desktop/bootstrap-desktop-collector.sh
set -euo pipefail

REPO="${HERMES_OPS_REPO:-13DJTEQ/ASUSRouterControl}"
REF="${HERMES_OPS_REF:-cursor/hermes-desktop-ops-cfe4}"
DESKTOP="${HOME}/Desktop"
DEST="${DESKTOP}/hermes-desktop-ops"
LIVE_LOG="${DEST}/LIVE.log"
DESKTOP_LIVE_LOG="${DESKTOP}/hermes-collect.live.log"
LIVE_STATUS="${DEST}/LIVE.status"
TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"
RUN_COLLECT=1
SKIP_COLLECT=0

usage() {
  cat <<'EOF'
Usage: bootstrap-desktop-collector.sh [--skip-collect] [--ref BRANCH] [--repo OWNER/REPO]

Downloads hermes-desktop-ops onto ~/Desktop/hermes-desktop-ops and runs
hermes-desktop-archive.sh unless --skip-collect is set.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-collect) SKIP_COLLECT=1; RUN_COLLECT=0; shift ;;
    --ref) REF="$2"; TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"; shift 2 ;;
    --repo) REPO="$2"; TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

mkdir -p "${DESKTOP}" "${DEST}"

live_append() {
  local ts_local line
  ts_local="$(date '+%Y-%m-%d %H:%M:%S')"
  line="[${ts_local}] $*"
  printf '%s\n' "${line}" >>"${LIVE_LOG}" 2>/dev/null || true
  printf '%s\n' "${line}" >>"${DESKTOP_LIVE_LOG}" 2>/dev/null || true
}

status_set() {
  local phase="$1"
  shift || true
  {
    echo "phase=${phase}"
    echo "updated_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "pid=$$"
    echo "ref=${REF}"
    echo "repo=${REPO}"
    local kv
    for kv in "$@"; do
      printf '%s\n' "${kv}"
    done
  } >"${LIVE_STATUS}" 2>/dev/null || true
}

note() {
  printf '[bootstrap] %s\n' "$*"
  live_append "[bootstrap] $*"
}
warn() {
  printf '[bootstrap] WARN: %s\n' "$*" >&2
  live_append "[bootstrap] WARN: $*"
}
die() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  live_append "[bootstrap] ERROR: $*"
  status_set "error" "error=$*"
  exit 1
}

# Long-step runner with >PROGRESS_AFTER_SECS heartbeats.
run_progress() {
  local label="$1"
  shift
  note "START: ${label}"
  status_set "${label}" "state=running" "cmd=$*"
  local start="${SECONDS}"
  "$@" &
  local pid=$!
  local announced=0
  local rc=0
  while kill -0 "${pid}" 2>/dev/null; do
    local elapsed=$((SECONDS - start))
    if (( elapsed >= PROGRESS_AFTER_SECS )); then
      if (( announced == 0 )); then
        note "STILL RUNNING (>${PROGRESS_AFTER_SECS}s): ${label} — elapsed ${elapsed}s"
        announced=1
      else
        note "progress: ${label} still running — elapsed ${elapsed}s"
      fi
      status_set "${label}" "state=running" "elapsed_s=${elapsed}"
      sleep "${PROGRESS_EVERY_SECS}"
    else
      sleep 1
    fi
  done
  wait "${pid}" || rc=$?
  local elapsed=$((SECONDS - start))
  note "DONE: ${label} in ${elapsed}s rc=${rc}"
  status_set "${label}" "state=done" "elapsed_s=${elapsed}" "rc=${rc}"
  return "${rc}"
}

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

PROGRESS_AFTER_SECS="${HERMES_PROGRESS_AFTER_SECS:-20}"
PROGRESS_EVERY_SECS="${HERMES_PROGRESS_EVERY_SECS:-5}"

status_set "start"
note "live progress: steps >${PROGRESS_AFTER_SECS}s emit heartbeats to LIVE.log"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-ops-bootstrap.XXXXXX")"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

status_set "download" "url=${TARBALL_URL}"
note "fetching ${TARBALL_URL}"
if ! run_progress "download tarball" curl -fsSL "${TARBALL_URL}" -o "${TMP}/src.tgz"; then
  die "failed to download branch tarball. Check network / that branch ${REF} exists on ${REPO}."
fi

status_set "extract"
note "extracting"
run_progress "extract tarball" tar -xzf "${TMP}/src.tgz" -C "${TMP}"
SRC_DIR="$(find "${TMP}" -maxdepth 2 -type d -name 'hermes-desktop-ops' | head -n 1 || true)"
[[ -n "${SRC_DIR}" && -d "${SRC_DIR}" ]] || die "hermes-desktop-ops not found inside tarball"

status_set "install" "dest=${DEST}"
note "installing to ${DEST}"
mkdir -p "${DEST}"
# Prefer rsync; fall back to cp
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '*.zip' \
    --exclude 'hermes-incident-*' \
    --exclude 'LIVE.log' \
    --exclude 'LIVE.status' \
    "${SRC_DIR}/" "${DEST}/"
else
  # Remove previous tool files but keep incident outputs and live progress files
  find "${DEST}" -mindepth 1 -maxdepth 1 \
    ! -name 'hermes-incident-*' \
    ! -name 'LIVE.log' \
    ! -name 'LIVE.status' \
    -exec rm -rf {} + 2>/dev/null || true
  cp -R "${SRC_DIR}/." "${DEST}/"
fi

chmod +x "${DEST}/hermes-desktop-archive.sh" \
  "${DEST}/install-to-desktop.sh" \
  "${DEST}/bootstrap-desktop-collector.sh" \
  "${DEST}/hermes-desktop-monitor.sh" 2>/dev/null || true
[[ -f "${DEST}/hermes-desktop-repair.sh" ]] && chmod +x "${DEST}/hermes-desktop-repair.sh" || true
[[ -f "${DEST}/lib/build_report.py" ]] || die "missing lib/build_report.py after install"

# Also leave a launcher on Desktop for Finder double-click / Terminal drag
cp "${DEST}/bootstrap-desktop-collector.sh" "${DESKTOP}/bootstrap-desktop-collector.sh" 2>/dev/null || true
chmod +x "${DESKTOP}/bootstrap-desktop-collector.sh" 2>/dev/null || true
cp "${DEST}/hermes-desktop-monitor.sh" "${DESKTOP}/hermes-desktop-monitor.sh" 2>/dev/null || true
chmod +x "${DESKTOP}/hermes-desktop-monitor.sh" 2>/dev/null || true

note "installed OK: ${DEST}"
ls -la "${DEST}"

if [[ "${RUN_COLLECT}" -eq 1 && "${SKIP_COLLECT}" -eq 0 ]]; then
  status_set "collect" "script=${DEST}/hermes-desktop-archive.sh"
  note "running collector"
  # Prefer bash explicitly so zsh users are fine
  /bin/bash "${DEST}/hermes-desktop-archive.sh"
  note "collector finished — check Desktop for hermes-incident-* files"
  status_set "done" "collector=finished"
else
  status_set "done" "collector=skipped"
  note "skip-collect set; run: /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh"
fi
