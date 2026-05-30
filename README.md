# ASUSRouterControl

Management and analysis tool for ASUS RT-AC68U routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in macOS Keychain.
Set `ROUTER_BACKEND=merlin` (default) or `ROUTER_BACKEND=freshtomato` in `.env` to select firmware backend.

## Developer validation

```bash
pip install -e ".[dev]"
bash scripts/validate.sh
```

The validation script runs lint (`ruff`), tests (`pytest`), and syntax checks (`compileall`).
If required dev tools are missing, it exits with an actionable install command.

## CI/CD pipeline

### CI (`.github/workflows/ci.yml`)
- Triggers on `pull_request` and `push` for `develop` and `master`.
- Runs canonical validation (`bash scripts/validate.sh`).
- Builds and uploads immutable package artifacts (`sdist` + wheel).
- Runs a macOS smoke job that imports the menubar runtime.

### CD (`.github/workflows/deploy.yml`)
- Manual `workflow_dispatch` deployment that promotes a single built artifact from `dev` to `prod`.
- `deploy_dev` installs and validates the release in a dev-scoped launchd service.
- `deploy_prod` is environment-gated and promotes the exact validated release to prod.
- Includes rollback hook (`scripts/deploy/rollback.sh`) if prod health checks fail.

### Required GitHub/release setup
- Create `develop` and protect both `develop` and `master` with required CI checks.
- Configure GitHub Environments:
  - `dev` (optional reviewer gate)
  - `prod` (required reviewers for approval gate)
- Register a self-hosted macOS runner with labels `[self-hosted, macOS]` for deployment jobs.
- Provide absolute runner-local paths for dev/prod env files when triggering `Deploy`.

### Local deployment helpers
```bash
bash scripts/deploy/install_release.sh --artifact dist/<artifact> --release-id <id>
bash scripts/deploy/activate_env.sh --env dev --release-id <id> --service scheduler --env-file /abs/path/dev.env
bash scripts/deploy/health_check.sh --env dev --service scheduler --env-file /abs/path/dev.env
bash scripts/deploy/promote_release.sh --source-env dev --target-env prod --release-id <id> --service scheduler --target-env-file /abs/path/prod.env
bash scripts/deploy/rollback.sh --env prod --service scheduler --env-file /abs/path/prod.env
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
- **Secure credentials**: macOS Keychain via `keyring` — no plaintext secrets
- **SoundShield integration**: JSON export for network-aware audio device discovery
