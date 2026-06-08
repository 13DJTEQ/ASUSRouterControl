# Pre-push hook setup and verification
## Goal
Install repository-local git hooks and verify pre-push validation runs `bash scripts/validate.sh`.
## Required assets
- `.githooks/pre-push`
- `scripts/pre-push-check.sh`
- `scripts/install-git-hooks.sh`
## Install hooks
Run from repository root:
- `bash scripts/install-git-hooks.sh`
- `git config --get core.hooksPath`
Expected value: `.githooks`
## Verify validation path
Manual check:
- `bash scripts/pre-push-check.sh`
Hook entrypoint check:
- `bash .githooks/pre-push`
Expected result for both: canonical validation completes successfully.
## Troubleshooting
- If tools are missing, install dev dependencies: `pip install -e ".[dev]"`.
- If hooks path is incorrect, rerun `bash scripts/install-git-hooks.sh`.
