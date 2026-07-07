# Phase 4 Plan v2: CLI Monolith Decomposition (Revised)

**Date:** 2026-06-16
**Scope:** `src/asusroutercontrol/cli.py` (3,893 lines, ~107 functions)
**Goal:** Decompose into a `cli/` package without breaking the `asusrouter` CLI entrypoint
**Previous:** `cli-monolith-decomposition-v1.md` (rejected by COST, RISK, and INTEGRATION-TEST reviewers)

---

## 1. What Changed from v1

| Dimension | v1 (Rejected) | v2 (Revised) |
|---|---|---|
| Phases | 7 serialized | 3 phases |
| Files | 16 | 6–8 |
| Wall-clock estimate | ~16 hrs | ~6 hrs |
| Entrypoint switch | End (Phase 4.7) — highest risk deferred | **First (Phase A)** — front-load discovery |
| Backward compat | 6-phase dual-maintenance shim | **Single-commit shim** → immediate delete |
| Shared utilities | 5 listed for `core.py` | **All ~20** documented with re-export stubs |
| Test strategy | Ignored | **Keep in place**, update imports only |
| `--help` ordering | No safeguard | **Snapshot test before any extraction** |
| optimize↔menubar coupling | Open question | **Pre-flight grep** before Phase C |

---

## 2. Current State (Verified by Integration-Test Reviewer)

- `cli.py`: 3,893 lines, ~107 functions, 14 domains
- `pyproject.toml` entrypoint: `asusrouter = "asusroutercontrol.cli:cli"`
- Test files importing from `cli.py`: 6 confirmed (`test_optimize_benchmark`, `test_runtime_isolation`, `test_dhcp_reservations`, `test_incident_workflow`, `test_dashboard_cli`, `test_speedtest_cli`)
- `_normalize_mac()` name collision: `probes.py` has its own `_normalize_mac()` (different implementation)
- 3-level Click nesting exists: `dhcp_group → dhcp_profile_group`
- No cross-module imports from `menubar.py` or `scheduler.py` into `cli.py`
- `make lint` and `make test` DO exist in the Makefile (correction from integration-test reviewer)

---

## 3. Target Architecture (6–8 files)

```
src/asusroutercontrol/cli/
├── __init__.py          # Click group + all command registrations
├── core.py              # ALL shared utilities (~20 functions)
├── dhcp.py              # dhcp_group + dhcp_profile_group (~400 lines)
├── devices.py           # status, devices, monitor, traffic, history, speedtest, trends, analysis, dashboard, reports (~600 lines)
├── incidents.py         # incident_group + snapshot/rollback (~300 lines)
├── optimize.py          # optimize_group + NVRAM commands (~350 lines)
└── management.py        # setup, credentials, ssh, scripts, entware, scheduler, menubar (~600 lines)
```

**Merged from v1's 16-file proposal:**
- `setup.py` → `management.py`
- `reports.py` → `devices.py`
- `scripts.py` + `entware.py` → `management.py`
- `ssh_trust.py` → `management.py`
- `dashboard.py` → `devices.py`
- `analysis.py` → `devices.py`
- `speedtest.py` → `devices.py`

---

## 4. Shared Utilities Inventory (for `core.py`)

