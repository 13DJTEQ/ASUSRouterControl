#!/usr/bin/env bash
# Pull / dump Connect diagnostics from Dave's Mac DEV (and PROD) data dirs.
# Redacts secrets (passwords, BW_SESSION tokens). Safe to paste into chat.
#
# Usage (on the Mac):
#   bash scripts/pull_connect_logs.sh
#   bash scripts/pull_connect_logs.sh | pbcopy
set -euo pipefail

DEV_DIR="${HOME}/.asusroutercontrol.dev"
PROD_DIR="${HOME}/.asusroutercontrol"
MAX_MATCH=200

_redact() {
  # Strip secret-looking assignments / tokens while keeping structure.
  sed -E \
    -e 's/(BW_SESSION|BITWARDEN_SESSION|BW_PASSWORD|password|passwd|secret|token|api[_-]?key)[=:][[:space:]]*[^[:space:]]+/\1=<redacted>/Ig' \
    -e 's/(BW_SESSION|BITWARDEN_SESSION)=[^[:space:]]+/\1=<redacted>/Ig' \
    -e 's/("password"[[:space:]]*:[[:space:]]*")[^"]+"/\1<redacted>"/Ig' \
    -e 's/(master password[^[:alnum:]]+)[^[:space:]]+/\1<redacted>/Ig'
}

_section() {
  printf '\n======== %s ========\n' "$1"
}

_dump_log_matches() {
  local log="$1"
  local label="$2"
  if [[ ! -f "$log" ]]; then
    echo "(missing) $log"
    return 0
  fi
  _section "$label — $log (last ~${MAX_MATCH} matching lines)"
  grep -E \
    "Connect defaults|Connect start|Connect success|Connect failure|Connect blocked|Connect dialog|Router credentials resolved|HTTP login attempt|HTTP probe failed|HTTP admin preflight|Stopping transport|Connect router failed|Bitwarden|Keychain|vault locked|master password|bw_sync|rejected login|captcha|Cannot access|Unable to Connect|Mirrored router login|Unlock:" \
    "$log" 2>/dev/null | tail -n "$MAX_MATCH" | _redact || true
  echo
  echo "-------- raw tail (60 lines, redacted) --------"
  tail -n 60 "$log" | _redact
}

_env_key_names() {
  local env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    echo "(missing) $env_file"
    return 0
  fi
  echo "keys present (names only):"
  # Print KEY= from dotenv; never print values.
  sed -nE 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' "$env_file" \
    | sort -u
  if grep -Eq '^[[:space:]]*BW_SESSION=' "$env_file"; then
    echo "BW_SESSION: present (value redacted)"
  else
    echo "BW_SESSION: absent"
  fi
  if grep -Eq '^[[:space:]]*ASUSROUTERCONTROL_ROUTER_USERNAME=' "$env_file"; then
    echo "ASUSROUTERCONTROL_ROUTER_USERNAME: present"
  else
    echo "ASUSROUTERCONTROL_ROUTER_USERNAME: absent"
  fi
  if grep -Eq '^[[:space:]]*ASUSROUTERCONTROL_ROUTER_SSH_PORT=|^[[:space:]]*SSH_PORT=' "$env_file"; then
    echo "SSH port keys: present"
  else
    echo "SSH port keys: absent"
  fi
}

_vault_hints() {
  _section "Bitwarden vault status hints"
  if ! command -v bw >/dev/null 2>&1; then
    for candidate in /opt/homebrew/bin/bw /usr/local/bin/bw; do
      if [[ -x "$candidate" ]]; then
        export PATH="$(dirname "$candidate"):$PATH"
        break
      fi
    done
  fi
  if command -v bw >/dev/null 2>&1; then
    echo "bw binary: $(command -v bw)"
    # Never print session tokens from status JSON.
    bw status 2>/dev/null | _redact || echo "bw status failed"
  else
    echo "bw CLI: not found on PATH"
  fi
  echo
  echo "BW_SESSION in this shell: $([[ -n "${BW_SESSION:-}" ]] && echo present || echo absent)"
  if command -v asusrouter >/dev/null 2>&1; then
    echo
    echo "asusrouter credentials bw-master --status (secrets redacted):"
    asusrouter credentials bw-master --status 2>&1 | _redact || true
  else
    echo "asusrouter CLI: not on PATH (activate .venv or rebuild DEV.app)"
  fi
}

echo "ASUSRouterControl Connect log pull — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host=$(hostname 2>/dev/null || echo unknown) user=${USER:-unknown}"

_vault_hints

for dir_label in "DEV:${DEV_DIR}" "PROD:${PROD_DIR}"; do
  label="${dir_label%%:*}"
  dir="${dir_label#*:}"
  _section "$label data dir: $dir"
  if [[ ! -d "$dir" ]]; then
    echo "(missing directory)"
    continue
  fi
  ls -la "$dir" 2>/dev/null | head -n 40 || true
  echo
  _section "$label .env key names"
  _env_key_names "${dir}/.env"
  echo
  # scheduler + any menubar/app logs
  for log in \
    "${dir}/scheduler.log" \
    "${dir}/menubar.log" \
    "${dir}/app.log" \
    "${dir}/connect.log" \
    "${dir}/asusroutercontrol.log"
  do
    _dump_log_matches "$log" "$label"
  done
  # Extra *.log files (cap)
  while IFS= read -r extra; do
    case "$extra" in
      */scheduler.log|*/menubar.log|*/app.log|*/connect.log|*/asusroutercontrol.log) continue ;;
    esac
    _dump_log_matches "$extra" "$label extra"
  done < <(find "$dir" -maxdepth 1 -type f -name '*.log' 2>/dev/null | head -n 8)
done

_section "Done"
echo "Paste this entire output into the agent chat (secrets already redacted)."
echo "Also useful: bash scripts/bw_sync_router_env.sh && asusrouter credentials bw-master --status"
