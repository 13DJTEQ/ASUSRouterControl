# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Development commands

### Environment setup
- `make setup` — create `.venv` (Python 3.11), install editable package with dev+menubar extras, and fix hidden `site-packages` flags required on this macOS setup.
- `make dev` — reinstall editable package with dev+menubar extras.
- `pip install -e ".[dev]"` — minimal dev dependency install (used by CI and `scripts/validate.sh`).

### Validation, lint, tests
- `bash scripts/validate.sh` — canonical validation sequence: `ruff` + `pytest` + `compileall`.
- `make lint` — run Ruff on `src/`.
- `make test` — run full pytest suite.
- `python3 -m pytest tests/path/to/test_file.py::test_name` — run a single test.

### Running the app
- `asusrouter setup` — write router credentials to macOS Keychain (required before most commands).
- `asusrouter status` / `asusrouter devices` / `asusrouter monitor` — primary CLI entrypoints.
- `make run-menubar` (or `python -m asusroutercontrol.menubar`) — run the macOS menu bar app.

## Architecture overview

### Core shape
- The project is a Python package (`src/asusroutercontrol`) with two primary entrypoints:
  - CLI (`cli.py`, exposed as `asusrouter`)
  - Menu bar app (`menubar.py`, exposed as `asusroutermonitor`)
- Both entrypoints rely on shared config, credentials, backend adapters, SSH probes, and SQLite persistence.

### Runtime layers and responsibilities
- **Configuration & credentials**
  - `config.py` loads non-secret runtime config from env / `.env`.
  - `credentials.py` handles secure credential retrieval/storage via macOS Keychain (`universal-keychain-*` naming), with legacy migration helpers.
  - `_time.py` — use `from asusroutercontrol._time import utcnow` everywhere instead of `datetime.utcnow()` (deprecated in 3.12, returns naive datetimes).
- **Router access**
  - `backends/base.py` defines the `FirmwareBackend` contract and `BackendOperationUnsupported` exception.
  - `backends/factory.py` — **always use `create_backend(cfg, username=..., password=...)`** to instantiate a backend. Never instantiate `MerlinBackend` or `FreshTomatoBackend` directly outside the factory. The factory respects `ROUTER_BACKEND` env var.
  - `backends/merlin.py` uses the `asusrouter` API (read + selected write operations).
  - `backends/freshtomato.py` — constructor now accepts `hostname`, `username`, `password`, `ssh_port`. Read-only operations raise `NotImplementedError`; write operations raise `BackendOperationUnsupported`.
  - `ssh.py` provides async SSH execution plus host-key trust/pinning logic (`strict`, `tofu_confirm`, `tofu_auto`).
- **Persistence**
  - `datastore.py` is the central async SQLite layer (`router.db`) with schema creation, lightweight migrations, inserts, queries, retention pruning, and notification cooldown tracking. Call `await store.open()` to initialise (not `init_db`).
  - `models.py` defines shared Pydantic models used across backends, probes, scheduler, analysis, and reporting. All `timestamp` fields default to `utcnow` via `default_factory=utcnow`.

### Data collection and scheduling
- `scheduler.py` (`MonitorScheduler`) is the long-running orchestrator for background monitoring. It runs concurrent loops for:
  - scheduled multi-source speed tests,
  - SSH probes (latency/system/WiFi snapshots),
  - per-client traffic deltas,
  - device/traffic polling through backend API,
  - periodic config snapshots,
  - periodic recommendation generation,
  - daily retention pruning.
- Scheduler loops use timeouts, rollback on failed DB cycles, and backoff after repeated failures.
- The `_poll_loop` uses `create_backend` from `backends.factory` (no longer hardcodes `MerlinBackend` directly).

