# CI/CD operations runbook

## Scope
Operational procedure for CI validation, manual deploy promotion (`dev` -> `prod`), health verification, and rollback using:
- `.github/workflows/ci.yml`
- `.github/workflows/deploy.yml`
- `scripts/deploy/*.sh`

## Preconditions
- GitHub branch protection requires passing CI on `master`.
- GitHub environments are configured:
  - `dev` (optional approvals)
  - `prod` (required reviewer approval)
- A self-hosted macOS runner is online with labels `[self-hosted, macOS]`.
- Runner has absolute env-file paths prepared for `dev` and `prod`.

## CI gate (must pass before deploy)
1. Confirm latest commit has green `CI` workflow status.
2. Confirm all staged jobs passed: `lint`, `test`, `package-smoke`, then `validate`.
3. Stop if any check is red; do not proceed to deploy workflow.

## Deploy workflow (HITL)
Use GitHub UI (`Actions > Deploy > Run workflow`) or CLI:

```bash
gh workflow run deploy.yml \
  --ref master \
  -f release_id=<immutable-release-id> \
  -f artifact_ref=<artifact-path-or-uri> \
  -f deploy_root=<runner-local-root> \
  -f db_path=<runner-local-router-db-path> \
  -f service_name=<optional-launchd-label> \
  -f dev_env_file=<optional-abs-path> \
  -f prod_env_file=<optional-abs-path> \
  -f dev_health_command=<optional-command> \
  -f prod_health_command=<optional-command> \
  -f dry_run=true
```

### Promotion procedure
1. Run `Deploy` with `dry_run=true`; verify both jobs complete.
2. Re-run with `dry_run=false` using the exact same `release_id` and `artifact_ref`.
3. Approve `prod` environment gate when prompted.
4. Confirm `deploy_dev` then `deploy_prod` succeed.

## Verification checklist
- `deploy_dev` completed all steps: install -> DB snapshot -> activate -> health check.
- `deploy_prod` completed all steps: DB snapshot -> promote -> health check.
- If `service_name` was provided, launchd service is loaded and healthy.
- Health command (if configured) reports expected result.

## Rollback procedure
Automatic rollback is invoked on prod health failure in workflow.
Manual rollback command:

```bash
bash scripts/deploy/rollback.sh \
  --env prod \
  --deploy-root <runner-local-root> \
  --db-path <runner-local-router-db-path> \
  --backup-file <backup-file-from-db_backup-step> \
  --service <optional-launchd-label> \
  --env-file <optional-abs-path-to-prod-env>
```

## Go/No-Go criteria
### Go
- CI is green on the exact commit to promote.
- Dry-run deploy succeeded using target inputs.
- `prod` approval completed by authorized reviewer.
- Live deploy finished with passing prod health check.

### No-Go
- Any CI stage failed or was skipped.
- Runner unavailable or missing required labels.
- Env files/paths are unknown or inconsistent between dry-run and live run.
- Prod health check fails and rollback does not restore expected state.
