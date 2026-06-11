# ASUSRouterControl

Management and analysis tool for ASUS RT-AC68U routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in 1Password via the `op` CLI.
Set `ROUTER_BACKEND=merlin` (default) or `ROUTER_BACKEND=freshtomato` in `.env` to select firmware backend.

## Developer validation

```bash
pip install -e ".[dev]"
bash scripts/validate.sh
```

The validation script runs lint (`ruff`), tests (`pytest`), and syntax checks (`compileall`).
If required dev tools are missing, it exits with an actionable install command.
For app-level verification in each development cycle, run `make verify-test-app` to rebuild the TEST app bundle, relaunch it, and perform a runtime smoke-check.

## Memory Palace workflow
For day-to-day planning, implementation checkpoints, and review summaries, follow:
- `docs/skills/memory-palace/references/mcp-workflow.md`
- `docs/skills/memory-palace/references/trigger-samples.md`

This keeps durable task context discoverable across sessions and contributors.

## CI/CD pipeline

### CI (`.github/workflows/ci.yml`)
- Triggers on `push`, `pull_request`, and manual `workflow_dispatch` when workflow/source paths change.
- Runs staged jobs: `lint`, `test`, and `package-smoke` in parallel.
- Runs canonical validation (`bash scripts/validate.sh`) only after all staged jobs pass.
- Uses concurrency cancellation (`ci-${workflow}-${ref}`) to avoid stale duplicate runs.

### CD (`.github/workflows/deploy.yml`)
- Manual `workflow_dispatch` deployment that promotes a single built artifact from `dev` to `prod`.
- `deploy_dev` installs and validates the release in a dev-scoped launchd service.
- `deploy_prod` is environment-gated and promotes the exact validated release to prod.
- Includes rollback hook (`scripts/deploy/rollback.sh`) if prod health checks fail.
- Defaults to `dry_run: true` and requires explicit opt-out for live promotion.

### Required GitHub/release setup
- Protect the production branch (currently `master`) with required CI checks.
- Configure GitHub Environments:
  - `dev` (optional reviewer gate)
  - `prod` (required reviewers for approval gate)
- Register a self-hosted macOS runner with labels `[self-hosted, macOS]` for deployment jobs.
- Provide absolute runner-local paths for dev/prod env files when triggering `Deploy`.
- Use immutable `release_id` + `artifact_ref` values for promotion; do not rebuild between `dev` and `prod`.

See `docs/runbooks/cicd-operations.md` for an end-to-end promotion and rollback procedure.

### Local deployment helpers
```bash
bash scripts/deploy/install_release.sh --artifact dist/<artifact> --release-id <id>
bash scripts/deploy/activate_env.sh --env dev --release-id <id> --service com.asusroutercontrol.scheduler.dev --env-file /abs/path/dev.env
bash scripts/deploy/health_check.sh --env dev --service com.asusroutercontrol.scheduler.dev --env-file /abs/path/dev.env
bash scripts/deploy/promote_release.sh --source-env dev --target-env prod --release-id <id> --service com.asusroutercontrol.scheduler --target-env-file /abs/path/prod.env
bash scripts/deploy/rollback.sh --env prod --service com.asusroutercontrol.scheduler --env-file /abs/path/prod.env
```

## Usage

```bash
asusrouter status     # Router system info
asusrouter devices    # Connected devices
asusrouter dhcp show  # Current DHCP reservations
asusrouter dhcp health  # Assert required reservation mappings
asusrouter dhcp reserve-macpro --dry-run
asusrouter dhcp reserve-denon-second-port --dry-run
asusrouter monitor    # Continuous monitoring (Phase 2)
asusrouter live-dhcp-auth --mac AA:BB:CC:DD:EE:FF -s 120   # Live phone reconnect diagnosis
```

## Architecture

- **Firmware-agnostic**: Backend abstraction supports Merlin now, FreshTomato later
- **Backend selection**: `ROUTER_BACKEND` switches between Merlin and FreshTomato implementations
- **Secure credentials**: 1Password (`op` CLI) with keychain fallback migration support
- **SoundShield integration**: JSON export for network-aware audio device discovery
