#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

environment=""
deploy_root=""
service=""
env_file=""
health_command=""
timeout_seconds="30"
dry_run="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      [[ $# -ge 2 ]] || usage_error "Missing value for --env."
      environment="$2"
      shift 2
      ;;
    --deploy-root)
      [[ $# -ge 2 ]] || usage_error "Missing value for --deploy-root."
      deploy_root="$2"
      shift 2
      ;;
    --service)
      [[ $# -ge 2 ]] || usage_error "Missing value for --service."
      service="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || usage_error "Missing value for --env-file."
      env_file="$2"
      shift 2
      ;;
    --health-command)
      [[ $# -ge 2 ]] || usage_error "Missing value for --health-command."
      health_command="$2"
      shift 2
      ;;
    --timeout-seconds)
      [[ $# -ge 2 ]] || usage_error "Missing value for --timeout-seconds."
      timeout_seconds="$2"
      shift 2
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    *)
      usage_error "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$environment" ]] || usage_error "--env is required."
deploy_root="$(expand_path "${deploy_root:-$(default_deploy_root)}")"
if [[ -n "$env_file" ]]; then
  env_file="$(expand_path "$env_file")"
fi
env_dir="${deploy_root}/envs/${environment}"
current_file="${env_dir}/current_release"
[[ -f "$current_file" ]] || usage_error "No active release found for env '${environment}'."

release_id="$(<"$current_file")"
release_dir="${deploy_root}/releases/${release_id}"
[[ -d "$release_dir" ]] || usage_error "Release directory missing: '${release_dir}'."
[[ -f "${release_dir}/.installed.ok" ]] || usage_error "Release marker missing: '${release_dir}/.installed.ok'."

if [[ -n "$env_file" && ! -f "$env_file" ]]; then
  log_warn "Configured env-file '${env_file}' does not exist on this runner."
fi

if is_truthy "$dry_run"; then
  log_info "Dry-run: would run health checks for env '${environment}' and release '${release_id}'."
  write_output "checked_release" "$release_id"
  exit 0
fi

if [[ -n "$service" && "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
  launchctl print "gui/${UID}/${service}" >/dev/null
fi

if [[ -n "$health_command" ]]; then
  log_info "Running custom health command."
  if command -v timeout >/dev/null 2>&1; then
    timeout "$timeout_seconds" bash -lc "$health_command"
  else
    bash -lc "$health_command"
  fi
else
  log_info "No custom health command provided; release metadata checks passed."
fi

write_output "checked_release" "$release_id"
log_info "Health checks passed for env '${environment}' release '${release_id}'."
