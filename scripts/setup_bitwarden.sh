#!/usr/bin/env bash
# scripts/setup_bitwarden.sh — Create and verify Bitwarden entries for ASUSRouterControl.
#
# Handles the full flow: login → unlock → create entries → verify retrieval.
# Secrets are never echoed. BW_SESSION is trapped for cleanup on exit.
set -euo pipefail

PROJECT="asusroutercontrol"
ENV="prod"
USERNAME_KEY="router_username"
PASSWORD_KEY="router_password"

title_for() { echo "universal-keychain-${PROJECT}-${ENV}-${1}"; }
account_for() { echo "${PROJECT}.${ENV}.${1}"; }

USERNAME_TITLE=$(title_for "$USERNAME_KEY")
PASSWORD_TITLE=$(title_for "$PASSWORD_KEY")
USERNAME_ACCOUNT=$(account_for "$USERNAME_KEY")
PASSWORD_ACCOUNT=$(account_for "$PASSWORD_KEY")

cleanup() { unset BW_SESSION 2>/dev/null || true; }
trap cleanup EXIT

# ── Step 1: Verify bw CLI ────────────────────────────────────────────────────
if ! command -v bw &>/dev/null; then
    echo "❌ Bitwarden CLI (bw) not found. Install: brew install bitwarden-cli"
    exit 1
fi
echo "✓ bw CLI found: $(bw --version 2>/dev/null || echo 'unknown version')"

# ── Step 2: Login if needed ──────────────────────────────────────────────────
STATUS=$(bw status 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
echo "  Vault status: ${STATUS}"

if [[ "$STATUS" == "unauthenticated" ]]; then
    echo ""
    echo "Logging in to Bitwarden..."
    bw login
    STATUS=$(bw status 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
fi

# ── Step 3: Unlock if needed (Keychain MP only — never interactive) ──────────
if [[ "$STATUS" == "locked" ]]; then
    echo ""
    echo "Unlocking vault via Keychain master password (non-interactive)..."
    SCRIPT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
    UNLOCK_OUT="$(
        ROOT="$SCRIPT_ROOT" PYTHONPATH="${SCRIPT_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}" \
        python3 - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
src = root / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from asusroutercontrol.credentials import (
    ensure_bitwarden_unlocked,
    get_bitwarden_master_password,
    get_last_bitwarden_unlock_error,
)

if not get_bitwarden_master_password():
    print(
        "No Bitwarden master password in Keychain.\n"
        "One-time: asusrouter credentials bw-master --set",
        file=sys.stderr,
    )
    sys.exit(2)
state = ensure_bitwarden_unlocked()
session = os.environ.get("BW_SESSION", "").strip()
if state != "unlocked" or not session:
    err = get_last_bitwarden_unlock_error() or f"vault status={state}"
    print(f"Keychain auto-unlock failed: {err}", file=sys.stderr)
    sys.exit(1)
print(session)
PY
    )" || {
        echo "❌ Non-interactive unlock failed."
        echo "  Store MP once: asusrouter credentials bw-master --set"
        echo "  Then re-run this script. (Interactive bw unlock is disabled.)"
        exit 1
    }
    export BW_SESSION="$UNLOCK_OUT"
fi

