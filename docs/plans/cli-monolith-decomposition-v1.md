# Phase 4 Plan: CLI Monolith Decomposition

**Date:** 2026-06-16
**Scope:** `src/asusroutercontrol/cli.py` (3,893 lines, ~90 functions)
**Goal:** Decompose into a `cli/` package without breaking the `asusrouter` CLI entrypoint

---

## 1. Current State Analysis

`cli.py` is a single file mixing 14 distinct domains:

| Domain | Functions | Lines (approx) | Risk Level |
|---|---|---|---|
| DHCP | `dhcp_*`, `_profile_*`, `_run_profile_*` | ~400 | Medium |
| Incidents | `incident_*`, `_collect_incident_snapshot`, `_render_incident_snapshot` | ~300 | High (complex rollback) |
| Setup / Credentials | `setup`, `credentials_migrate`, `credentials_cleanup` | ~50 | Low |
| Device queries | `status`, `devices`, `monitor`, `traffic`, `history` | ~200 | Medium |
| Menubar | `menubar_*`, `_scoped_*`, `_guard_*` | ~200 | High (macOS plumbing) |
| Scheduler | `scheduler_*` | ~150 | Medium |
| SSH trust | `ssh_*`, `trust` | ~100 | Low |
| Speedtest / Trends | `speedtest`, `trends` | ~100 | Medium |
| Analysis | `analyze`, `config_history`, `config_snapshot` | ~150 | Medium |
| Dashboard | `dashboard` | ~150 | Medium |
| Reports | `report` | ~60 | Low |
| Optimization | `optimize_*` | ~350 | High (NVRAM changes) |
| Scripts | `scripts_*` | ~120 | Low |
| Entware | `entware_*` | ~120 | Low |

**Root problems:**
- No domain boundaries — changing DHCP affects the entire file's merge conflicts
- Test fixtures import from `cli.py` directly, creating tight coupling
- Rich console formatting mixed with business logic
- 3,893 lines exceeds cognitive load for single-file review

---

## 2. Proposed Target Architecture

```
src/asusroutercontrol/cli/
├── __init__.py          # Re-exports main CLI group (backward compat)
├── main.py              # Click group definition + shared options
├── core.py              # _get_backend(), console, table helpers, MAC normalization
├── dhcp.py              # dhcp_group + all dhcp_* commands
├── incidents.py         # incident_group + all incident_* commands
├── devices.py           # status, devices, monitor, traffic, history, client_load_diagnostics
├── menubar.py           # menubar_group + all menubar_* commands
├── scheduler.py         # scheduler_group + all scheduler_* commands
├── ssh_trust.py         # ssh_group + all ssh_* commands
├── speedtest.py         # speedtest, trends
├── analysis.py          # analyze, config_history, config_snapshot
├── dashboard.py         # dashboard command
├── reports.py           # report command
├── optimize.py          # optimize_group + all optimize_* commands
├── scripts.py           # scripts_group + all scripts_* commands
├── entware.py           # entware_group + all entware_* commands
└── setup.py             # setup, credentials_migrate, credentials_cleanup
```

**Shared utilities moved to `core.py`:**
- `_get_backend()` — router backend factory
- `console` — Rich console instance
- `_normalize_mac()` — MAC address normalization
- `_parse_live_event()` — log line parser
- `_diagnose_capture()` — capture state analyzer
- Table/rendering helpers (`_render_device_row`, `_profile_field`, etc.)

---

## 3. Migration Strategy: Incremental Extraction

**Phased approach (6 PRs, not 1 big-bang):**

### Phase 4.1 — Scaffold `cli/` package + extract `core.py`
- Create `cli/` directory
- Move shared utilities to `cli/core.py`
- Import them back into `cli.py` to maintain backward compat
- Update `pyproject.toml` entrypoint if needed
- **Validation:** `asusrouter --help` still works, all tests pass

### Phase 4.2 — Extract low-risk domains (Setup, SSH, Scripts, Entware)
- Extract `setup.py`, `ssh_trust.py`, `scripts.py`, `entware.py`
- Each gets its own Click group registered with main CLI
- **Validation:** `asusrouter setup`, `asusrouter ssh trust show`, `asusrouter scripts list`, `asusrouter entware status` all work

