#!/usr/bin/env bash
set -euo pipefail

log_info() {
  printf '[deploy][INFO] %s\n' "$*"
}

log_warn() {
  printf '[deploy][WARN] %s\n' "$*" >&2
}

log_error() {
  printf '[deploy][ERROR] %s\n' "$*" >&2
}

usage_error() {
  log_error "$1"
  exit 2
}

default_deploy_root() {
  printf '%s\n' "${DEPLOY_ROOT:-$HOME/.asusroutercontrol/deployments}"
}

default_db_path() {
  printf '%s\n' "${DB_PATH:-${DATA_DIR:-$HOME/.asusroutercontrol}/router.db}"
}

expand_path() {
  local input_path="$1"
  case "$input_path" in
    "~")
      printf '%s\n' "$HOME"
      ;;
    "~/"*)
      printf '%s\n' "$HOME/${input_path#~/}"
      ;;
    *)
      printf '%s\n' "$input_path"
      ;;
  esac
}

now_utc() {
  date -u +"%Y%m%dT%H%M%SZ"
}

require_release_id() {
  local release_id="${1:-}"
  [[ -n "$release_id" ]] || usage_error "release_id is required."
  [[ "$release_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$ ]] || usage_error "release_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$."
}

write_output() {
  local key="$1"
  local value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  fi
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

run_restart_hook() {
  local service="${1:-}"
  local restart_command="${2:-}"

  if [[ -n "$restart_command" ]]; then
    log_info "Running explicit restart command hook."
    bash -lc "$restart_command"
    return 0
  fi

  if [[ -n "$service" ]]; then
    if command -v launchctl >/dev/null 2>&1 && [[ "$(uname -s)" == "Darwin" ]]; then
      log_info "Restarting launchd service: $service"
      launchctl kickstart -k "gui/${UID}/${service}"
    else
      log_warn "Restart requested for service '$service', but launchctl/Darwin is unavailable."
    fi
  fi
}