# Final status check
STATUS=$(bw status 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
if [[ "$STATUS" != "unlocked" ]]; then
    echo "❌ Vault is not unlocked (status: ${STATUS}). Cannot proceed."
    exit 1
fi
echo "✓ Vault unlocked"

# ── Step 4: Sync vault ───────────────────────────────────────────────────────
echo "  Syncing vault..."
bw sync 2>/dev/null || true

# ── Step 5: Prompt for router credentials ────────────────────────────────────
echo ""
echo "Router credentials (stored in Bitwarden):"
read -r -p "Router username [admin]: " ROUTER_USER
ROUTER_USER="${ROUTER_USER:-admin}"
read -r -s -p "Router password: " ROUTER_PASS
echo ""

if [[ -z "$ROUTER_PASS" ]]; then
    echo "❌ Password cannot be empty."
    exit 1
fi

# ── Step 6: Create or update Bitwarden entries ───────────────────────────────
create_or_update_item() {
    local title="$1"
    local account="$2"
    local secret="$3"

    # Check if item already exists
    EXISTING=$(bw list items --search "$title" 2>/dev/null | python3 -c "
import sys, json
items = json.load(sys.stdin)
for item in items:
    if item.get('name') == sys.argv[1]:
        print(item.get('id', ''))
        break
" "$title" 2>/dev/null || echo "")

    if [[ -n "$EXISTING" ]]; then
        echo "  Updating existing item: ${title}"
        EDIT_JSON=$(bw get item "$EXISTING" 2>/dev/null | python3 -c "
import sys, json
item = json.load(sys.stdin)
item['login']['username'] = sys.argv[1]
item['login']['password'] = sys.argv[2]
# Strip fields bw rejects on edit
for k in ('object','id','organizationId','folderId','collectionIds','revisionDate','reprompt','deletedDate'):
    item.pop(k, None)
json.dump(item, sys.stdout)
" "$account" "$secret")
        bw edit item "$EXISTING" "$EDIT_JSON" &>/dev/null
        echo "  ✓ Updated: ${title}"
    else
        echo "  Creating item: ${title}"
        CREATE_JSON=$(python3 -c "
import json, sys
payload = {
    'type': 1,
    'name': sys.argv[1],
    'login': {
        'username': sys.argv[2],
        'password': sys.argv[3]
    }
}
json.dump(payload, sys.stdout)
" "$title" "$account" "$secret")
        bw create item "$CREATE_JSON" &>/dev/null
        echo "  ✓ Created: ${title}"
    fi
}

echo ""
echo "Creating Bitwarden entries..."
create_or_update_item "$USERNAME_TITLE" "$USERNAME_ACCOUNT" "$ROUTER_USER"
create_or_update_item "$PASSWORD_TITLE" "$PASSWORD_ACCOUNT" "$ROUTER_PASS"

# ── Step 7: Verify retrieval via the application's credential module ─────────
echo ""
echo "Verifying credential retrieval..."
VERIFY_RESULT=$(cd "/Volumes/2TB NvMe/MediaWave Development Projects/ASUSRouterControl" && \
    .venv/bin/python3 -c "
import os
os.environ.pop('ASUSROUTERCONTROL_CREDENTIAL_BACKEND', None)  # use default (bitwarden)
from asusroutercontrol.credentials import get_router_credentials, _active_backend_name
backend = _active_backend_name()
user, pw = get_router_credentials()
if user and pw:
    print(f'OK|{backend}|{len(user)}|{len(pw)}')
else:
    missing = []
    if not user: missing.append('router_username')
    if not pw: missing.append('router_password')
    print(f'FAIL|{backend}|{\",\".join(missing)}')
" 2>/dev/null || echo "ERROR|unknown|import_failed")

IFS='|' read -r STATUS BACKEND ARG1 ARG2 <<< "$VERIFY_RESULT"

if [[ "$STATUS" == "OK" ]]; then
    echo "✓ Credentials retrieved successfully"
    echo "  Backend: ${BACKEND}"
    echo "  Username: ${ARG1} chars stored"
    echo "  Password: ${ARG2} chars stored"
    echo ""
    echo "✓ ASUSRouterControl Bitwarden setup complete."
    echo "  Run 'asusrouter status' to verify router connectivity."
elif [[ "$STATUS" == "FAIL" ]]; then
    echo "❌ Verification failed — missing keys: ${ARG1}"
    echo "  Backend was: ${BACKEND}"
    echo "  Try: export ASUSROUTERCONTROL_CREDENTIAL_BACKEND=bitwarden"
    exit 1
else
    echo "❌ Verification error: ${ARG1}"
    echo "  Ensure the .venv is set up: make setup"
    exit 1
fi
