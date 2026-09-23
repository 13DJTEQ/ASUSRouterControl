#!/usr/bin/env bash
# Probe ASUS HTTP(S) admin LOGIN using Bitwarden item
# "router.asus.com (13Maschine)" when ROUTER_PASS is not provided.
#
# Host candidates align with Connect: requested HOST, well-known names,
# default gateway, plus optional lab gateway 192.168.50.1.
#
# Usage:
#   bash scripts/probe_router_login.sh
#   HOST=192.168.50.1 bash scripts/probe_router_login.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOST="${HOST:-router.asus.com}"
ITEM_NAME="${ASUSROUTERCONTROL_BW_ROUTER_ITEM:-router.asus.com (13Maschine)}"

PYTHON=""
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -x "${ROOT}/.venv/bin/python3" ]]; then
  PYTHON="${ROOT}/.venv/bin/python3"
else
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import dotenv, asusrouter, aiohttp" 2>/dev/null; then
  echo "Missing deps in: $PYTHON" >&2
  echo "From the repo root run:  make setup" >&2
  exit 1
fi

# Prefer BW item via the app credential helpers (loads .env + BW_SESSION).
export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"
export ASUSROUTERCONTROL_BW_ROUTER_ITEM="${ITEM_NAME}"

CRED_OUT="$("$PYTHON" - <<'PY'
from asusroutercontrol.credentials import (
    get_router_credentials,
    get_router_ssh_port,
    load_runtime_env_files,
    lookup_bitwarden_router_item,
    bitwarden_vault_status,
)

load_runtime_env_files()
status = bitwarden_vault_status()
item = lookup_bitwarden_router_item(host_hint="router.asus.com")
name = item.get("name") if isinstance(item, dict) else None
user, password = get_router_credentials(host_hint="router.asus.com")
port = get_router_ssh_port(host_hint="router.asus.com")
print(status)
print(name or "")
print(user or "")
print(password or "")
print(port or "")
PY
)" || true

BW_STATUS="$(printf '%s\n' "$CRED_OUT" | sed -n '1p')"
BW_ITEM="$(printf '%s\n' "$CRED_OUT" | sed -n '2p')"
BW_USER="$(printf '%s\n' "$CRED_OUT" | sed -n '3p')"
BW_PASS="$(printf '%s\n' "$CRED_OUT" | sed -n '4p')"
BW_SSH="$(printf '%s\n' "$CRED_OUT" | sed -n '5p')"

echo "bw_status=${BW_STATUS}"
echo "bw_item=${BW_ITEM:-none}"

export ROUTER_USER="${ROUTER_USER:-${BW_USER:-${ASUSROUTERCONTROL_ROUTER_USERNAME:-13Maschine}}}"
if [[ -z "${ROUTER_PASS:-}" ]]; then
  ROUTER_PASS="${BW_PASS:-}"
  export ROUTER_PASS
fi
if [[ -z "${ROUTER_PASS:-}" ]]; then
  echo "No password from Bitwarden (status=${BW_STATUS})." >&2
  echo "Run: bash scripts/bw_sync_router_env.sh" >&2
  read -r -s -p "Router password for ${ROUTER_USER}@${HOST}: " ROUTER_PASS
  echo
  export ROUTER_PASS
fi

echo "ssh_port_from_bw=${BW_SSH:-unknown}"
echo "Probing as user=${ROUTER_USER}"

exec "$PYTHON" <<'PY'
import asyncio
import os
import sys
from types import SimpleNamespace

host = os.environ["HOST"]
user = os.environ["ROUTER_USER"]
password = os.environ["ROUTER_PASS"]

async def main() -> int:
    from asusroutercontrol.connect import _http_transport_attempts, probe_http
    from asusroutercontrol.discovery import default_gateway_ipv4

    def cfg(h: str, port: int, ssl: bool):
        return SimpleNamespace(
            router_host=h,
            router_port=port,
            use_ssl=ssl,
            router_backend="merlin",
            ssh_port=1313,
        )

    hosts: list[str] = []
    for candidate in (
        host,
        "router.asus.com",
        "www.asusrouter.com",
        default_gateway_ipv4() or "",
        "192.168.50.1",
    ):
        key = (candidate or "").strip()
        if key and key not in hosts:
            hosts.append(key)

    attempts = []
    for h in hosts:
        for attempt_host, port, ssl in _http_transport_attempts(
            h, http_port=80, use_ssl=False
        ):
            attempts.append(cfg(attempt_host, port, ssl))

    # Dedupe (host, port, ssl)
    seen: set[tuple[str, int, bool]] = set()
    ordered = []
    for c in attempts:
        key = (c.router_host, c.router_port, c.use_ssl)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(c)

    print(f"Using python={sys.executable}")
    print(f"Hosts={hosts}")
    for c in ordered:
        ok, err = await probe_http(c, user, password)
        label = f"{'https' if c.use_ssl else 'http'}://{c.router_host}:{c.router_port}"
        if ok:
            print(f"OK  {label}")
            return 0
        print(f"FAIL {label}: {err}")
    return 1

sys.exit(asyncio.run(main()))
PY
