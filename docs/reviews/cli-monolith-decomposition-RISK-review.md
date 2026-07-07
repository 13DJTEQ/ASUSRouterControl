# Risk Review: CLI Monolith Decomposition Plan

**Reviewer:** RISK lens
**Date:** 2026-06-16
**Plan:** `/Volumes/2TB NvMe/MediaWave Development Projects/ASUSRouterControl/docs/plans/cli-monolith-decomposition-v1.md`
**Scope:** `src/asusroutercontrol/cli.py` (3,893 lines)

---

## Executive Summary

| Overall Recommendation | **BLOCK with conditions** |
|---|---|
| Risk severity | **High** |
| Confidence | **High** (based on direct code inspection) |
| Blocking issues | 3 Critical, 5 High |

The decomposition is well-motivated but the plan understates several structural dangers. Most critically: the entrypoint transition (Phase 4.7) is a single hard cutover with no graceful degradation, test imports from `cli.py` are not addressed incrementally, and the Rich console / Table rendering is so deeply interleaved with business logic that "pure file movement" is not achievable in practice.

---

## 1. Critical Risks (Will Likely Cause Failure)

### 1.1 Entrypoint Hard Cutover — Phase 4.7 Is a Single Point of Failure

**Finding:** `pyproject.toml` line 34 defines:
```toml
asusrouter = "asusroutercontrol.cli:cli"
```

The plan states: "No changes to `pyproject.toml` entrypoints until Phase 4.7" and then "delete `cli.py`, make `cli/__init__.py` the sole entrypoint."

**Risk:** This is an atomic, non-incremental change. If `cli/__init__.py` has an import error, circular import, or missing re-export, the `asusrouter` CLI becomes completely non-functional. There is no A/B or fallback path between `cli.py` (module) and `cli/` (package) because they cannot coexist as importable names.

**Gap in plan:** No intermediate step where both `cli.py` and `cli/__init__.py` are validated in parallel.

**Recommendation:** Before Phase 4.7, create a `cli/__main__.py` or temporary entrypoint script and run `python -m asusroutercontrol.cli` to validate the package works *before* changing `pyproject.toml`.

---

### 1.2 Test Imports Are Not Addressed Incrementally

**Finding:** 6 test files import directly from `cli.py`:

| Test File | Import |
|---|---|
| `tests/test_optimize_benchmark.py` | `from asusroutercontrol.cli import cli` |
| `tests/test_runtime_isolation.py` | `from asusroutercontrol.cli import _scoped_launchd_label` |
| `tests/test_dhcp_reservations.py` | `from asusroutercontrol.cli import cli` |
| `tests/test_incident_workflow.py` | `from asusroutercontrol.cli import cli` |
| `tests/test_dashboard_cli.py` | `from asusroutercontrol.cli import cli` |
| `tests/test_speedtest_cli.py` | `from asusroutercontrol.cli import cli` |

**Risk:** The plan says "`cli.py` remains untouched until Phase 4.7" and "no logic changes." However, tests import `cli` from the *module* `cli.py`. If during Phase 4.1 you add `cli/` as a package, Python's import semantics mean `asusroutercontrol.cli` could resolve to the *package* `cli/__init__.py` rather than the *module* `cli.py`, depending on filesystem ordering. This is non-deterministic across Python versions and installation methods (editable vs. installed).

**Gap in plan:** No mention of updating test imports per phase. The tests will silently test the wrong code or fail with `ImportError` during the transition.

**Recommendation:** Phase 4.1 must include updating all test imports to point to the new canonical location. Maintain a checklist.

---

### 1.3 Dual-Maintenance Shim Is a Merge-Conflict Magnet

**Finding:** During Phases 4.1–4.6, `cli.py` must simultaneously contain old code AND import re-exported symbols from the new `cli/` package. Any bugfix or feature addition to CLI commands during this window requires editing code in two places.

**Risk:** The plan's estimated "8 hours total" assumes no interruptions. In reality, decomposition spans multiple days. If another developer (or the same developer) fixes a bug in `cli.py` during Phase 4.3, that fix will be overwritten/lost when `cli.py` is eventually deleted in Phase 4.7 unless manually ported to the extracted module.

**Gap in plan:** No freeze policy on `cli.py` during the transition. No process for keeping shim and extracted code in sync.

**Recommendation:** Add a `DO_NOT_MODIFY` header to `cli.py` after Phase 4.1 and institute a freeze: all CLI changes must target the new `cli/` package.

---

## 2. High Risks (Likely to Cause Regressions)

