# ASUSRouterControl — Session Checkpoint (2026-06-15)
## Last updated: after Phase 3 validation + DEV/PROD coexistence investigation

---

## 1. What we did this session

### Phase 1 — Multi-agent exception refinement (parallel)
- Narrowed 185 `except Exception:` blocks across 9 files to specific types.
- Eliminated all silent `except Exception: pass` patterns.
- Files touched: `merlin.py`, `datastore.py`, `executor.py`, `menubar.py`, `probes.py`, `reporting.py`, `scheduler.py`, `speedtest_providers.py`, `ssh.py`.
- **Validation:** `make lint` clean; `pytest tests/ -x -q` passed.

### Phase 2 — SQLite + backend lifecycle + deprecation fixes (parallel)
- Enabled `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL` in `datastore.py`.
- Refactored `scheduler.py` `_poll_loop` to connect backend once before loop, disconnect in `finally`.
- Removed unbounded `self._perf_counts` dict; replaced with `deque(maxlen=120)`.
- Replaced deprecated `datetime.utcnow` in `models.py` with project `utcnow` factory.
- Added `PRAGMA optimize` on datastore close.
- **Validation:** `make lint` clean; `pytest tests/ -q` passed.

### Phase 3 — Schema migration robustness
- Replaced fragile blind `ALTER TABLE` in `_migrate()` with a `schema_version` table + 9 explicit numbered migrations.
- Updated `tests/test_scheduler_notifications.py` to remove `_perf_log_every` reference.
- Verified zero silent exception blocks remain.
- **Validation:** `make lint && pytest tests/ -q` passed (293 passed, 1 skipped).

### Side investigation — DEV/PROD menu bar coexistence
- Rebuilt DEV app (`make build-dev-app`) and verified (`make verify-dev-app`) successfully.
- Confirmed DEV and PROD already use distinct bundle identifiers (`com.asusroutermonitor` vs `ASUSRouterControlDevRuntime` vs `dev.mediawavetech.asusroutercontrol.dev` wrapper).
- **Finding:** macOS Sequoia 26.5 ControlCenter daemon rejects second status-bar scene registration when PROD is already running. This is a **system limitation**, not a code bug.
- Reverted `NSSquareStatusItemLength` → `NSVariableStatusItemLength` in `menubar.py` (line 280) because emoji/text prefixes need variable-length items.
- **Workaround for simultaneous testing:** Kill PROD before launching DEV, or rely on smoke-check telemetry (`make verify-dev-app`) which confirms DEV is functional even when icon is invisible.

---

## 2. Current repo state (git)

- Branch: `consolidation/2026-06-14-main` (ahead 7, behind 0 vs origin/master)
- Status: ~18 modified files from this session, plus 1 staged.
- Key diffs: `menubar.py`, `scheduler.py`, `datastore.py`, `models.py`, `probes.py`, `ssh.py`, `merlin.py`, `reporting.py`, `speedtest_providers.py`, `executor.py`, `cli.py`, `tests/test_scheduler_notifications.py`.
- **Lint:** Clean (`ruff check src/`)
- **Tests:** 293 passed, 1 skipped.

---

## 3. Hardware context

- Dual-slot Thunderbolt NVMe dock.
- Current 2TB (`/Volumes/2TB NvMe/`) houses dev projects + AI models.
- Second 2TB would provide: dedicated model storage, project isolation, backup target.
- Brand-matching less critical than enclosure chipset matching for dual-slot thermal/power consistency.

---

## 4. Deferred / not done

- CLI monolith refactor (`cli.py` ~3,888 lines) — untouched.
- DB covering indexes for menubar hot paths — not added.
- Speedtest composite error classification — not refined.
- SSH exponential backoff review — deferred.
- `make build-prod-dmg` not run (production build not rebuilt this session).

---

## 5. How to restart from here

### Fresh session quick-start:
```bash
# 1. Verify environment
cd "/Volumes/2TB NvMe/MediaWave Development Projects/ASUSRouterControl"
make lint
python3 -m pytest tests/ -q

# 2. If you need to rebuild DEV:
make build-dev-app
make verify-dev-app

# 3. If you need simultaneous DEV+PROD icons:
#   Option A: Kill PROD first: pkill -f "Asus Router Monitor"
#   Option B: Accept smoke-check verification without visible icon
```

### Key files to read if resuming:
- `src/asusroutercontrol/menubar.py` — line 280 (`NSVariableStatusItemLength`)
- `src/asusroutercontrol/scheduler.py` — `_poll_loop` backend lifetime
- `src/asusroutercontrol/datastore.py` — WAL mode + schema_version migrations
- `src/asusroutercontrol/models.py` — utcnow factory usage

---

## 6. Session skill saved

`~/.hermes/skills/multi-agent-refinement-workflow/SKILL.md` — documents the 3-phase parallel agent workflow, validation gates, and anti-patterns discovered.

---

Restart link: Load this checkpoint and run `make lint && pytest tests/ -q` to confirm state.
