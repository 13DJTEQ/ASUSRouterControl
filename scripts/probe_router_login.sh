#!/usr/bin/env bash
# Probe ASUS HTTP(S) admin LOGIN the same way the app does.
# Run on the Mac (on the router LAN).
#
# Usage:
#   ROUTER_PASS='your-password' bash scripts/probe_router_login.sh
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

export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"
python3 <<'PY'
import asyncio
import os
import sys

host = os.environ["HOST"]
user = os.environ["ROUTER_USER"]
password = os.environ["ROUTER_PASS"]

async def main() -> int:
    from asusroutercontrol.config import Config
    from asusroutercontrol.connect import probe_http

    attempts = [
        Config(router_host=host, router_port=8443, use_ssl=True),
        Config(router_host=host, router_port=80, use_ssl=False),
        Config(router_host="192.168.50.1", router_port=8443, use_ssl=True),
        Config(router_host="192.168.50.1", router_port=80, use_ssl=False),
    ]
    print(f"Probing as user={user!r}")
    for cfg in attempts:
        ok, err = await probe_http(cfg, user, password)
        label = f"{'https' if cfg.use_ssl else 'http'}://{cfg.router_host}:{cfg.router_port}"
        if ok:
            print(f"OK  {label}")
            return 0
        print(f"FAIL {label}: {err}")
    return 1

sys.exit(asyncio.run(main()))
PY