### 2.1 Click `--help` Output Ordering Is Fragile

**Finding:** Click group/command registration order determines `--help` output order. Currently all groups are defined in a single file in this order:
1. `dhcp`
2. `incident`
3. `setup`
4. `menubar`
5. `scheduler`
6. `scripts`
7. `optimize`
8. `ssh`
9. Various `@cli.command()` top-level commands

**Risk:** When extracting groups to separate files (e.g., `dhcp.py`, `menubar.py`), the import order in `main.py` or `__init__.py` dictates the registration order. A different import order will silently reorder `asusrouter --help` output, which may break any documentation, scripts, or muscle memory that relies on help ordering.

**Gap in plan:** No validation step explicitly checks `--help` output ordering against a snapshot.

**Recommendation:** In Phase 4.1, capture `asusrouter --help` output as a snapshot test. Every subsequent phase must pass this snapshot test.

---

### 2.2 Rich Console / Table Rendering Is NOT Separable

**Finding:** `cli.py` contains 100+ usages of `console.print()`, `Table()`, `Panel()`, etc. These are interleaved with business logic at the line level. For example:
- `_render_dhcp_apply_result()` (lines 211-237) both validates a result object AND builds a `Table` of NVRAM changes
- `_render_incident_snapshot()` (lines 834-913) both parses snapshot data AND renders multiple tables and panels
- `_render_device_row()` (lines 283-...) both formats device data AND applies Rich styling

**Risk:** The plan proposes moving "Table/rendering helpers" to `core.py`, but these functions are not pure renderers — they contain domain logic (MAC normalization, conditional formatting, color codes based on health status). Extracting them without splitting each function into "logic" and "render" halves is impossible without changing behavior.

**Gap in plan:** The claim "No logic changes — pure file movement" is **not achievable** for any command that uses Rich.

**Recommendation:** Acknowledge that some functions will need to be refactored (not just moved). Add a risk buffer of ~2 extra hours per high-Rich domain (incidents, DHCP, devices, optimize).

---

### 2.3 `_scoped_launchd_label` Has Hidden Test Dependency

**Finding:** `tests/test_runtime_isolation.py` imports `_scoped_launchd_label` directly from `cli.py` (line 5) and asserts its behavior (lines 12-13).

**Risk:** The plan proposes moving `_scoped_launchd_label` to `core.py`. When this happens, the test import breaks. Worse: this function is used by both `menubar` and `scheduler` commands in `cli.py`, so it is part of the shared utility layer.

**Gap in plan:** Not listed among the shared utilities to be moved to `core.py`, yet it is tested independently.

**Recommendation:** Explicitly include `_scoped_launchd_label` and `_scoped_launchd_plist_path` in the `core.py` extraction list. Update test imports in Phase 4.1.

---

### 2.4 Optimize Commands Touch Router NVRAM Without Rollback Guardrails During Extraction

**Finding:** `optimize_apply` (line ~3546) modifies router NVRAM directly. The plan marks this as "highest risk, last to extract" but provides no extraction-specific safety measures.

**Risk:** During Phase 4.6 extraction, if `optimize.py` fails to import a shared utility from `core.py` (e.g., `_get_backend()`), the command could fail midway through applying changes, leaving the router in a partially-configured state.

**Gap in plan:** No mention of a pre-extraction backup of current NVRAM state, or a smoke-test that runs `optimize_apply --dry-run` before any real extraction.

**Recommendation:** Before Phase 4.6, add a mandatory pre-step: run `asusrouter optimize apply --dry-run` and `asusrouter optimize audit` against a real router (or high-fidelity mock) to verify the extracted module's imports are complete.

---

### 2.5 No Circular Import Analysis Was Done

**Finding:** The plan asks "Is there any hidden coupling between `optimize.py` and `menubar.py`?" as an open question but doesn't answer it.

**Risk:** My search found that `menubar.py` does NOT import from `cli.py` — good. However, `cli.py` imports from `config`, `credentials`, `datastore`. The new `cli/core.py` would also need to import these. If any extracted module (e.g., `menubar.py` in the new `cli/` package) later needs to import from `cli.core`, and `cli.core` transitively imports from a module that imports from `menubar.py`, a circular import could be introduced.

**Gap in plan:** No static analysis of import graph before extraction.

**Recommendation:** Run `pydeps` or `pyreverse` on `src/asusroutercontrol/` before Phase 4.1 to visualize and baseline the import graph.

---

## 3. Medium Risks (Manageable but Need Attention)

### 3.1 Bisectability During 7-Phase Transition

