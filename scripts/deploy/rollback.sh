#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

environment=""
deploy_root=""
service=""
env_file=""
db_path=""
backup_file=""
restart_command=""
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
    --db-path)
      [[ $# -ge 2 ]] || usage_error "Missing value for --db-path."
      db_path="$2"
      shift 2
      ;;
    --backup-file)
      [[ $# -ge 2 ]] || usage_error "Missing value for --backup-file."
      backup_file="$2"
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
deploy_root="$(expand_path "${deploy_root:-$(default_deploy_root)}")"
db_path="$(expand_path "${db_path:-$(default_db_path)}")"
if [[ -n "$env_file" ]]; then
  env_file="$(expand_path "$env_file")"
fi

env_dir="${deploy_root}/envs/${environment}"
current_file="${env_dir}/current_release"
previous_file="${env_dir}/previous_release"
failed_file="${env_dir}/failed_release"
env_file_record="${env_dir}/env_file"

[[ -f "$previous_file" ]] || usage_error "No previous release found for env '${environment}', rollback unavailable."
rollback_release="$(<"$previous_file")"
current_release=""
if [[ -f "$current_file" ]]; then
  current_release="$(<"$current_file")"
fi

if is_truthy "$dry_run"; then
  log_info "Dry-run: would roll back env '${environment}' from '${current_release}' to '${rollback_release}'."
  write_output "rolled_back_to" "$rollback_release"
  exit 0
fi

mkdir -p "$env_dir"
if [[ -n "$current_release" ]]; then
  printf '%s\n' "$current_release" > "$failed_file"
fi
printf '%s\n' "$rollback_release" > "$current_file"
if [[ -n "$env_file" ]]; then
  printf '%s\n' "$env_file" > "$env_file_record"
fi

if [[ -n "$backup_file" ]]; then
  if [[ -f "$backup_file" && "$backup_file" != *.missing ]]; then
    mkdir -p "$(dirname "$db_path")"
    cp -p "$backup_file" "$db_path"
    log_info "Restored DB from '${backup_file}' to '${db_path}'."
  else
    log_warn "Backup file '${backup_file}' is not a restorable DB snapshot; skipping DB restore."
  fi
fi

run_restart_hook "$service" "$restart_command"
write_output "rolled_back_to" "$rollback_release"
log_info "Rollback complete for env '${environment}' to release '${rollback_release}'."
