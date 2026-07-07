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

# ── Step 3: Unlock if needed ─────────────────────────────────────────────────
if [[ "$STATUS" == "locked" ]]; then
    echo ""
    echo "Unlocking vault..."
    export BW_SESSION=$(bw unlock --passwordenv BW_SESSION 2>/dev/null | sed -n 's/.*BW_SESSION=\([^ ]*\).*/\1/p' || true)
    if [[ -z "${BW_SESSION:-}" ]]; then
        # Fallback: interactive unlock
        echo "Run 'bw unlock' in another terminal and paste the session token:"
        read -r -s -p "BW_SESSION: " BW_SESSION
        echo ""
        export BW_SESSION
    fi
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
