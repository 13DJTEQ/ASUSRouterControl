# COST Lens Review: CLI Monolith Decomposition Plan
**Reviewer:** Cost-efficiency lens  
**Target:** `/docs/plans/cli-monolith-decomposition-v1.md`  
**Date:** 2026-06-16  
**Verdict:** 🔴 **BLOCK — requires significant simplification before execution**

---

## 1. Executive Summary

The proposed 7-phase / 8-hour / 16-file decomposition is **over-engineered for the actual pain points**. It optimizes for file-count purity at the expense of developer time, review cycles, and merge-risk surface. A 6-file package executed in **2-3 phases** achieves ~80% of the architectural benefit with ~40% of the effort.

---

## 2. Critical Cost Findings

### 🔴 Finding A: 7 Phases Creates More Overhead Than Value
**Plan:** 6 incremental PRs + 1 cleanup PR, serialized.  
**Reality:** Phases 4.2, 4.3, and 4.4 are all low-to-medium risk extractions with zero interdependency. Serializing them burns developer time on context-switching, CI cycles, and review latency.

**Cost math:**
- 6 PRs × (review + CI + context-switch overhead) ≈ **12-16 hours of wall-clock time**, not 8.
- Each phase requires `git diff --stat` validation, smoke tests, and rollback discipline — multiplied by 6.

**Cheaper win:** Parallelize 4.2–4.4 into a single phase, or split across 2 developers. Bundle 4.5–4.6 together (menubar/scheduler and incidents/optimize are independent).

---

### 🔴 Finding B: 16 Files is Cognitive Overload Inversion
**Plan:** 15 domain-segregated submodules + `__init__.py`.  
**Reality:** Some proposed files are ~50–120 lines (`setup.py`, `reports.py`, `scripts.py`, `entware.py`, `ssh_trust.py`). A 50-line module has MORE overhead (boilerplate, imports, docstring, test file decision) than value.

| Proposed File | Lines | Worth its own file? |
|---|---|---|
| `setup.py` | ~50 | ❌ No — merge with `core.py` or `devices.py` |
| `reports.py` | ~60 | ❌ No — merge with `analysis.py` |
| `scripts.py` | ~120 | ⚠️ Borderline — merge with `entware.py` into `tools.py` |
| `entware.py` | ~120 | ⚠️ Borderline — merge with `scripts.py` |
| `ssh_trust.py` | ~100 | ❌ No — merge with `setup.py` into `admin.py` |
| `dashboard.py` | ~150 | ⚠️ Could merge with `analysis.py` |

**Recommended 6-file target:**
```
cli/
├── __init__.py       # Entrypoint + re-exports
├── core.py           # Shared utils, console, _get_backend(), table helpers
├── devices.py        # status, devices, monitor, traffic, history, speedtest, trends
├── dhcp.py           # dhcp_* commands (~400 lines, highest conflict domain)
├── incidents.py      # incident_* commands (~300 lines, highest complexity)
├── optimize.py       # optimize_* commands (~350 lines, NVRAM risk)
├── management.py     # setup, ssh, scripts, entware, scheduler, menubar, reports, dashboard, analysis
```

This keeps high-churn domains isolated (DHCP, Incidents, Optimize) while avoiding the "file hunt" tax for the remaining ~2,200 lines of relatively stable code.

---

### 🔴 Finding C: Backward-Compat Import Strategy is Contradictory and Expensive
**Plan says:**
> "`cli.py` remains untouched until Phase 4.7"  
> AND  
> "Import them back into `cli.py` to maintain backward compat"

These are **mutually exclusive**. If `cli.py` re-exports from `cli/core.py`, it IS being modified. During phases 4.1–4.6, `cli.py` becomes a dual-maintenance shim: it must simultaneously contain the old code AND import from the new package. Any edit to the old code during this window requires editing both copies.