### Phase 4.3 — Extract device queries + speedtest
- Extract `devices.py`, `speedtest.py`
- **Validation:** `asusrouter status`, `asusrouter devices`, `asusrouter speedtest` all work

### Phase 4.4 — Extract DHCP + analysis
- Extract `dhcp.py`, `analysis.py`, `reports.py`
- **Validation:** `asusrouter dhcp show`, `asusrouter analyze`, `asusrouter report` all work

### Phase 4.5 — Extract menubar + scheduler
- Extract `menubar.py`, `scheduler.py`
- **Validation:** `asusrouter menubar status`, `asusrouter scheduler status` all work
- These touch macOS `launchd` — highest risk, so they get their own phase

### Phase 4.6 — Extract incidents + optimization
- Extract `incidents.py`, `optimize.py`
- **Validation:** `asusrouter incident snapshot`, `asusrouter optimize audit` all work
- Optimization commands modify NVRAM — highest risk, last to extract

### Phase 4.7 — Remove legacy `cli.py`
- Once all commands are imported from submodules, delete `cli.py`
- Make `cli/__init__.py` the sole entrypoint
- **Validation:** Full test suite + `make lint`

---

## 4. Testing Strategy

**Per-phase validation:**
1. `make lint` — must pass before any commit
2. `make test` — must pass before any commit
3. Manual smoke test: `asusrouter --help` + 3 commands from the extracted domain
4. `git diff --stat` reviewed for no unrelated changes

**Regression prevention:**
- Each extracted function keeps its original name and Click decorators
- No logic changes — pure file movement
- If a function needs a shared utility, import from `cli.core` (not from `cli.py`)

---

## 5. Rollback Plan

- Each phase is a separate commit on `consolidation/2026-06-14-main`
- If any phase fails validation, revert that commit: `git revert HEAD`
- `cli.py` remains untouched until Phase 4.7 — the original file is always the fallback
- No changes to `pyproject.toml` entrypoints until Phase 4.7

---

## 6. Acceptance Criteria (per phase)

| Phase | Criteria |
|---|---|
| 4.1 | `cli/` exists, `core.py` has utilities, `cli.py` still imports them, lint+tests pass |
| 4.2 | Setup/SSH/Scripts/Entware commands live in submodules, smoke tests pass |
| 4.3 | Device/Speedtest commands live in submodules, smoke tests pass |
| 4.4 | DHCP/Analysis/Report commands live in submodules, smoke tests pass |
| 4.5 | Menubar/Scheduler commands live in submodules, launchd smoke tests pass |
| 4.6 | Incident/Optimize commands live in submodules, NVRAM-safe smoke tests pass |
| 4.7 | `cli.py` deleted, `cli/__init__.py` is entrypoint, full validation passes |

---

## 7. Estimated Effort

| Phase | Estimated Lines Changed | Estimated Time |
|---|---|---|
| 4.1 | ~100 lines (scaffold + imports) | 30 min |
| 4.2 | ~400 lines | 1 hour |
| 4.3 | ~400 lines | 1 hour |
| 4.4 | ~600 lines | 1.5 hours |
| 4.5 | ~400 lines | 1.5 hours (macOS testing) |
| 4.6 | ~700 lines | 2 hours (NVRAM caution) |
| 4.7 | ~50 lines (cleanup) | 30 min |
| **Total** | **~2,650 lines** | **~8 hours** |

---

## 8. Open Questions

1. Should `cli.py` be preserved as `cli_legacy.py` for a deprecation period, or deleted immediately in Phase 4.7?
2. Should each submodule have its own test file, or should tests stay in `tests/test_cli.py` (current pattern)?
3. Should we extract the `_render_*` table helpers into a `cli/renderers.py` module, or keep them in `core.py`?
4. Is there any hidden coupling between `optimize.py` and `menubar.py` (e.g., shared rollout state)?

---

*Ready for review — please apply cost, risk, and integration-test lenses.*
