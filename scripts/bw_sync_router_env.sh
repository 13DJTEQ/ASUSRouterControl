#!/usr/bin/env bash
# Unlock Bitwarden (if needed) and sync the lab router login item into DEV .env
# so the menubar app can read username / SSH port / BW_SESSION.
#
# Item: router.asus.com (13Maschine)
#
# Prefers Keychain-stored master password via:
#   asusrouter credentials bw-master --set
#
# Usage:
#   bash scripts/bw_sync_router_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ITEM_NAME="${ASUSROUTERCONTROL_BW_ROUTER_ITEM:-router.asus.com (13Maschine)}"
ENV_FILE="${ASUSROUTERCONTROL_ENV_FILE:-${HOME}/.asusroutercontrol.dev/.env}"

if ! command -v bw >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/bw /usr/local/bin/bw; do
    if [[ -x "$candidate" ]]; then
      export PATH="$(dirname "$candidate"):$PATH"
      break
    fi
  done
fi
if ! command -v bw >/dev/null 2>&1; then
  echo "bw CLI not found. Install Bitwarden CLI first." >&2
  exit 1
fi

status="$(bw status 2>/dev/null | tr -d '\r' || true)"
echo "bw status: ${status:-unknown}"

if echo "$status" | grep -qi 'unauthenticated'; then
  echo "Run: bw login" >&2
  exit 1
fi

if echo "$status" | grep -qi 'locked' || [[ -z "${BW_SESSION:-}" ]]; then
  PY_BIN="${ROOT}/.venv/bin/python"
  if [[ ! -x "$PY_BIN" ]]; then
    PY_BIN="$(command -v python3 || true)"
  fi
  session=""
  if [[ -n "$PY_BIN" ]]; then
    echo "Trying Keychain master-password unlock..."
    session="$("$PY_BIN" -c 'import os,sys; sys.path.insert(0,"src"); from asusroutercontrol.credentials import ensure_bitwarden_unlocked; st=ensure_bitwarden_unlocked(); print(os.environ.get("BW_SESSION","") if st=="unlocked" else "")' 2>/dev/null || true)"
  fi
  if [[ -z "$session" ]]; then
    echo "Unlocking Bitwarden vault (enter master password when prompted)..."
    unlock_out="$(bw unlock --raw 2>/dev/null || true)"
    session="$(printf '%s\n' "$unlock_out" | tail -n1 | tr -d '\r')"
    if [[ "$session" == *BW_SESSION=* ]]; then
      session="$(printf '%s\n' "$unlock_out" | sed -n 's/.*BW_SESSION="\([^"]*\)".*/\1/p' | tail -n1)"
      if [[ -z "$session" ]]; then
        session="$(printf '%s\n' "$unlock_out" | sed -n 's/.*BW_SESSION=\([^ ]*\).*/\1/p' | tail -n1)"
      fi
    fi
  fi
  if [[ -z "$session" ]]; then
    echo "Could not unlock Bitwarden." >&2
    echo "Store master password once: asusrouter credentials bw-master --set" >&2
    echo "Or run: bw unlock" >&2
    exit 1
  fi
  export BW_SESSION="$session"
fi

echo "Fetching item: ${ITEM_NAME}"
item_json="$(bw get item "$ITEM_NAME" --raw 2>/dev/null || true)"
if [[ -z "$item_json" ]]; then
  item_id="$(bw list items --search 'router.asus.com' --raw 2>/dev/null | python3 -c "
import json, sys
q = '''${ITEM_NAME}'''.lower()
items = json.load(sys.stdin)
for it in items:
    name = (it.get('name') or '').lower()
    if name == q or '13maschine' in name:
        print(it.get('id') or '')
        break
" 2>/dev/null || true)"
  if [[ -n "$item_id" ]]; then
    item_json="$(bw get item "$item_id" --raw)"
  fi
fi
if [[ -z "$item_json" ]]; then
  echo "Bitwarden item not found: ${ITEM_NAME}" >&2
  exit 1
fi

mkdir -p "$(dirname "$ENV_FILE")"
export ENV_FILE ITEM_NAME ITEM_JSON="$item_json"
python3 <<'PY'
import json, os
from pathlib import Path

item = json.loads(os.environ["ITEM_JSON"])
login = item.get("login") or {}
user = str(login.get("username") or "").strip()
password = str(login.get("password") or "").strip()
port = ""
for field in item.get("fields") or []:
    name = str(field.get("name") or "").strip().lower().replace("_", " ").replace("-", " ")
    if "ssh" in name and "port" in name:
        port = str(field.get("value") or "").strip()
        break
if not port:
    port = "1313"
if not user or not password:
    raise SystemExit("Item is missing username/password")

env_path = Path(os.environ["ENV_FILE"])
item_name = os.environ["ITEM_NAME"]
session = os.environ.get("BW_SESSION", "").strip()
managed = {
    "BW_SESSION",
    "BITWARDEN_SESSION",
    "ASUSROUTERCONTROL_BW_ROUTER_ITEM",
    "ASUSROUTERCONTROL_ROUTER_USERNAME",
    "ASUSROUTERCONTROL_ROUTER_SSH_PORT",
    "SSH_PORT",
}
lines = []
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in managed:
            continue
        lines.append(line)
while lines and not lines[-1].strip():
    lines.pop()
lines.extend(
    [
        f"BW_SESSION={session}",
        f"ASUSROUTERCONTROL_BW_ROUTER_ITEM={item_name}",
        f"ASUSROUTERCONTROL_ROUTER_USERNAME={user}",
        f"ASUSROUTERCONTROL_ROUTER_SSH_PORT={port}",
        f"SSH_PORT={port}",
    ]
)
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote {env_path}")
print(f"  item: {item_name}")
print(f"  username: {user}")
print(f"  ssh_port: {port}")
print("  password: (kept in Bitwarden only — not written to .env)")
PY

echo
echo "Next: quit DEV.app, then rebuild and relaunch from testbuilds."
