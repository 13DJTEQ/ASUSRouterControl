#!/usr/bin/env bash
# lib/live_progress.sh — shared LIVE.log / LIVE.status helpers for ops scripts.
# Source from hermes-desktop-ops scripts. Requires bash.
#
# Env:
#   HERMES_PROGRESS_AFTER_SECS  start heartbeats after this many seconds (default 20)
#   HERMES_PROGRESS_EVERY_SECS  heartbeat interval once past threshold (default 5)

# shellcheck disable=SC2034
HERMES_PROGRESS_AFTER_SECS="${HERMES_PROGRESS_AFTER_SECS:-20}"
HERMES_PROGRESS_EVERY_SECS="${HERMES_PROGRESS_EVERY_SECS:-5}"

_live_init() {
  _LIVE_DESKTOP="${HOME}/Desktop"
  _LIVE_OPS="${_LIVE_DESKTOP}/hermes-desktop-ops"
  _LIVE_LOG="${_LIVE_OPS}/LIVE.log"
  _LIVE_STATUS="${_LIVE_OPS}/LIVE.status"
  _LIVE_DESKTOP_LOG="${_LIVE_DESKTOP}/hermes-collect.live.log"
  _LIVE_PREFIX="${1:-ops}"
  mkdir -p "${_LIVE_DESKTOP}" "${_LIVE_OPS}"
  # Mirror to Desktop root for Finder visibility
  : >>"${_LIVE_LOG}"
  : >>"${_LIVE_DESKTOP_LOG}"
  # Soft-link Desktop live log to ops LIVE.log when missing/stale
  if [[ ! -e "${_LIVE_DESKTOP_LOG}" ]] || [[ -f "${_LIVE_DESKTOP_LOG}" ]]; then
    # Keep both as real files; append to both (Finder-friendly)
    true
  fi
  _LIVE_RUN_START="${SECONDS}"
}

_live_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

live_note() {
  local msg="$*"
  local line
  line="$(_live_ts) [${_LIVE_PREFIX:-ops}] ${msg}"
  printf '%s\n' "${line}"
  printf '%s\n' "${line}" >>"${_LIVE_LOG}" 2>/dev/null || true
  printf '%s\n' "${line}" >>"${_LIVE_DESKTOP_LOG}" 2>/dev/null || true
}

live_warn() {
  live_note "WARN: $*"
}

live_status() {
  # live_status key=val key=val ...
  {
    echo "updated_utc=$(_live_ts)"
    echo "run_elapsed_s=$((SECONDS - _LIVE_RUN_START))"
    echo "pid=$$"
    local kv
    for kv in "$@"; do
      echo "${kv}"
    done
  } >"${_LIVE_STATUS}.tmp" 2>/dev/null && mv "${_LIVE_STATUS}.tmp" "${_LIVE_STATUS}" 2>/dev/null || true
}

# Run a command, capture stdout/stderr to outfile, emit live progress if > threshold.
# Usage: live_run_capture "phase label" outfile cmd [args...]
live_run_capture() {
  local label="$1"
  local outfile="$2"
  shift 2
  live_note "START: ${label}"
  live_status "phase=${label}" "state=running" "cmd=$*"
  local start="${SECONDS}"
  (
    echo "\$ $*"
    "$@" 2>&1 || echo "EXIT:$?"
  ) >"${outfile}" 2>&1 &
  local pid=$!
  local announced=0
  while kill -0 "${pid}" 2>/dev/null; do
    local elapsed=$((SECONDS - start))
    if (( elapsed >= HERMES_PROGRESS_AFTER_SECS )); then
      if (( announced == 0 )); then
        live_note "STILL RUNNING (>${HERMES_PROGRESS_AFTER_SECS}s): ${label} — elapsed ${elapsed}s; heartbeats every ${HERMES_PROGRESS_EVERY_SECS}s"
        announced=1
      else
        live_note "progress: ${label} still running — elapsed ${elapsed}s"
      fi
      live_status "phase=${label}" "state=running" "elapsed_s=${elapsed}" "cmd=$*"
      sleep "${HERMES_PROGRESS_EVERY_SECS}"
    else
      sleep 1
    fi
  done
  wait "${pid}" || true
  local elapsed=$((SECONDS - start))
  if (( elapsed >= HERMES_PROGRESS_AFTER_SECS )); then
    live_note "DONE (long): ${label} finished in ${elapsed}s"
  else
    live_note "DONE: ${label} in ${elapsed}s"
  fi
  live_status "phase=${label}" "state=done" "elapsed_s=${elapsed}"
}

# Run without capturing to a file (bootstrap curl/tar), still with heartbeats.
# Usage: live_run "phase label" cmd [args...]
live_run() {
  local label="$1"
  shift
  live_note "START: ${label}"
  live_status "phase=${label}" "state=running" "cmd=$*"
  local start="${SECONDS}"
  "$@" &
  local pid=$!
  local announced=0
  local rc=0
  while kill -0 "${pid}" 2>/dev/null; do
    local elapsed=$((SECONDS - start))
    if (( elapsed >= HERMES_PROGRESS_AFTER_SECS )); then
      if (( announced == 0 )); then
        live_note "STILL RUNNING (>${HERMES_PROGRESS_AFTER_SECS}s): ${label} — elapsed ${elapsed}s"
        announced=1
      else
        live_note "progress: ${label} still running — elapsed ${elapsed}s"
      fi
      live_status "phase=${label}" "state=running" "elapsed_s=${elapsed}" "cmd=$*"
      sleep "${HERMES_PROGRESS_EVERY_SECS}"
    else
      sleep 1
    fi
  done
  wait "${pid}" || rc=$?
  local elapsed=$((SECONDS - start))
  live_note "DONE: ${label} in ${elapsed}s rc=${rc}"
  live_status "phase=${label}" "state=done" "elapsed_s=${elapsed}" "rc=${rc}"
  return "${rc}"
}

live_phase() {
  local n="$1"
  local total="$2"
  local name="$3"
  live_note "PHASE ${n}/${total}: ${name}"
  live_status "phase=${name}" "phase_n=${n}" "phase_total=${total}" "state=running"
}
