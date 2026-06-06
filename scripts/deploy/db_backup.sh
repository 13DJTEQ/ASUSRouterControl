#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

environment=""
release_id=""
db_path=""
deploy_root=""
allow_missing="true"
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
    --db-path)
      [[ $# -ge 2 ]] || usage_error "Missing value for --db-path."
      db_path="$2"
      shift 2
      ;;
    --deploy-root)
      [[ $# -ge 2 ]] || usage_error "Missing value for --deploy-root."
      deploy_root="$2"
      shift 2
      ;;
    --strict-missing-db)
      allow_missing="false"
      shift
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
suffix="${release_id:-manual}"
timestamp="$(now_utc)"
backup_root="${deploy_root}/backups/${environment}"
backup_file="${backup_root}/${timestamp}_${suffix}_router.db"
backup_present="false"

if is_truthy "$dry_run"; then
  log_info "Dry-run: would snapshot DB '${db_path}' to '${backup_file}'."
  write_output "backup_file" "$backup_file"
  write_output "backup_present" "false"
  exit 0
fi

mkdir -p "$backup_root"
if [[ -f "$db_path" ]]; then
  cp -p "$db_path" "$backup_file"
  backup_present="true"
  log_info "DB snapshot created at '${backup_file}'."
else
  if is_truthy "$allow_missing"; then
    backup_file="${backup_root}/${timestamp}_${suffix}_router.db.missing"
    printf 'db_path=%s missing at %s\n' "$db_path" "$timestamp" > "$backup_file"
    log_warn "DB path '${db_path}' does not exist; wrote missing-db marker '${backup_file}'."
  else
    usage_error "DB file '${db_path}' does not exist."
  fi
fi

write_output "backup_file" "$backup_file"
write_output "backup_present" "$backup_present"
