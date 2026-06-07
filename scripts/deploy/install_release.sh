#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

release_id=""
artifact_ref=""
deploy_root=""
dry_run="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id)
      [[ $# -ge 2 ]] || usage_error "Missing value for --release-id."
      release_id="$2"
      shift 2
      ;;
    --artifact|--artifact-ref)
      [[ $# -ge 2 ]] || usage_error "Missing value for --artifact."
      artifact_ref="$2"
      shift 2
      ;;
    --deploy-root)
      [[ $# -ge 2 ]] || usage_error "Missing value for --deploy-root."
      deploy_root="$2"
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

require_release_id "$release_id"
[[ -n "$artifact_ref" ]] || usage_error "artifact_ref is required."
deploy_root="$(expand_path "${deploy_root:-$(default_deploy_root)}")"

release_dir="${deploy_root}/releases/${release_id}"
metadata_file="${release_dir}/metadata.env"

if [[ -d "$release_dir" ]]; then
  usage_error "Release directory already exists for '${release_id}' (immutable release IDs cannot be reused)."
fi

if is_truthy "$dry_run"; then
  log_info "Dry-run: would install release '${release_id}' from '${artifact_ref}' into '${release_dir}'."
  write_output "release_id" "$release_id"
  write_output "release_dir" "$release_dir"
  exit 0
fi

mkdir -p "${release_dir}/artifacts"
resolved_artifact="$artifact_ref"

if [[ -f "$artifact_ref" ]]; then
  artifact_name="$(basename "$artifact_ref")"
  cp -p "$artifact_ref" "${release_dir}/artifacts/${artifact_name}"
  resolved_artifact="${release_dir}/artifacts/${artifact_name}"
fi

{
  printf 'RELEASE_ID=%s\n' "$release_id"
  printf 'ARTIFACT_REF=%s\n' "$artifact_ref"
  printf 'RESOLVED_ARTIFACT=%s\n' "$resolved_artifact"
  printf 'INSTALLED_AT=%s\n' "$(now_utc)"
} > "$metadata_file"

touch "${release_dir}/.installed.ok"

log_info "Installed release '${release_id}' with artifact reference '${artifact_ref}'."
write_output "release_id" "$release_id"
write_output "release_dir" "$release_dir"
