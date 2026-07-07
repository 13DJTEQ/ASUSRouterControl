# Integration-Test Lens Review: CLI Monolith Decomposition Plan

**Reviewer:** INTEGRATION-TEST lens  
**Date:** 2026-06-16  
**Plan:** `docs/plans/cli-monolith-decomposition-v1.md`  
**Codebase:** `/Volumes/2TB NvMe/MediaWave Development Projects/ASUSRouterControl`

---

## Summary

The plan is directionally sound but contains **multiple invented or unverified assumptions**. Most critically, it references a non-existent `Makefile`, under-counts shared utilities by ~60%, misses a deeply-nested Click group, and overlooks a private-function import from tests. These gaps will cause build and test failures during Phase 4.1 if not corrected.

---

## Detailed Findings

### 1. Build / Entrypoint (`pyproject.toml`) — ✅ CORRECT

| Plan Assumption | Live Codebase | Status |
|---|---|---|
| Entrypoint is `asusroutercontrol.cli:cli` | `pyproject.toml:34` → `asusrouter = "asusroutercontrol.cli:cli"` | ✅ Verified |
| `cli.py` should remain untouched until Phase 4.7 | Plan explicitly says this | ✅ Correct strategy |

No issues here.

---

### 2. `make lint` / `make test` — ❌ INVENTED ASSUMPTION

**Plan statement (lines 115-116):**
> "`make lint` — must pass before any commit"  
> "`make test` — must pass before any commit"

**Live codebase finding:**
- **No `Makefile` exists** anywhere in the repository (verified by `find` + grep).
- Actual lint/test commands in `pyproject.toml` dev dependencies are `ruff` and `pytest`.
- The COST review already flagged this; the plan author never corrected it.

**Impact:** Phase validation criteria reference non-existent commands. This will confuse reviewers and CI.

**Recommendation:** Replace `make lint` with `ruff check .` and `make test` with `pytest` in Sections 4 and 6.

---

### 3. Test Files Importing from `cli.py` — ⚠️ UNDER-REPORTED

**Plan assumption (line 32):**
> "Test fixtures import from `cli.py` directly, creating tight coupling"

**Live codebase finding:** 6 test files import from `cli.py`:

| File | Import | Line |
|---|---|---|
| `tests/test_dashboard_cli.py` | `from asusroutercontrol.cli import cli` | 7 |
| `tests/test_optimize_benchmark.py` | `from asusroutercontrol.cli import cli` | 9 |
| `tests/test_speedtest_cli.py` | `from asusroutercontrol.cli import cli` | 5 |
| `tests/test_incident_workflow.py` | `from asusroutercontrol.cli import cli` | 8 |
| `tests/test_dhcp_reservations.py` | `from asusroutercontrol.cli import cli` | 8 |
| `tests/test_runtime_isolation.py` | `from asusroutercontrol.cli import _scoped_launchd_label` | 5 |

**Critical detail:** `test_runtime_isolation.py` imports a **private function** (`_scoped_launchd_label`), not the `cli` group object. If this function is moved to `cli/core.py`, the test import **will break** unless `cli.py` re-exports it or the test is updated.

**Plan does not mention:**
- The private-function import.
- That tests import `cli` via `from asusroutercontrol.cli import cli` (not just fixtures).

**Recommendation:** Add Phase 0 task: audit all test imports, add re-export stubs in `cli.py` during transition, and update `test_runtime_isolation.py` to import from `cli.core` once extracted.

---

### 4. Cross-Module Coupling (`menubar.py`, `scheduler.py`, etc.) — ✅ NO HIDDEN COUPLING

**Plan open question (line 170):**
> "Is there any hidden coupling between `optimize.py` and `menubar.py` (e.g., shared rollout state)?"

