#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

source_env=""
target_env=""
release_id=""
deploy_root=""
service=""
target_env_file=""
restart_command=""
dry_run="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-env)
      [[ $# -ge 2 ]] || usage_error "Missing value for --source-env."
      source_env="$2"
      shift 2
      ;;
    --target-env)
      [[ $# -ge 2 ]] || usage_error "Missing value for --target-env."
      target_env="$2"
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
    --target-env-file)
      [[ $# -ge 2 ]] || usage_error "Missing value for --target-env-file."
      target_env_file="$2"
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

[[ -n "$source_env" ]] || usage_error "--source-env is required."
[[ -n "$target_env" ]] || usage_error "--target-env is required."
deploy_root="$(expand_path "${deploy_root:-$(default_deploy_root)}")"
if [[ -n "$target_env_file" ]]; then
  target_env_file="$(expand_path "$target_env_file")"
fi

source_current_file="${deploy_root}/envs/${source_env}/current_release"
[[ -f "$source_current_file" ]] || usage_error "Source env '${source_env}' has no active release."
source_release="$(<"$source_current_file")"

if [[ -z "$release_id" ]]; then
  release_id="$source_release"
fi
require_release_id "$release_id"

if [[ "$source_release" != "$release_id" ]]; then
  usage_error "Immutable promotion violation: source env '${source_env}' is on '${source_release}', requested '${release_id}'."
fi

cmd=(
  bash "${SCRIPT_DIR}/activate_env.sh"
  --env "$target_env"
  --release-id "$release_id"
  --deploy-root "$deploy_root"
)
if [[ -n "$service" ]]; then
  cmd+=(--service "$service")
fi
if [[ -n "$target_env_file" ]]; then
  cmd+=(--env-file "$target_env_file")
fi
if [[ -n "$restart_command" ]]; then
  cmd+=(--restart-command "$restart_command")
fi
if is_truthy "$dry_run"; then
  cmd+=(--dry-run)
fi

"${cmd[@]}"
write_output "promoted_release" "$release_id"
log_info "Promoted release '${release_id}' from '${source_env}' to '${target_env}'."