Verified complete list (~20 functions, not the v1's claimed 5):

```python
# Backend / config
_get_backend() -> FirmwareBackend

# Console / rendering
console: Console
_render_device_row(device) -> None
_render_dhcp_apply_result(result) -> None
_profile_field(profile_key: str, field: str) -> str
_profile_target(...) -> ...
_print_profile_device_match_summary(...) -> None

# Data parsing / normalization
_normalize_mac(mac: str | None) -> str | None
_parse_live_event(line: str) -> dict | None
_diagnose_capture(state: dict) -> tuple[str, str]

# DHCP profiles
_get_dhcp_profiles() -> dict
_get_profiles_for_display() -> dict

# Incident rendering
_collect_incident_snapshot() -> dict
_render_incident_snapshot(snapshot, classification) -> None

# Environment / launchd helpers
_normalize_service_environment(environment: str) -> str
_scoped_launchd_label(base_label: str, environment: str) -> str
_scoped_launchd_plist_path(base_label: str, environment: str) -> Path
_validate_env_file(env_file: Path | None) -> Path | None
_guard_runtime_data_dir(cfg, *, runtime_env: str, context: str) -> None
```

**Re-export stubs in `cli.py` (Phase A only):**
All functions remain importable from `asusroutercontrol.cli` during the transition via `from .core import _normalize_mac, ...` etc.

---

## 5. Phase A — Scaffold + Entrypoint Switch (1–1.5 hours)

**Goal:** Create the package structure, move ALL shared utilities to `core.py`, switch the entrypoint, and make `cli.py` a re-export shim.

**Steps:**
1. **Snapshot `asusrouter --help`** → save output to `tests/snapshots/cli_help.txt`
2. Create `cli/` directory
3. Create `cli/core.py` with all ~20 shared utilities (copy-paste from `cli.py`)
4. Create `cli/__init__.py`:
   - Import `cli` Click group from `cli.py` (temporarily)
   - Import all commands from `cli.py` (temporarily)
   - This becomes the canonical entrypoint
5. Update `pyproject.toml`:
   ```toml
   [project.scripts]
   asusrouter = "asusroutercontrol.cli.__init__:cli"
   ```
6. Make `cli.py` a **re-export shim only**:
   ```python
   # cli.py — DEPRECATED: will be removed in Phase C
   from asusroutercontrol.cli.core import (
       _get_backend, console, _normalize_mac, ...
   )
   from asusroutercontrol.cli.__init__ import cli
   # ... all commands imported from their new homes
   ```
7. Update test imports:
   - `test_runtime_isolation.py`: change `from asusroutercontrol.cli import _scoped_launchd_label` → `from asusroutercontrol.cli.core import _scoped_launchd_label`
   - All other tests: change `from asusroutercontrol.cli import ...` → `from asusroutercontrol.cli.core import ...` or `from asusroutercontrol.cli.__init__ import ...`
8. **Validation gates:**
   - `asusrouter --help` output matches snapshot (byte-for-byte)
   - `make lint` clean
   - `make test` — 295 passed, 1 skipped
   - `python -c "from asusroutercontrol.cli import cli; print(cli)"` works

**Rollback:** `git revert HEAD` restores `cli.py` as the source of truth.

---

## 6. Phase B — Extract High-Risk Domains (2–2.5 hours)

**Goal:** Extract the three highest-churn domains: DHCP, Incidents, Optimize.

**Order matters:** These are independent — can be done in any order or parallel by multiple developers.

### B1. Extract `cli/dhcp.py` (~400 lines)
- Move all `dhcp_*` functions and `dhcp_group` / `dhcp_profile_group`
- Import shared utilities from `cli.core`
- Register group in `cli/__init__.py`
- **Validation:** `asusrouter dhcp show`, `asusrouter dhcp profile list`, `make test` (especially `test_dhcp_reservations.py`)

### B2. Extract `cli/incidents.py` (~300 lines)
- Move all `incident_*` functions and `incident_group`
- Import shared utilities from `cli.core`
- Register group in `cli/__init__.py`
- **Validation:** `asusrouter incident snapshot`, `make test` (especially `test_incident_workflow.py`)

### B3. Extract `cli/optimize.py` (~350 lines)
- Move all `optimize_*` functions and `optimize_group`
- Import shared utilities from `cli.core`
- Register group in `cli/__init__.py`
- **Safety:** Before extraction, run `asusrouter optimize audit --dry-run` to verify the command works
- **Validation:** `asusrouter optimize audit`, `make test` (especially `test_optimize_benchmark.py`)

**Validation gates (for all B1-B3):**
- `asusrouter --help` output still matches snapshot
- `make lint` clean
- `make test` — zero regressions
- No changes to router NVRAM during extraction (use `--dry-run` for optimize commands)

---

## 7. Phase C — Extract Remaining Domains + Cleanup (1.5–2 hours)

**Goal:** Extract everything else and delete `cli.py`.

**Pre-flight check (before starting):**
```bash
grep -r "from asusroutercontrol.cli import" src/ --include="*.py"
grep -r "import asusroutercontrol.cli" src/ --include="*.py"
# Should return only references in cli.py itself and test files
```

### C1. Extract `cli/devices.py` (~600 lines)
- Move: `status`, `devices`, `monitor`, `traffic`, `history`, `client_load_diagnostics`, `speedtest`, `trends`, `analyze`, `config_history`, `config_snapshot`, `dashboard`, `report`
- Import shared utilities from `cli.core`
- Register commands in `cli/__init__.py`

### C2. Extract `cli/management.py` (~600 lines)
- Move: `setup`, `credentials_migrate`, `credentials_cleanup`, `menubar_*`, `scheduler_*`, `ssh_*`, `scripts_*`, `entware_*`
- Import shared utilities from `cli.core`
- Register groups in `cli/__init__.py`

### C3. Delete `cli.py`
- Remove the shim
- Ensure `cli/__init__.py` is the sole entrypoint
- Update `pyproject.toml` to final form:
  ```toml
  [project.scripts]
  asusrouter = "asusroutercontrol.cli:cli"
  ```

### C4. Final validation
- `asusrouter --help` output matches snapshot
- `make lint` clean
- `make test` — 295 passed, 1 skipped
- `git diff --stat` reviewed — no unrelated changes

---

## 8. Test Strategy

**No test file reorganization.** Keep existing test files in `tests/`. Only update imports:

| Test File | Current Import | New Import |
|---|---|---|
| `test_optimize_benchmark.py` | `from asusroutercontrol.cli import optimize_audit` | `from asusroutercontrol.cli.optimize import optimize_audit` |
| `test_runtime_isolation.py` | `from asusroutercontrol.cli import _scoped_launchd_label` | `from asusroutercontrol.cli.core import _scoped_launchd_label` |
| `test_dhcp_reservations.py` | `from asusroutercontrol.cli import dhcp_show` | `from asusroutercontrol.cli.dhcp import dhcp_show` |
| `test_incident_workflow.py` | `from asusroutercontrol.cli import incident_snapshot` | `from asusroutercontrol.cli.incidents import incident_snapshot` |
| `test_dashboard_cli.py` | `from asusroutercontrol.cli import dashboard` | `from asusroutercontrol.cli.devices import dashboard` |
| `test_speedtest_cli.py` | `from asusroutercontrol.cli import speedtest` | `from asusroutercontrol.cli.devices import speedtest` |

---

## 9. Acceptance Criteria

| Phase | Criteria |
|---|---|
| A | `cli/` exists, `core.py` has all utilities, entrypoint switched, `cli.py` is shim, `--help` matches snapshot, lint+tests pass |
| B1 | DHCP commands live in `cli/dhcp.py`, smoke tests pass, `--help` matches snapshot |
| B2 | Incident commands live in `cli/incidents.py`, smoke tests pass, `--help` matches snapshot |
| B3 | Optimize commands live in `cli/optimize.py`, `--dry-run` smoke test passes, `--help` matches snapshot |
| C1 | Device/Speedtest/Analysis commands live in `cli/devices.py`, smoke tests pass |
| C2 | Management commands live in `cli/management.py`, smoke tests pass |
| C3 | `cli.py` deleted, `pyproject.toml` points to `cli:cli`, full validation passes |

---

## 10. Rollback Plan

- Each phase is a separate commit on `consolidation/2026-06-14-main`
- `cli.py` remains as a shim until Phase C3 — the original file is always recoverable via `git checkout HEAD~N -- src/asusroutercontrol/cli.py`
- If any phase fails validation: `git revert HEAD` and re-plan
- Feature branch alternative: `git checkout -b feat/cli-decomposition` for the entire sequence, merge only after Phase C3 passes

---

## 11. Estimated Effort

| Phase | Lines Changed | Est. Time |
|---|---|---|
| A | ~200 (scaffold + imports) | 1–1.5 hrs |
| B1 | ~400 | 45 min |
| B2 | ~300 | 45 min |
| B3 | ~350 | 1 hr |
| C1 | ~600 | 1 hr |
| C2 | ~600 | 1 hr |
| C3 | ~50 (cleanup) | 30 min |
| **Total** | **~2,500 lines** | **~6 hrs** |

---

## 12. Risks and Mitigations (from RISK Reviewer)

| Risk | Severity | Mitigation |
|---|---|---|
| `--help` output reordering | Medium | Snapshot test in Phase A; validate after every phase |
| Test import shadowing | Medium | Update imports in Phase A; no package/module name collision |
| Circular imports during move | Medium | Front-load entrypoint switch; shim validates import graph |
| NVRAM modification during optimize extraction | High | Mandatory `--dry-run` smoke test before touching optimize code |
| Dual-maintenance drift | Medium | Single-commit shim; deleted immediately in Phase C |
| `_normalize_mac` name collision | Low | `cli/core.py` uses `_normalize_mac`; `probes.py` keeps its own (verified no overlap) |

---

*Ready for v2 review — this plan addresses all BLOCK findings from COST, RISK, and INTEGRATION-TEST reviewers.*