**Live codebase finding:**
- `menubar.py` does **NOT** import from `cli.py` at all. It imports from `backends.factory`, `credentials`, `ssh`, `analysis.clients`, `config`, `datastore`, `notifications`, and `scheduler`.
- `scheduler.py` does **NOT** import from `cli.py`.
- **Zero** `src/asusroutercontrol/*.py` files import from `cli.py`.

**Status:** ✅ No cross-module coupling detected. The plan's concern about hidden coupling is unfounded in the current codebase.

---

### 5. Click Group Nesting — ⚠️ MISSES DEEP NESTING

**Plan assumption:** Lists 14 flat domains.

**Live codebase finding:** There is a **3-level nested group** in DHCP:

```python
# cli.py:448-458
@click.group()
def cli(): ...

@cli.group("dhcp")          # Level 1
def dhcp_group(): ...

@dhcp_group.group("profile")  # Level 2 (nested inside dhcp_group)
def dhcp_profile_group(): ...

@dhcp_profile_group.command("list")  # Level 3
def dhcp_profile_list(): ...
```

**Impact:** When extracting `dhcp.py`, the `dhcp_profile_group` must be registered to `dhcp_group`, not `cli`. The plan's flat architecture diagram does not show this nesting. If implemented naively as separate top-level modules, `asusrouter dhcp profile list` will break.

**Recommendation:** Update the architecture diagram and Phase 4.4 notes to explicitly preserve the `dhcp → profile` sub-group hierarchy.

---

### 6. Shared Utilities (`core.py`) — ❌ SIGNIFICANTLY UNDER-COUNTED

**Plan lists (lines 61-67):**
> `_get_backend()`, `console`, `_normalize_mac()`, `_parse_live_event()`, `_diagnose_capture()`, Table/rendering helpers

**Live codebase reality:** There are **at least 20 shared/private functions** that would need to move to `core.py` or stay in `cli.py` as re-exports. Here is the complete inventory from `cli.py`:

**Global objects:**
- `console = Console()` (line 27)
- `_DHCP_EVENT_RE` (line 28)
- `_AUTH_EVENT_RE` (line 33)

**Core backend / config helpers:**
- `_get_backend()` (line 40)
- `_normalize_mac(mac: str \| None)` (line 57) — **note:** `probes.py` has a *different* `_normalize_mac` (line 774) with signature `str -> str` and no validation. Collision risk if both move to core.
- `_parse_live_event(line)` (line 71)
- `_diagnose_capture(state)` (line 124)
- `_read_new_syslog_lines(ssh, last_line)` (async, line 106)

**DHCP profile helpers (used by multiple dhcp_* commands):**
- `_get_dhcp_profiles()` (line 181)
- `_get_profiles_for_display()` (line 192)
- `_render_dhcp_apply_result(result)` (line 211)
- `_profile_field(profile_key, field)` (line 239)
- `_profile_target(...)` (line 272)
- `_render_device_row(device)` (line 283)
- `_collect_device_match_rows(...)` (async, line 290)
- `_print_profile_device_match_summary(...)` (line 322)
- `_run_profile_reservation(...)` (line 351)
- `_run_profile_unreserve(...)` (line 402)

**Incident helpers:**
- `_collect_incident_snapshot()` (line 804)
- `_render_incident_snapshot(snapshot, classification)` (line 834)

**Runtime / launchd helpers (used by menubar + scheduler commands):**
- `_normalize_service_environment(environment)` (line 2020)
- `_scoped_launchd_label(base_label, environment)` (line 2029) — **imported by test_runtime_isolation.py**
- `_scoped_launchd_plist_path(base_label, environment)` (line 2034)
- `_validate_env_file(env_file)` (line 2040)
- `_guard_runtime_data_dir(cfg, *, runtime_env, context)` (line 2048)
- `_run_with_backend(coro_factory)` (async, line 1166)

**Plan under-counts by ~15 functions.** If only the 5 listed utilities are moved to `core.py`, extracted modules will fail to import.