**Additional hidden cost:** 6 test files import directly from `asusroutercontrol.cli`:
- `test_optimize_benchmark.py`
- `test_runtime_isolation.py`
- `test_dhcp_reservations.py`
- `test_incident_workflow.py`
- `test_dashboard_cli.py`
- `test_speedtest_cli.py`

If `cli.py` is a shim, these tests work. But if someone edits the real function in `cli/dhcp.py` and forgets the shim is stale, tests pass against stale code. The plan doesn't account for this risk.

**Cheaper win:** Do a **single-commit shim switch**:
1. Create `cli/__init__.py` with the real entrypoint.
2. Make `cli.py` a 20-line re-export shim.
3. Update `pyproject.toml` to point to `cli.__init__:cli`.
4. Delete `cli.py` in the next commit.

This eliminates 6 phases of dual-maintenance risk.

---

### 🟡 Finding D: Entrypoint Switch is Scheduled Backwards
**Plan says:**
> "No changes to `pyproject.toml` entrypoints until Phase 4.7"

This is the **riskiest moment** (the switch from module to package) deferred to the end, when the most changes have accumulated. If the package import graph has a circular dependency or missing `__init__.py` export, you discover it only after 6 prior commits.

**Cheaper win:** Switch the entrypoint **in Phase 1**. Make `cli/__init__.py` the canonical source of truth immediately. `cli.py` becomes the shim. This front-loads risk discovery and simplifies every subsequent phase.

---

### 🟡 Finding E: Missing Test Migration Cost
**Open Question #2:** "Should each submodule have its own test file?"

This is not an open question — it's a **cost driver** that the plan ignores entirely.

- There are **31 test files** in `tests/`.
- At least **6** import directly from `cli.py`.
- Moving tests to `tests/cli/test_*.py` requires import updates, directory scaffolding, and potential `conftest.py` changes.

**Recommendation:** Keep tests where they are. Just update their imports from `asusroutercontrol.cli` to `asusroutercontrol.cli.dhcp` etc. Test file migration is a separate workstream with its own ROI — not a prerequisite for CLI decomposition.

---

### 🟡 Finding F: "No Logic Changes" is Misleading
**Plan says:**
> "No logic changes — pure file movement"

Moving functions between files in a Click CLI **always requires import changes**:
- `@dhcp.command()` decorators reference a `dhcp` group object that must now be imported.
- Shared utilities (`console`, `_get_backend()`) move to `core.py` and must be imported.
- Table rendering helpers (`_render_device_row`) may have hidden dependencies on local closures.

These are not "logic changes" in the algorithmic sense, but they are **logic changes in the import graph**, and they introduce real risks (circular imports, namespace collisions).

**Recommendation:** Budget 20% extra time for import-debugging. Call it "mechanical refactoring with import graph risk."

---

### 🟡 Finding G: Open Question #4 is a Pre-Requisite, Not a Post-Script
**Open Question #4:** "Is there any hidden coupling between optimize.py and menubar.py?"

This question existing at all means the **decomposition boundaries may be wrong**. If optimize and menubar share rollout state or NVRAM-guard logic, splitting them into separate files creates coupling via imports that is harder to see than when they coexist in one file.

**Recommendation:** BLOCK extraction of optimize and menubar until this coupling is mapped. Do a 30-minute `grep` analysis for shared variables, cross-function calls, and shared state before committing to file boundaries.

---

### 🟡 Finding H: Validation Commands Assume Non-Existent Tooling
**Plan says:**
> "`make lint` — must pass before any commit"  
> "`make test` — must pass before any commit"

**No Makefile exists** in the repository. The actual commands are `ruff check .` and `pytest`. This suggests the plan author hasn't validated the local build environment.

**Impact:** Low, but indicates the estimate lacks ground-truth. Actual lint/test commands may have different flags or paths.

---

## 3. Comparative Cost Analysis

