#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_APP="${PROJECT_ROOT}/testbuilds/ASUSRouterControl TEST.app"
TEST_LAUNCHER="${TEST_APP}/Contents/MacOS/asusroutercontrol-launcher"
TEST_LAUNCH_LOG="${PROJECT_ROOT}/testbuilds/verify-test-app.launch.log"
TEST_DB_PATH="${HOME}/.asusroutercontrol.test/router.db"
TEST_LOG_PATH="${HOME}/.asusroutercontrol.test/scheduler.log"
TIMEOUT_SECONDS="${VERIFY_TEST_APP_TIMEOUT_SECONDS:-120}"
FRESHNESS_SECONDS="${VERIFY_TEST_APP_FRESHNESS_SECONDS:-180}"
LAUNCH_TIMEOUT_SECONDS="${VERIFY_TEST_APP_LAUNCH_TIMEOUT_SECONDS:-12}"

PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "No python interpreter found. Install Python 3." >&2
  exit 1
fi

echo "[verify-test-app 1/3] Building TEST app bundle"
bash "${SCRIPT_DIR}/build_macos_app.sh" --mode test

if [[ ! -d "${TEST_APP}" ]]; then
  echo "Expected TEST app bundle missing: ${TEST_APP}" >&2
  exit 1
fi

echo "[verify-test-app 2/3] Relaunching TEST app"
"${PYTHON_BIN}" - <<'PY'
import os
import signal
import subprocess
import time


def _test_runtime_pids() -> list[int]:
    out = subprocess.check_output(["ps", "eww", "-Ao", "pid=,command="], text=True)
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, cmd = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cmd_l = cmd.lower()
        is_test_env = "ASUSROUTERCONTROL_RUNTIME_ENV=test" in cmd
        is_router_proc = "asusroutercontrol" in cmd_l
        is_test_app_path = "ASUSRouterControl TEST.app" in cmd
        if (is_test_env and is_router_proc) or is_test_app_path:
            pids.append(pid)
    return sorted(set(pids))


pids = _test_runtime_pids()
if not pids:
    print("No existing TEST runtime process found.")
    raise SystemExit(0)

print(f"Stopping existing TEST runtime PIDs: {', '.join(str(p) for p in pids)}")
for pid in pids:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

deadline = time.time() + 8.0
while time.time() < deadline:
    remaining = [pid for pid in _test_runtime_pids() if pid in pids]
    if not remaining:
        raise SystemExit(0)
    time.sleep(0.25)

remaining = [pid for pid in _test_runtime_pids() if pid in pids]
if remaining:
    print(f"Force-stopping lingering TEST runtime PIDs: {', '.join(str(p) for p in remaining)}")
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
PY

open -n "${TEST_APP}"

if ! VERIFY_TEST_APP_LAUNCH_TIMEOUT_SECONDS="${LAUNCH_TIMEOUT_SECONDS}" \
"${PYTHON_BIN}" - <<'PY'
import os
import subprocess
import time

timeout_seconds = int(os.environ["VERIFY_TEST_APP_LAUNCH_TIMEOUT_SECONDS"])


def _test_process_running() -> bool:
    try:
        out = subprocess.check_output(["ps", "eww", "-Ao", "pid=,command="], text=True)
    except subprocess.CalledProcessError:
        return False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        cmd = parts[1]
        cmd_l = cmd.lower()
        if (
            "asusroutercontrol_runtime_env=test" in cmd_l
            and "asusroutercontrol" in cmd_l
        ):
            return True
    return False


deadline = time.time() + timeout_seconds
seen_at: float | None = None
while time.time() < deadline:
    now = time.time()
    if _test_process_running():
        if seen_at is None:
            seen_at = now
        elif now - seen_at >= 2.0:
            print("TEST runtime detected after open launch (stable).")
            raise SystemExit(0)
    else:
        seen_at = None
    time.sleep(0.5)

print("No TEST runtime detected after open launch attempt.")
raise SystemExit(1)
PY
then
  echo "Falling back to direct launcher execution: ${TEST_LAUNCHER}"
  if [[ ! -x "${TEST_LAUNCHER}" ]]; then
    echo "Missing or non-executable launcher: ${TEST_LAUNCHER}" >&2
    exit 1
  fi
  mkdir -p "$(dirname "${TEST_LAUNCH_LOG}")"
  nohup "${TEST_LAUNCHER}" >"${TEST_LAUNCH_LOG}" 2>&1 &
fi