**Recommendation:** Expand `core.py` scope in the plan to include all private helpers above, or create a `cli/helpers.py` for domain-specific private functions. Explicitly document the `_normalize_mac` name collision with `probes.py`.

---

### 7. Function Count — ⚠️ ROUGH ESTIMATE

**Plan says:** "~90 functions" (line 4, Scope).

**Live codebase:** 107 functions total:
- 104 `def` functions
- 3 `async def` functions (`_read_new_syslog_lines`, `_collect_device_match_rows`, `_run_with_backend`)

The "~90" is a lowball by ~15 functions. Not a blocker, but suggests the plan author did not do an exact count.

---

### 8. Domain Boundary Accuracy — ✅ MOSTLY CORRECT

The 14 domains listed in Section 1 map cleanly to the function names found in `cli.py`. No invented domains were detected. The function-to-domain assignments are reasonable.

One minor note: `client_load_diagnostics` is listed under "Device queries" in the plan. In the code it is a `@cli.command()` (line 1411) alongside `status`, `devices`, etc. This is consistent.

---

### 9. Rollback Plan — ⚠️ PARTIALLY UNVERIFIABLE

**Plan says (line 129-131):**
> "Each phase is a separate commit on `consolidation/2026-06-14-main`"  
> "`cli.py` remains untouched until Phase 4.7"

**Issue:** The branch `consolidation/2026-06-14-main` was not verified to exist in the repo at the time of this review (out of scope for grep, but worth noting). The strategy itself is sound.

---

## Risk Heatmap

| # | Issue | Severity | Phase Affected |
|---|---|---|---|
| 1 | `make lint` / `make test` do not exist | 🔴 High | Phase 4.1 (acceptance criteria) |
| 2 | Shared utilities under-counted by ~15 functions | 🔴 High | Phase 4.1 (core.py extraction) |
| 3 | `test_runtime_isolation.py` imports private `_scoped_launchd_label` | 🔴 High | Phase 4.1 (import breakage) |
| 4 | 3-level Click nesting (`dhcp → profile`) not documented | 🟡 Medium | Phase 4.4 (DHCP extraction) |
| 5 | `_normalize_mac` collides with `probes.py` version | 🟡 Medium | Phase 4.1 (naming conflict) |
| 6 | Function count is ~107, not ~90 | 🟢 Low | Estimation only |

---

## Recommendations

1. **Fix build commands:** Replace all references to `make lint` and `make test` with `ruff check .` and `pytest`.
2. **Expand `core.py` inventory:** Update Section 2 to include all 20+ shared/private functions identified above.
3. **Handle `_normalize_mac` collision:** Either rename the CLI version (e.g., `_normalize_and_validate_mac`) or keep the probes.py version separate.
4. **Document Click nesting:** Add `dhcp_profile_group` to the architecture diagram as a child of `dhcp_group`.
5. **Add test-import audit to Phase 4.1:** Ensure `test_runtime_isolation.py` is updated or a backward-compat re-export is added.
6. **Verify branch existence:** Confirm `consolidation/2026-06-14-main` exists before starting Phase 4.1.

---

## Files Referenced in this Review

- `docs/plans/cli-monolith-decomposition-v1.md` (the plan)
- `src/asusroutercontrol/cli.py` (3,893 lines, verified via grep)
- `src/asusroutercontrol/probes.py` (line 774, `_normalize_mac` collision)
- `src/asusroutercontrol/menubar.py` (zero imports from cli.py — verified)
- `src/asusroutercontrol/scheduler.py` (zero imports from cli.py — verified)
- `pyproject.toml` (entrypoint lines 33-34)
- `tests/test_dashboard_cli.py`
- `tests/test_optimize_benchmark.py`
- `tests/test_speedtest_cli.py`
- `tests/test_incident_workflow.py`
- `tests/test_dhcp_reservations.py`
- `tests/test_runtime_isolation.py` (imports `_scoped_launchd_label`)

---

*Review complete. Plan requires revision before execution.*