| Approach | Files | Phases | Est. Wall-Clock | Merge-Conflict Risk | Cognitive Load |
|---|---|---|---|---|---|
| **Proposed (v1)** | 16 | 7 | ~16 hrs | Medium (6-commit window) | High (file sprawl) |
| **Recommended (minimal)** | 6 | 2–3 | ~6 hrs | Low (fast completion) | Medium (clear boundaries) |
| **Recommended (balanced)** | 8 | 4 | ~10 hrs | Low | Medium |

---

## 4. Pass / Block / Modify Recommendations

| Item | Status | Rationale |
|---|---|---|
| **Overall plan** | 🔴 **BLOCK** | Over-engineered; 80/20 win ignored |
| **Phase 4.1 (Scaffold)** | 🟡 **MODIFY** | Switch entrypoint FIRST; make `cli.py` the shim, not the source |
| **Phases 4.2–4.4 (Low-risk)** | 🟡 **MERGE** | Combine into 1 phase; domains are independent |
| **Phase 4.5 (Menubar/Scheduler)** | 🟡 **MERGE** | Combine with 4.6; only macOS-testing justifies its own phase, not file count |
| **Phase 4.6 (Incidents/Optimize)** | 🟡 **MODIFY** | Resolve Open Question #4 first; if coupling exists, merge into shared module |
| **Phase 4.7 (Cleanup)** | 🟢 **KEEP** | But do it immediately after Phase 1 (single-commit shim + next-commit delete) |
| **16-file package** | 🔴 **REJECT** | Reduce to 6–8 files; merge sub-150-line modules |
| **Test file migration** | 🟡 **DEFER** | Update imports only; don't reorganize test directory |
| **Backward-compat shim** | 🟢 **KEEP** | But implement as 1-commit switch, not 6-phase dual-maintenance |

---

## 5. Recommended Rewritten Plan

### Phase A — Scaffold + Entrypoint Switch (1–1.5 hrs)
1. Create `cli/__init__.py` with the `cli` Click group.
2. Move `_get_backend()`, `console`, `_normalize_mac()`, `_parse_live_event()`, and table helpers to `cli/core.py`.
3. Update `pyproject.toml` to `asusrouter = "asusroutercontrol.cli:cli"`.
4. Make `cli.py` a **re-export shim only** (import from `cli.core`, re-register commands temporarily).
5. Validate: `asusrouter --help`, `pytest`, `ruff check`.

### Phase B — Extract High-Value Domains (2–2.5 hrs)
1. Extract `cli/dhcp.py` (~400 lines, highest merge-conflict risk).
2. Extract `cli/incidents.py` (~300 lines, most complex rollback logic).
3. Extract `cli/optimize.py` (~350 lines, NVRAM-touching code).
4. Move commands into `cli/__init__.py` groups or keep as subgroups in their files.
5. Validate: smoke tests for each extracted domain.

### Phase C — Extract Remaining Domains (1.5–2 hrs)
1. Extract `cli/devices.py` (status, monitor, speedtest, trends, analysis, dashboard, reports).
2. Extract `cli/management.py` (setup, ssh, scripts, entware, scheduler, menubar).
3. Remove `cli.py` shim entirely.
4. Validate: full test suite + `ruff check`.

**Total: 4.5–6 hours** (vs. proposed 8 hours) with **3 phases** (vs. proposed 7) and **6 files** (vs. proposed 16).

---

## 6. Risk Assessment Summary

| Risk | Severity | Mitigation in Recommended Plan |
|---|---|---|
| Circular imports during move | Medium | Front-load entrypoint switch; shim validates import graph early |
| Test breakage | Medium | Keep tests in place; only update imports |
| Merge conflicts during multi-phase rollout | Low | Fewer phases = smaller window for upstream changes to `cli.py` |
| File-sprawl cognitive load | Low | 6 files vs. 16; boundaries map to actual team mental models |
| optimize↔menubar coupling | Unknown | Map with `grep` before Phase C; merge into `management.py` if coupled |

---

*Review complete. The plan should be rewritten to the 3-phase, 6-file recommendation before execution.*