**Risk:** With 7 phases over multiple commits, the codebase will be in a "transitional" state for an extended period. If a production bug is reported during Phase 4.4, bisecting through commits that have half-extracted CLI modules is extremely difficult.

**Gap in plan:** No branch strategy. The plan says "each phase is a separate commit on `consolidation/2026-06-14-main`" but doesn't specify whether intermediate phases are merged to `main`.

**Recommendation:** Do the decomposition on a feature branch (`refactor/cli-decomposition`). Only merge to `main` after Phase 4.7 is complete and validated. This preserves bisectability on `main`.

---

### 3.2 `console = Console()` Is Shared Mutable State

**Finding:** `console = Console()` is a module-level variable in `cli.py`. The plan proposes moving it to `core.py`.

**Risk:** If any extracted module modifies `console` properties (e.g., `console.width`, `console.quiet`), it affects all other modules. This is already true today because all code is in one file, but after extraction it becomes a cross-module hidden dependency.

**Recommendation:** After extraction, replace the global `console` with a `get_console()` factory or make it immutable.

---

### 3.3 `make lint` / `make test` May Not Cover Entrypoint Changes

**Finding:** The plan's per-phase validation says `make lint` and `make test` must pass. However, `pytest` tests the importable Python code, not the installed CLI entrypoint. An error in `pyproject.toml`'s `[project.scripts]` would not be caught by `pytest`.

**Gap in plan:** No explicit "install and run the entrypoint" test.

**Recommendation:** Add a validation step: `pip install -e . && asusrouter --help` in a fresh virtualenv per phase.

---

## 4. Low Risks (Note for Awareness)

### 4.1 Archive Directory Contains Duplicate Code

`archive/ASUSRouterControl_Claude/src/asusroutercontrol/cli.py` contains an old copy of `_scoped_launchd_label` and launchd logic. This is dead code but could confuse a developer grepping for usages.

### 4.2 `menubar.py` Duplicates Launchd Logic

`menubar.py` defines `_menubar_launchd_label()` (line ~199) while `cli.py` defines `_scoped_launchd_label()` (line 2029). They serve the same purpose. The decomposition could consolidate them into `core.py` — but the plan doesn't mention this.

---

## 5. Pass/Block Recommendations by Phase

| Phase | Recommendation | Blocking Conditions |
|---|---|---|
| **4.1** | **BLOCK** | Must add: import graph analysis, `--help` snapshot test, test import updates, `_scoped_launchd_label` in `core.py`, freeze notice on `cli.py` |
| **4.2** | **CONDITIONAL PASS** | Only after 4.1 blockers resolved. Validate with snapshot test. |
| **4.3** | **CONDITIONAL PASS** | Same as above. |
| **4.4** | **CONDITIONAL PASS** | Same as above. |
| **4.5** | **CONDITIONAL PASS** | Requires macOS launchd smoke test. Must run on real macOS. |
| **4.6** | **BLOCK** | Must add: NVRAM dry-run smoke test before extraction, rollback plan for partial NVRAM state. |
| **4.7** | **BLOCK** | Must add: parallel validation of `python -m asusroutercontrol.cli` before changing `pyproject.toml`. |

---

## 6. Missing Safeguards Checklist

Before any phase is approved, the following must exist:

- [ ] Snapshot test for `asusrouter --help` output (to detect ordering regressions)
- [ ] Updated test imports in all 6 test files that reference `cli.py`
- [ ] `cli.py` header comment: `## FROZEN — do not modify; edit cli/ submodules instead`
- [ ] Feature branch (`refactor/cli-decomposition`) — do not merge intermediate phases to `main`
- [ ] `_scoped_launchd_label` and `_scoped_launchd_plist_path` explicitly listed in `core.py`
- [ ] Pre-Phase 4.6 `optimize apply --dry-run` smoke test against real router or mock
- [ ] `pip install -e . && asusrouter --help` in clean venv as part of CI/per-phase validation
- [ ] Import graph baseline (`pydeps` or `pyreverse` output committed to repo)

---

## 7. Conclusion

The decomposition plan is directionally correct but treats the extraction as lower-risk than it is. The claim of "pure file movement, no logic changes" is contradicted by the deep entanglement of Rich rendering with domain logic. The entrypoint cutover in Phase 4.7 is a cliff with no safety net. The absence of test import updates and `--help` snapshot tests means regressions will be discovered late.

**My recommendation:** Block Phases 4.1 and 4.7 until the Missing Safeguards Checklist above is satisfied. The remaining phases can proceed sequentially on a feature branch once 4.1 blockers are cleared.