echo "[verify-test-app 3/3] Running smoke-check"
VERIFY_TEST_APP_TIMEOUT_SECONDS="${TIMEOUT_SECONDS}" \
VERIFY_TEST_APP_FRESHNESS_SECONDS="${FRESHNESS_SECONDS}" \
VERIFY_TEST_APP_DB_PATH="${TEST_DB_PATH}" \
VERIFY_TEST_APP_LOG_PATH="${TEST_LOG_PATH}" \
"${PYTHON_BIN}" - <<'PY'
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone

timeout_seconds = int(os.environ["VERIFY_TEST_APP_TIMEOUT_SECONDS"])
freshness_seconds = int(os.environ["VERIFY_TEST_APP_FRESHNESS_SECONDS"])
db_path = os.environ["VERIFY_TEST_APP_DB_PATH"]
log_path = os.environ["VERIFY_TEST_APP_LOG_PATH"]


def _parse_iso(ts: str | None) -> datetime | None:
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _test_process_running() -> bool:
    try:
        out = subprocess.check_output(["ps", "eww", "-Ao", "pid=,command="], text=True)
    except subprocess.CalledProcessError:
        return False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        cmd = parts[1]
        cmd_l = cmd.lower()
        if (
            "asusroutercontrol_runtime_env=test" in cmd_l
            and "asusroutercontrol" in cmd_l
        ):
            return True
    return False


def _db_snapshot() -> tuple[str | None, int, int]:
    if not os.path.exists(db_path):
        return None, 0, 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT MAX(timestamp) AS ts FROM device_perf_history")
        ts = cur.fetchone()["ts"]
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=freshness_seconds)).isoformat()
        cur = conn.execute(
            """
            SELECT
              SUM(CASE WHEN LOWER(COALESCE(band,''))='wired' THEN 1 ELSE 0 END) AS wired_rows,
              SUM(
                CASE
                  WHEN LOWER(COALESCE(band,''))='wired'
                    AND (tx_rate_mbps IS NOT NULL OR rx_rate_mbps IS NOT NULL)
                  THEN 1 ELSE 0
                END
              ) AS wired_with_rates
            FROM device_perf_history
            WHERE timestamp >= ?
            """,
            (cutoff,),
        )
        row = cur.fetchone()
        return ts, int(row["wired_rows"] or 0), int(row["wired_with_rates"] or 0)
    except sqlite3.Error:
        return None, 0, 0
    finally:
        conn.close()

def _log_snapshot() -> tuple[str | None, bool]:
    if not os.path.exists(log_path):
        return None, False
    mtime = datetime.fromtimestamp(os.path.getmtime(log_path), tz=timezone.utc)
    age_s = (datetime.now(timezone.utc) - mtime).total_seconds()
    return mtime.isoformat(), age_s <= freshness_seconds


deadline = time.time() + timeout_seconds
last_ts: str | None = None
last_wired_rows = 0
last_wired_rates = 0
last_log_ts: str | None = None
last_log_fresh = False

while time.time() < deadline:
    running = _test_process_running()
    ts, wired_rows, wired_with_rates = _db_snapshot()
    log_ts, log_fresh = _log_snapshot()
    last_ts = ts
    last_wired_rows = wired_rows
    last_wired_rates = wired_with_rates
    last_log_ts = log_ts
    last_log_fresh = log_fresh
    parsed = _parse_iso(ts)
    fresh = (
        parsed is not None
        and (datetime.now(timezone.utc) - parsed).total_seconds() <= freshness_seconds
    )
    if running and (fresh or log_fresh):
        if fresh:
            print(f"Smoke-check PASS: TEST runtime active; latest device_perf_history row at {ts}")
            print(f"Recent wired rows: {wired_rows}; with tx/rx rates: {wired_with_rates}")
        else:
            print("Smoke-check PASS: TEST runtime active with fresh scheduler activity (no fresh telemetry rows yet).")
            print(f"Latest scheduler.log timestamp: {log_ts}")
            print(f"Latest device_perf_history timestamp: {ts}")
        if wired_rows > 0 and wired_with_rates == 0:
            print("Note: wired clients present, but backend did not expose per-client wired tx/rx rates.")
        raise SystemExit(0)
    time.sleep(2.0)

running = _test_process_running()
print("Smoke-check FAIL: TEST runtime did not reach healthy state before timeout.")
print(f"- process_running={running}")
print(f"- latest_device_perf_history_timestamp={last_ts}")
print(f"- recent_wired_rows={last_wired_rows}")
print(f"- recent_wired_rows_with_rates={last_wired_rates}")
print(f"- latest_scheduler_log_timestamp={last_log_ts}")
print(f"- recent_scheduler_log_activity={last_log_fresh}")
raise SystemExit(1)
PY

echo "[verify-test-app] Completed successfully."
