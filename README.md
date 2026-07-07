# ASUSRouterControl

Management and analysis tool for all AsusWRT routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in Bitwarden via the `bw` CLI (default).
Alternate backends (1Password, macOS Keychain) are available via `ASUSROUTERCONTROL_CREDENTIAL_BACKEND`.
Set `ROUTER_BACKEND=merlin` (default) or `ROUTER_BACKEND=freshtomato` in `.env` to select firmware backend.

## Developer validation

```bash
pip install -e ".[dev]"
bash scripts/validate.sh
```

The validation script runs lint (`ruff`), tests (`pytest`), and syntax checks (`compileall`).
If required dev tools are missing, it exits with an actionable install command.
For app-level verification in each development cycle, run `make verify-dev-app` to rebuild the DEV app bundle, relaunch it, and perform a runtime smoke-check.

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
asusrouter aimesh status     # AiMesh health summary
asusrouter aimesh nodes      # List all mesh nodes with status
asusrouter aimesh topology   # Show node topology map
```

## Web Dashboard

Browse telemetry data in a browser via the built-in FastAPI dashboard.

### Quick start
```bash
pip install -e ".[web]"
asusrouter web                # starts on http://127.0.0.1:8080
asusrouter web --port 9090    # custom port
asusrouter web --reload       # dev mode with auto-reload
```

### API endpoints
- `GET /api/` — dashboard homepage (HTMX + Alpine.js)
- `GET /api/isp-performance?hours=24` — ISP speed test data
- `GET /api/client-load?hours=1` — client device load
- `GET /api/devices` — connected devices
- `GET /api/health?hours=24` — router health score (0–100, grade A–F)

### Docker
```bash
docker compose up -d          # build and run on port 8080
```
Mount your data directory to expose collected telemetry:
```yaml
volumes:
  - ~/.asusroutercontrol:/data
```

## Architecture

### Overview
ASUSRouterControl is a Python 3.11+ async-first application (~25K LOC source, ~6.3K LOC tests) with two primary entrypoints:
- **CLI** (`asusrouter`) — command-line interface for router management, diagnostics, and DHCP operations
- **Menu bar app** (`asusroutermonitor`) — macOS native menu bar app with live metrics display

### Runtime Layers

**Configuration & Credentials**
- `config.py` — frozen `Config` dataclass, env/.env driven, runtime-environment isolation (dev vs prod data dirs)
- `credentials.py` — pluggable backend registry (Bitwarden primary, 1Password and macOS Keychain fallbacks)

**Firmware Backends** (strategy pattern)
- `backends/base.py` — `FirmwareBackend` ABC with `BackendOperationUnsupported` exception
- `backends/merlin.py` — uses `asusrouter` library API (read + selected write operations)
- `backends/freshtomato.py` — SSH-driven, currently read-only for write operations
- `backends/factory.py` — selects backend via `ROUTER_BACKEND` env var

**SSH & Probes**
- `ssh.py` — async SSH with host-key trust modes (`strict`, `tofu_confirm`, `tofu_auto`)
- `probes.py` — NVRAM snapshots, WiFi/client telemetry, latency via SSH commands
- `aimesh.py` — AiMesh mesh network monitoring via `AsusData.AIMESH` and `AsusData.NODE_INFO`

**Persistence**
- `datastore.py` — async SQLite (`aiosqlite`) with schema migrations, retention pruning, notification cooldowns
- `models.py` — 20+ Pydantic models (Device, TrafficSnapshot, SpeedTestResult, WiFiSnapshot, ClientLoad, etc.)

**Scheduler** (`scheduler.py`)
- Long-running `MonitorScheduler` orchestrating concurrent loops: speedtests, SSH probes, client traffic deltas, device polling, config snapshots, recommendations, daily pruning
- Timeouts, rollback on failed DB cycles, backoff after repeated failures

**Analysis Pipeline**
- `analyzer.py` + `analysis/` — trends, anomalies, SLA metrics from persisted telemetry
- `speedtest.py` + `speedtest_providers.py` — multi-provider (Ookla, Cloudflare, CDN) with confidence-scored composite
- `optimizer.py` → `executor.py` → `rollout.py` — NVRAM optimization with whitelist safeguards, snapshots, config-event recording
- `reporting.py` — aggregates datastore windows into structured health reports

**CLI Decomposition** (in progress)
- `cli.py` (136K monolith) being split into `cli/` package (`core.py` = 14K extracted)
- `_cli_legacy.py` = archived monolith copy

### Dependencies
- **Core**: `asusrouter>=1.21`, `aiohttp`, `keyring`, `pydantic>=2.0`, `click`, `aiosqlite`, `rich`, `asyncssh`
- **Dev**: `pytest`, `pytest-asyncio`, `ruff`, `httpx`
- **Web**: `fastapi`, `uvicorn[standard]`, `jinja2`
- **Menubar**: `pyobjc-core`, `pyobjc-framework-cocoa`

## Market Validity Assessment

**Technical Strengths**
- Solid engineering: ~25K LOC with comprehensive test coverage, multi-backend architecture, CI/CD pipeline
- Feature-rich: device monitoring, WiFi telemetry, multi-provider speed tests, SSH probes, NVRAM optimization, DHCP management, client load analysis, incident rollback, channel surveys
- Production-ready: dev→prod deployment with rollback, self-hosted macOS runner, environment isolation

**Market Constraints**
- **Hardware scope**: Supports all AsusWRT routers via the `asusrouter` library; model auto-detected on connect
- **Firmware dependency**: Primary backend relies on `asusrouter` library for Merlin firmware; FreshTomato backend is read-only
- **Competitive pressure**: ASUS's newer routers (WiFi 6E/7) increasingly expose native APIs via mobile apps, eroding differentiation
- **Niche audience**: Viable for power users and home lab enthusiasts running Merlin firmware on legacy hardware

**Viability Assessment**
- ✅ **Personal/power-user tool**: Excellent fit for technical users who want programmatic router management, telemetry, and automation
- ✅ **Open-source community project**: Strong foundation for community contributions and feature extensions
- ⚠️ **Commercial product**: Not viable without pivoting to broader hardware support (WiFi 6E/7 mesh systems) and SaaS telemetry layer
- ⚠️ **Scalability**: Single-router focus; no multi-site or fleet management capabilities

**Strategic Recommendation**: Continue as a personal tool and open-source project. Commercial viability requires a cloud-based telemetry/management layer.

## Tested router models

- RT-AX86U
- RT-AX88U
- GT-AX6000
- ZenWiFi XD6

## Performance metrics source methodology

- **Backend API metrics**: Device inventory and router-level traffic snapshots come from the selected backend. On Merlin, these come from the `asusrouter` API datasets (`CLIENTS`, `NETWORK`) rather than Bandwidth Monitor page scraping.
- **Per-client throughput metrics**: High-frequency client tx/rx rates are derived from SSH probe byte-counter deltas (primarily `wl sta_info`) across sampling intervals.
- **WiFi and interface telemetry**: RSSI, channel, noise, client counts, and interface byte counters come from SSH commands (`wl`, `/proc/net/dev`) and are stored as time-series snapshots.
- **Internet performance metrics**: WAN speed and latency are measured with multi-provider active tests (Ookla, Cloudflare, and CDN HTTP providers), then combined into a confidence-scored composite.
- **Missing-data handling**: When firmware does not expose a metric (commonly some wired client tx/rx fields), the app records presence/placeholder rows instead of inventing synthetic values.
