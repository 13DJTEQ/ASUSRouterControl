#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

environment=""
release_id=""
deploy_root=""
service=""
env_file=""
restart_command=""
dry_run="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      [[ $# -ge 2 ]] || usage_error "Missing value for --env."
      environment="$2"
      shift 2
      ;;
    --release-id)
      [[ $# -ge 2 ]] || usage_error "Missing value for --release-id."
      release_id="$2"
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
    --restart-command)
      [[ $# -ge 2 ]] || usage_error "Missing value for --restart-command."
      restart_command="$2"
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
require_release_id "$release_id"
deploy_root="$(expand_path "${deploy_root:-$(default_deploy_root)}")"

release_dir="${deploy_root}/releases/${release_id}"
[[ -d "$release_dir" ]] || usage_error "Release '${release_id}' is not installed at '${release_dir}'."

env_dir="${deploy_root}/envs/${environment}"
current_file="${env_dir}/current_release"
previous_file="${env_dir}/previous_release"
env_file_record="${env_dir}/env_file"

if is_truthy "$dry_run"; then
  log_info "Dry-run: would activate release '${release_id}' in env '${environment}'."
  write_output "current_release" "$release_id"
  exit 0
fi

mkdir -p "$env_dir"
prior_release=""
if [[ -f "$current_file" ]]; then
  prior_release="$(<"$current_file")"
fi

if [[ -n "$prior_release" && "$prior_release" != "$release_id" ]]; then
  printf '%s\n' "$prior_release" > "$previous_file"
fi

printf '%s\n' "$release_id" > "$current_file"
if [[ -n "$env_file" ]]; then
  env_file="$(expand_path "$env_file")"
  printf '%s\n' "$env_file" > "$env_file_record"
fi

run_restart_hook "$service" "$restart_command"
log_info "Activated release '${release_id}' in env '${environment}'."
write_output "current_release" "$release_id"
