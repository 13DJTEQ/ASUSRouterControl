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
- **Router access**
  - `backends/base.py` defines the firmware backend contract.
  - `backends/factory.py` selects backend from `ROUTER_BACKEND` (`merlin` or `freshtomato`).
  - `backends/merlin.py` uses the `asusrouter` API (read + selected write operations).
  - `backends/freshtomato.py` is SSH-driven and currently read-only for write operations.
  - `ssh.py` provides async SSH execution plus host-key trust/pinning logic (`strict`, `tofu_confirm`, `tofu_auto`).
- **Persistence**
  - `datastore.py` is the central async SQLite layer (`router.db`) with schema creation, lightweight migrations, inserts, queries, retention pruning, and notification cooldown tracking.
  - `models.py` defines shared Pydantic models used across backends, probes, scheduler, analysis, and reporting.

### Data collection and scheduling
- `scheduler.py` (`MonitorScheduler`) is the long-running orchestrator for background monitoring. It runs concurrent loops for:
  - scheduled multi-source speed tests,
  - SSH probes (latency/system/WiFi),
  - per-client traffic deltas,
  - device/traffic polling through backend API,
  - periodic config snapshots,
  - periodic recommendation generation,
  - daily retention pruning.
- Scheduler loops use timeouts, rollback on failed DB cycles, and backoff after repeated failures.
- Important implementation detail: `_poll_loop` currently instantiates `MerlinBackend` directly instead of using `backends.factory.create_backend`; `ROUTER_BACKEND` selection is respected in CLI paths, but not this scheduler poll path.

### Analysis, optimization, reporting pipeline
- **Probes** (`probes.py`) gather low-level router signals over SSH, including tracked NVRAM snapshots and diffs.
- **Speed tests** (`speedtest.py` + `speedtest_providers.py`) run multiple providers and compute a confidence-scored composite result.
- **Analysis** (`analyzer.py`, `analysis/*`) computes trends, mean shifts, anomalies, and SLA-oriented metrics from persisted telemetry.
- **Optimization/execution** (`optimizer.py`, `executor.py`, `rollout.py`) turns findings into suggested/applied NVRAM changes with whitelist safeguards, snapshots, and config-event recording.
- **Reporting** (`reporting.py`) aggregates datastore windows into structured health reports and recommendation summaries.

## Repository-specific guardrails
- Keep backend behavior aligned with `FirmwareBackend` operation support; unsupported operations should surface via `BackendOperationUnsupported` (or equivalent explicit failure), not silent no-ops.
- Any change that affects telemetry collection should keep `datastore.py` schema/query compatibility in mind; this project relies heavily on longitudinal reads (trends, reports, recommendation cooldowns).
- Prefer extending existing scheduler/probe loops rather than creating ad-hoc collectors, so data retention, rollback, and failure-backoff behavior remain consistent.
