#!/usr/bin/env bash
# Unlock Bitwarden (if needed) and sync the lab router login item into DEV .env
# so the menubar app can read username / SSH port / BW_SESSION.
#
# Unlock order (never prompts for the Bitwarden master password):
#   1. Existing valid BW_SESSION in env / .env
#   2. Community Keychain service BW_SESSION (via ensure_bitwarden_unlocked)
#   3. Keychain master-password candidates (secondary)
# If still locked: exit non-zero with Terminal steps (stock `bw unlock` once).
#
# Item: router.asus.com (13Maschine)
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

# Prefer repo venv Python (editable install / src on path) so imports succeed.
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Seed BW_SESSION from the target .env when the shell has none (GUI sync path).
if [[ -z "${BW_SESSION:-}" && -f "$ENV_FILE" ]]; then
  seeded="$(
    sed -n 's/^[[:space:]]*BW_SESSION=//p' "$ENV_FILE" | tail -n1 | tr -d '\r' | sed 's/^"//;s/"$//'
  )"
  if [[ -n "$seeded" ]]; then
    export BW_SESSION="$seeded"
  fi
fi

status="$(bw status 2>/dev/null | tr -d '\r' || true)"
echo "bw status: ${status:-unknown}"

if echo "$status" | grep -qi 'unauthenticated'; then
  echo "Run: bw login" >&2
  exit 1
fi

_try_keychain_unlock() {
  # Prints session token on stdout when vault becomes unlocked; returns 0 on success.
  # Exit 2 = no Keychain session/MP. Exit 1 = unlock attempted and failed.
  ROOT="$ROOT" "$PY" - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
src = root / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from asusroutercontrol.credentials import (  # noqa: E402
    bitwarden_master_password_keychain_present,
    discover_bw_session_from_keychain,
    ensure_bitwarden_unlocked,
    get_bitwarden_master_password,
    get_last_bitwarden_unlock_error,
)

kc_token, kc_path = discover_bw_session_from_keychain()
has_mp = bool(
    get_bitwarden_master_password() or bitwarden_master_password_keychain_present()
)
if not kc_token and not has_mp:
    print(
        "No Keychain BW_SESSION or master password found.\n"
        "In Terminal (stock bw, once):\n"
        "  bw unlock\n"
        "  export BW_SESSION=\"<token from unlock --raw>\"\n"
        "  bash scripts/bw_sync_router_env.sh",
        file=sys.stderr,
    )
    sys.exit(2)

state = ensure_bitwarden_unlocked()
session = os.environ.get("BW_SESSION", "").strip()
if state != "unlocked" or not session:
    err = get_last_bitwarden_unlock_error() or f"vault status={state}"
    print(f"Keychain auto-unlock failed: {err}", file=sys.stderr)
    print(
        "In Terminal (stock bw, once — this script does not prompt):\n"
        "  bw unlock\n"
        "  export BW_SESSION=\"<token>\"\n"
        "  bash scripts/bw_sync_router_env.sh",
        file=sys.stderr,
    )
    sys.exit(1)
if kc_path:
    print(f"Unlocked via Keychain session candidate ({kc_path})", file=sys.stderr)
print(session)
PY
}

if echo "$status" | grep -qi 'locked' || [[ -z "${BW_SESSION:-}" ]]; then
  # If status says locked while BW_SESSION is set, clear stale token via Python.
  if echo "$status" | grep -qi 'locked' && [[ -n "${BW_SESSION:-}" ]]; then
    ROOT="$ROOT" "$PY" - <<'PY' || true
import os, sys
from pathlib import Path
root = Path(os.environ["ROOT"])
sys.path.insert(0, str(root / "src"))
from asusroutercontrol.credentials import clear_stale_bw_session
clear_stale_bw_session(reason="bw_sync: bw status locked with BW_SESSION set")
print("Cleared stale BW_SESSION before Keychain unlock attempt.", file=sys.stderr)
PY
    unset BW_SESSION || true
  fi
  err_file="$(mktemp "${TMPDIR:-/tmp}/bw_keychain_unlock.XXXXXX")"
  set +e
  session="$(_try_keychain_unlock 2>"$err_file")"
  kc_rc=$?
  set -e
  if [[ "$kc_rc" -eq 0 && -n "$session" ]]; then
    export BW_SESSION="$session"
    echo "Unlocked Bitwarden via Keychain BW_SESSION / master password."
  else
    if [[ -s "$err_file" ]]; then
      cat "$err_file" >&2 || true
    fi
    rm -f "$err_file"
    if [[ "$kc_rc" -eq 2 ]]; then
      echo "Non-interactive sync requires a Keychain BW_SESSION or prior unlock." >&2
      echo "In Terminal (stock bw, once — no prompt in this script):" >&2
      echo "  bw unlock" >&2
      echo "  export BW_SESSION=\"<token>\"" >&2
      echo "  bash scripts/bw_sync_router_env.sh" >&2
      exit 2
    fi
    echo "Keychain auto-unlock did not unlock the vault." >&2
    echo "Check: asusrouter credentials bw-master --status" >&2
    echo "Interactive bw unlock is disabled here — unlock once via stock bw yourself." >&2
    exit 1
  fi
  rm -f "$err_file"
fi

echo "Fetching item: ${ITEM_NAME}"
item_json="$(bw get item "$ITEM_NAME" --raw 2>/dev/null || true)"
if [[ -z "$item_json" ]]; then
  item_id="$(bw list items --search 'router.asus.com' --raw 2>/dev/null | "$PY" -c "
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
"$PY" <<'PY'
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
        # Config.load_config reads SSH_PORT for profile overlays / RouterSSH.
        f"SSH_PORT={port}",
    ]
)
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote {env_path}")
print(f"  item: {item_name}")
print(f"  username: {user}")
print(f"  ssh_port: {port}")
print("  password: (kept in Bitwarden only — not written to .env)")

# Mirror login into Keychain so DEV.app Connect works even when the GUI
# cannot unlock Bitwarden (missing BW_SESSION / Keychain ACL for .app).
import sys as _sys
try:
    import os as _os
    _os.environ.setdefault("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
    from asusroutercontrol.credentials import mirror_router_login_to_keychain

    backend = mirror_router_login_to_keychain(
        user,
        password,
        ssh_port=int(port),
        env="dev",
    )
    print(f"  keychain mirror: {backend} (env=dev)")
except Exception as exc:  # noqa: BLE001
    print(f"  keychain mirror failed: {exc}", file=_sys.stderr)
    raise SystemExit(1) from exc
PY

echo
echo "Next: quit DEV.app, then rebuild and relaunch from testbuilds."
echo "Connect with blank password — Keychain mirror is primary when vault is locked."
