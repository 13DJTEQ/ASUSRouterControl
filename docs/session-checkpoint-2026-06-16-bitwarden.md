# Session Checkpoint: Bitwarden Integration Phase

**Date:** 2026-06-16
**Branch:** `consolidation/2026-06-14-main`
**Commit:** `7a46434`
**Previous checkpoint:** `97d0845`

## Changes since last checkpoint

### Major feature: Pluggable credential backend registry

`src/asusroutercontrol/credentials.py` was fully rewritten to support three backends:

- **1Password** (`_OnePasswordBackend`) — existing op CLI logic, extracted to class
- **macOS Keychain** (`_KeychainBackend`) — promoted from fallback-only to primary backend
- **Bitwarden** (`_BitwardenBackend`) — new scaffold with `bw` CLI integration

### Key behaviors

| Feature | Status |
|---|---|
| Backend switch via `ASUSROUTERCONTROL_CREDENTIAL_BACKEND` | ✅ |
| Env-aware lookups (dev → shared → prod fallback) | ✅ |
| Cross-backend read fallback | ✅ |
| Bitwarden `login_check()` (unlocked/locked/unauthenticated/cli_not_found) | ✅ |
| Bitwarden graceful handling when `bw` CLI missing | ✅ |
| Bitwarden `store()` with correct JSON stdin syntax | ✅ |
| `get_router_credentials()` reads `ASUSROUTERCONTROL_RUNTIME_ENV` | ✅ |
| CLI setup/migrate messages reference active backend name | ✅ |

### Validation

- `make lint` — clean (ruff)
- `make test` — 295 passed, 1 skipped
- No breaking changes to existing 1Password or Keychain workflows

### Known limitations / deferred work

1. Bitwarden `store()` JSON payload for `bw edit item` and `bw create item` has been syntax-corrected but not tested against a live Bitwarden vault (bw CLI not installed on this machine)
2. `BW_SESSION` env var is checked but `bw status` JSON parsing is the primary auth detection
3. No CLI flag to switch backend at runtime (only env var)
4. Remaining deferred items from Phase 3 still pending:
   - CLI monolith refactor
   - DB covering indexes
   - Speedtest error classification refinement
   - SSH exponential backoff review

## Test notes

DEV menubar was launched from venv (PID 26954, later replaced). Credential isolation confirmed working — DEV queries dev namespace, falls back to shared/prod. 1Password CLI not configured on this machine, so lookups failed gracefully with clear error messages. Router connectivity confirmed via env var fallback credentials.

## Next recommended steps

1. Install `bw` CLI and run `bw login` to validate the Bitwarden backend against a real vault
2. Test `store_credential()` with `ASUSROUTERCONTROL_CREDENTIAL_BACKEND=bitwarden`
3. Consider adding `bw` as an optional dependency in pyproject.toml extras
4. Resume dual DEV+PROD testing once Bitwarden connectivity is confirmed
