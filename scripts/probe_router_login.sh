#!/usr/bin/env bash
# Probe ASUS HTTP(S) admin LOGIN the same way the app does.
# Run on the Mac (on the router LAN).
#
# Usage:
#   ROUTER_PASS='your-real-password' bash scripts/probe_router_login.sh
#   ROUTER_USER=13Maschine HOST=192.168.50.1 ROUTER_PASS='...' bash scripts/probe_router_login.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOST="${HOST:-router.asus.com}"
export ROUTER_USER="${ROUTER_USER:-${ASUSROUTERCONTROL_ROUTER_USERNAME:-13Maschine}}"

if [[ -z "${ROUTER_PASS:-}" ]]; then
  if command -v bw >/dev/null 2>&1 && [[ -n "${BW_SESSION:-}" ]]; then
    ROUTER_PASS="$(bw get password "router.asus.com (13Maschine)" 2>/dev/null || true)"
    export ROUTER_PASS
  fi
fi
if [[ -z "${ROUTER_PASS:-}" ]]; then
  read -r -s -p "Router password for ${ROUTER_USER}@${HOST}: " ROUTER_PASS
  echo
  export ROUTER_PASS
fi

PYTHON=""
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -x "${ROOT}/.venv/bin/python3" ]]; then
  PYTHON="${ROOT}/.venv/bin/python3"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import dotenv, asusrouter, aiohttp" 2>/dev/null; then
  echo "Missing deps in: $PYTHON" >&2
  echo "From the repo root run:  make setup   (or: python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]')" >&2
  exit 1
fi

export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"
exec "$PYTHON" <<'PY'
import asyncio
import os
import sys
from types import SimpleNamespace

host = os.environ["HOST"]
user = os.environ["ROUTER_USER"]
password = os.environ["ROUTER_PASS"]

async def main() -> int:
    from asusroutercontrol.connect import probe_http

    def cfg(h: str, port: int, ssl: bool):
        return SimpleNamespace(
            router_host=h,
            router_port=port,
            use_ssl=ssl,
            router_backend="merlin",
            ssh_port=1313,
        )

    attempts = [
        cfg(host, 8443, True),
        cfg(host, 80, False),
        cfg("192.168.50.1", 8443, True),
        cfg("192.168.50.1", 80, False),
    ]
    print(f"Using python={sys.executable}")
    print(f"Probing as user={user!r}")
    for c in attempts:
        ok, err = await probe_http(c, user, password)
        label = f"{'https' if c.use_ssl else 'http'}://{c.router_host}:{c.router_port}"
        if ok:
            print(f"OK  {label}")
            return 0
        print(f"FAIL {label}: {err}")
    return 1

sys.exit(asyncio.run(main()))
PY