### Analysis, optimization, reporting pipeline
- **Probes** (`probes.py`) gather low-level router signals over SSH, including tracked NVRAM snapshots and diffs.
- **Speed tests** (`speedtest.py` + `speedtest_providers.py`) run multiple providers and compute a confidence-scored composite result.
- **Analysis** (`analyzer.py`, `analysis/*`) computes trends, mean shifts, anomalies, and SLA-oriented metrics from persisted telemetry.
- **Optimization/execution** (`optimizer.py`, `executor.py`, `rollout.py`) turns findings into suggested/applied NVRAM changes with whitelist safeguards, snapshots, and config-event recording. `rollout.py` uses `create_backend` via `_backend_from_local_config()`.
- **Reporting** (`reporting.py`) aggregates datastore windows into structured health reports and recommendation summaries.

### DHCP profiles
- Device shortcut profiles (MAC → IP → hostname mappings) live in `~/.asusroutercontrol/dhcp_profiles.toml`.
- CLI surface: `asusrouter dhcp profile {install,list,show}` — subgroup lives in `src/asusroutercontrol/cli/dhcp.py` (restored from the archived Claude variant during consolidation).
- `dhcp_profiles.py` handles loading (user TOML → `BUILTIN_PROFILES` fallback) and exposes `load_dhcp_profiles(data_dir)` plus `install_user_profiles(data_dir, overwrite=)`.
- The packaged example file is `src/asusroutercontrol/dhcp_profiles.example.toml`.
### Analysis CLI (dashboard)
- `asusrouter dashboard` lives in `src/asusroutercontrol/cli/analysis.py` (restored during consolidation).
- Backed by `analysis/dashboard.py` (`build_isp_client_dashboard`).
- Options: `--hours/-H`, `--clients`, `--timeline-points`, `--json`, `--export PATH`.
### Repository structure notes
- `archive/` (gitignored): contains the two pre-consolidation variants — `ASUSRouterControl_v1_archived/` (original baseline) and `ASUSRouterControl_claude_archived/` (Claude rebuild). Reference only; do not modify. Safe to delete once consolidation is verified.
- `logs/` (gitignored): runtime output from the menubar, optimizer, and launchd wrappers. Not tracked.
## Testing

### Test infrastructure
- `tests/conftest.py` — shared fixtures: `tmp_data_dir`, `datastore` (real SQLite on tmp file, initialised via `await store.open()`), `fake_backend` (FakeBackend implementing FirmwareBackend), `fake_ssh` (FakeSSH with NVRAM dict), `env_clean` (clears all env vars).
- `pytest-asyncio` in `asyncio_mode = "auto"` — all async test functions work without explicit `@pytest.mark.asyncio` decoration.
- `hypothesis` available for property-based tests (see `test_dhcp_reservations.py`).

### Current coverage
Tests exist for:
- `test_imports.py` — import smoke tests + factory contract
- `test_dhcp_reservations.py` — parse/upsert/remove/rollback + Hypothesis round-trip
- `test_credentials.py` — Keychain CRUD + legacy migration (in-memory keyring backend)
- `test_config.py` — env parsing, defaults, env-file override, type coercion

Coverage target: 60% short-term, 80% medium-term. Next priority modules: `datastore.py`, `optimizer.py`, `executor.py`, `rollout.py`.

## Repository-specific guardrails
- **Never instantiate `MerlinBackend` or `FreshTomatoBackend` directly.** Always use `backends.factory.create_backend`.
- **Never use `datetime.utcnow()`.** Use `from asusroutercontrol._time import utcnow` instead.
- Keep backend behaviour aligned with the `FirmwareBackend` contract; unsupported operations must raise `BackendOperationUnsupported` (not silent no-ops).
- Any change that affects telemetry collection should keep `datastore.py` schema/query compatibility in mind; this project relies heavily on longitudinal reads (trends, reports, recommendation cooldowns).
- Prefer extending existing scheduler/probe loops rather than creating ad-hoc collectors, so data retention, rollback, and failure-backoff behaviour remain consistent.
- Python 3.11 minimum; CI targets 3.11 and 3.12.
