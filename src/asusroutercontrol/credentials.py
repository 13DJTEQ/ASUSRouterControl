"""Secure credential management with pluggable backends: 1Password, macOS Keychain, Bitwarden.

Canonical naming follows the universal-keychain convention:
  Service / vault item title: universal-keychain-asusroutercontrol-{env}-{key}
  Account metadata: asusroutercontrol.{env}.{key}

The active backend is controlled by ASUSROUTERCONTROL_CREDENTIAL_BACKEND env var
("1password" | "keychain" | "bitwarden").  When the backend is unreachable, reads
fall back through the remaining backends in priority order.
"""

from __future__ import annotations

import abc
import logging
import os
import subprocess
from json import JSONDecodeError
from json import dumps as json_dumps
from json import loads as json_loads

import keyring

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Naming constants
# ---------------------------------------------------------------------------
PROJECT = "asusroutercontrol"
DEFAULT_ENV = "prod"
_LEGACY_SERVICE = "com.asusroutercontrol"

_CREDENTIAL_BACKEND_ENV = "ASUSROUTERCONTROL_CREDENTIAL_BACKEND"
_OP_VAULT_ENV = "ASUSROUTERCONTROL_1PASSWORD_VAULT"
_OP_VAULT_ENV_FALLBACK = "OP_VAULT"
_BW_SESSION_ENV = "BW_SESSION"

# ---------------------------------------------------------------------------
# Backend detection helpers
# ---------------------------------------------------------------------------


def _backend_name(backend: object) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__name__}"


def _is_fail_backend(backend: object) -> bool:
    return _backend_name(backend) == "keyring.backends.fail.Keyring"


def _build_macos_keyring():
    from keyring.backends.macOS import Keyring as MacOSKeyring

    return MacOSKeyring()


def _ensure_secure_keyring_backend() -> bool:
    backend = keyring.get_keyring()
    if not _is_fail_backend(backend):
        return True
    try:
        keyring.set_keyring(_build_macos_keyring())
    except Exception as exc:
        log.error(
            "Secure keychain backend unavailable (active backend=%s): %s",
            _backend_name(backend),
            exc,
        )
        return False
    active = keyring.get_keyring()
    if _is_fail_backend(active):
        log.error(
            "Secure keychain backend unavailable (still active backend=%s)",
            _backend_name(active),
        )
        return False
    log.info("Activated secure keychain backend: %s", _backend_name(active))
    return True


# ---------------------------------------------------------------------------
# Canonical name builders
# ---------------------------------------------------------------------------


def _service_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"universal-keychain-{PROJECT}-{env}-{key}"


def _account_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"{PROJECT}.{env}.{key}"


# ---------------------------------------------------------------------------
# 1Password helpers
# ---------------------------------------------------------------------------


def _op_vault() -> str | None:
    value = os.environ.get(_OP_VAULT_ENV, "").strip()
    if value:
        return value
    return os.environ.get(_OP_VAULT_ENV_FALLBACK, "").strip() or None


def _op_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["op", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        log.error("1Password CLI ('op') not found. Install it to manage project credentials.")
        return None
    except OSError as exc:
        log.error("Failed to execute 1Password CLI command: %s", exc)
        return None


def _op_item_not_found(message: str) -> bool:
    normalized = message.lower()
    return any(
        candidate in normalized
        for candidate in (
            "isn't an item",
            "could not find",
            "not found",
            "doesn't exist",
            "no item",
        )
    )


def _extract_op_secret(item: dict) -> str | None:
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        fields = []
    fallback_value: str | None = None
    for field in fields:
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if value in (None, ""):
            continue
        value_text = str(value)
        if fallback_value is None:
            fallback_value = value_text
        field_id = str(field.get("id", "")).lower()
        purpose = str(field.get("purpose", "")).lower()
        field_type = str(field.get("type", "")).lower()
        label = str(field.get("label", "")).lower()
        if (
            field_id == "password"
            or purpose == "password"
            or field_type == "concealed"
            or label in {"password", "value", "secret"}
        ):
            return value_text
    explicit_password = item.get("password")
    if isinstance(explicit_password, str) and explicit_password:
        return explicit_password
    return fallback_value


# ---------------------------------------------------------------------------
# Bitwarden helpers
# ---------------------------------------------------------------------------


# Sentinel to avoid repeatedly probing for the bw binary.
_bw_cli_found: bool | None = None


def _bw_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Execute a Bitwarden CLI command, returning None if bw is unavailable."""
    global _bw_cli_found  # noqa: PLW0603
    try:
        result = subprocess.run(
            ["bw", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        _bw_cli_found = True
        return result
    except FileNotFoundError:
        _bw_cli_found = False
        log.warning(
            "Bitwarden CLI ('bw') not found. Install it to manage project credentials."
        )
        return None
    except OSError as exc:
        _bw_cli_found = False
        log.error("Failed to execute Bitwarden CLI command: %s", exc)
        return None


def _bw_item_not_found(message: str) -> bool:
    normalized = message.lower()
    return any(
        candidate in normalized
        for candidate in (
            "not found",
            "no objects found",
            "search does not match",
            "not found in this vault",
            "we didn't find an item",
            "more than one item was found",
        )
    )


def _extract_bw_secret(item: dict) -> str | None:
    login = item.get("login", {})
    if isinstance(login, dict):
        password = login.get("password")
        if isinstance(password, str) and password:
            return password
    fields = item.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                name = str(field.get("name", "")).lower()
                if name in {"password", "value", "secret"}:
                    value = field.get("value")
                    if isinstance(value, str) and value:
                        return value
    notes = item.get("notes")
    if isinstance(notes, str) and notes:
        return notes
    return None


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class _CredentialBackend(abc.ABC):
    """Pluggable credential backend."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        ...

    @abc.abstractmethod
    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        ...

    @abc.abstractmethod
    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        ...

    def healthy(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# 1Password backend
# ---------------------------------------------------------------------------


class _OnePasswordBackend(_CredentialBackend):
    @property
    def name(self) -> str:
        return "1password"

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        title = _service_name(key, env)
        args = ["item", "get", title, "--format", "json"]
        vault = _op_vault()
        if vault:
            args.extend(["--vault", vault])
        result = _op_run(args)
        if result is None:
            return None
        if result.returncode != 0:
            error_blob = f"{result.stdout}\n{result.stderr}".strip()
            if _op_item_not_found(error_blob):
                return None
            log.error("Failed to read 1Password item '%s': %s", title, error_blob)
            return None
        try:
            payload = json_loads(result.stdout or "{}")
        except JSONDecodeError as exc:
            log.error("Invalid JSON received from 1Password for '%s': %s", title, exc)
            return None
        if not isinstance(payload, dict):
            log.error("Unexpected 1Password response payload type for '%s'", title)
            return None
        return _extract_op_secret(payload)

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        title = _service_name(key, env)
        account = _account_name(key, env)
        vault = _op_vault()
        edit_args = [
            "item",
            "edit",
            title,
            f"username={account}",
            f"password={value}",
        ]
        if vault:
            edit_args.extend(["--vault", vault])
        edit_result = _op_run(edit_args)
        if edit_result is None:
            return False
        if edit_result.returncode == 0:
            return True
        edit_error = f"{edit_result.stdout}\n{edit_result.stderr}".strip()
        if not _op_item_not_found(edit_error):
            log.error("Failed to update 1Password item '%s': %s", title, edit_error)
            return False
        create_args = [
            "item",
            "create",
            "--category",
            "password",
            "--title",
            title,
            f"username={account}",
            f"password={value}",
        ]
        if vault:
            create_args.extend(["--vault", vault])
        create_result = _op_run(create_args)
        if create_result is None:
            return False
        if create_result.returncode != 0:
            create_error = f"{create_result.stdout}\n{create_result.stderr}".strip()
            log.error("Failed to create 1Password item '%s': %s", title, create_error)
            return False
        return True

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        title = _service_name(key, env)
        args = ["item", "delete", title, "--archive"]
        vault = _op_vault()
        if vault:
            args.extend(["--vault", vault])
        result = _op_run(args)
        if result is None:
            return False
        if result.returncode == 0:
            return True
        error_blob = f"{result.stdout}\n{result.stderr}".strip()
        if _op_item_not_found(error_blob):
            return False
        log.error("Failed to delete 1Password item '%s': %s", title, error_blob)
        return False


# ---------------------------------------------------------------------------
# macOS Keychain backend
# ---------------------------------------------------------------------------


class _KeychainBackend(_CredentialBackend):
    @property
    def name(self) -> str:
        return "keychain"

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        if not _ensure_secure_keyring_backend():
            return None
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            return keyring.get_password(svc, acct)
        except Exception as exc:
            log.error("Failed to read keychain entry %s/%s: %s", svc, acct, exc)
            return None

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        if not _ensure_secure_keyring_backend():
            return False
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            keyring.set_password(svc, acct, value)
            return True
        except Exception as exc:
            log.error("Failed to write keychain entry %s/%s: %s", svc, acct, exc)
            return False

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        if not _ensure_secure_keyring_backend():
            return False
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            keyring.delete_password(svc, acct)
            return True
        except Exception as exc:
            log.error("Failed to delete keychain entry %s/%s: %s", svc, acct, exc)
            return False


# ---------------------------------------------------------------------------
# Bitwarden backend
# ---------------------------------------------------------------------------


class _BitwardenBackend(_CredentialBackend):
    """Bitwarden CLI backend with graceful handling of missing CLI,
    login state checks, and clear error messages."""

    @property
    def name(self) -> str:
        return "bitwarden"

    def login_check(self) -> str:
        """Verify Bitwarden vault state and return a descriptive status string.

        Returns:
            "unlocked" if the vault is unlocked and ready for use.
            "locked" if the user is logged in but the vault is locked.
            "unauthenticated" if the user is not logged in.
            "cli_not_found" if the `bw` binary is not available.
            "unknown" for any other state (parse errors, etc.).
        """
        result = _bw_run(["status"])
        if result is None:
            return "cli_not_found"
        if result.returncode != 0:
            error = f"{result.stdout}\n{result.stderr}".strip()
            log.debug("Bitwarden status command failed: %s", error)
            return "unknown"
        try:
            status = json_loads(result.stdout or "{}")
        except JSONDecodeError:
            log.debug("Bitwarden status returned invalid JSON")
            return "unknown"
        if not isinstance(status, dict):
            return "unknown"
        bw_status = str(status.get("status", "")).lower()
        if bw_status == "unlocked":
            return "unlocked"
        if bw_status == "locked":
            return "locked"
        if bw_status in {"unauthenticated", "not logged in", "not authenticated"}:
            return "unauthenticated"
        return "unknown"

    def healthy(self) -> bool:
        """Return True only when the bw CLI is present AND the vault is unlocked."""
        if _bw_cli_found is False:
            log.debug("Bitwarden CLI was previously detected as missing; skipping health check.")
            return False
        state = self.login_check()
        if state == "unlocked":
            return True
        if state == "cli_not_found":
            log.info("Bitwarden backend unhealthy: CLI not found.")
        elif state == "locked":
            log.info("Bitwarden backend unhealthy: vault is locked. Run 'bw unlock'.")
        elif state == "unauthenticated":
            log.info("Bitwarden backend unhealthy: not logged in. Run 'bw login'.")
        else:
            log.info("Bitwarden backend unhealthy: unknown vault state.")
        return False

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
        return _bw_run(arguments)

    def _item_title(self, key: str, env: str = DEFAULT_ENV) -> str:
        return _service_name(key, env)

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        title = self._item_title(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden get skipped: CLI not installed.")
            return None
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden get skipped: vault %s.", state)
            return None
        result = self._run(["get", "item", title, "--raw"])
        if result is None:
            return None
        if result.returncode != 0:
            error_blob = f"{result.stdout}\n{result.stderr}".strip()
            if _bw_item_not_found(error_blob):
                log.debug("Bitwarden item '%s' not found.", title)
                return None
            log.error("Failed to read Bitwarden item '%s': %s", title, error_blob)
            return None
        try:
            payload = json_loads(result.stdout or "{}")
        except JSONDecodeError as exc:
            log.error("Invalid JSON received from Bitwarden for '%s': %s", title, exc)
            return None
        if not isinstance(payload, dict):
            log.error("Unexpected Bitwarden response payload type for '%s'", title)
            return None
        return _extract_bw_secret(payload)

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        title = self._item_title(key, env)
        account = _account_name(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden store skipped: CLI not installed.")
            return False
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden store skipped: vault %s.", state)
            return False

        # Determine if the item already exists.
        get_result = self._run(["get", "item", title, "--raw"])
        if get_result is not None and get_result.returncode == 0:
            # Item exists — update via `bw edit item` using JSON stdin.
            edit_payload = {
                "login": {
                    "username": account,
                    "password": value,
                },
            }
            edit_json = json_dumps(edit_payload)
            edit_result = self._run(["edit", "item", title, edit_json])
            if edit_result is None:
                return False
            if edit_result.returncode == 0:
                return True
            error_blob = f"{edit_result.stdout}\n{edit_result.stderr}".strip()
            log.error("Failed to update Bitwarden item '%s': %s", title, error_blob)
            return False

        # Item does not exist — create via `bw create item login` using JSON stdin.
        create_payload = {
            "type": 1,  # Login
            "name": title,
            "login": {
                "username": account,
                "password": value,
            },
        }
        create_json = json_dumps(create_payload)
        create_result = self._run(["create", "item", "login", create_json, "--raw"])
        if create_result is None:
            return False
        if create_result.returncode != 0:
            error_blob = f"{create_result.stdout}\n{create_result.stderr}".strip()
            log.error("Failed to create Bitwarden item '%s': %s", title, error_blob)
            return False
        return True

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        title = self._item_title(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden delete skipped: CLI not installed.")
            return False
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden delete skipped: vault %s.", state)
            return False
        result = self._run(["delete", "item", title])
        if result is None:
            return False
        if result.returncode == 0:
            return True
        error_blob = f"{result.stdout}\n{result.stderr}".strip()
        if _bw_item_not_found(error_blob):
            log.debug("Bitwarden item '%s' not found for delete.", title)
            return False
        log.error("Failed to delete Bitwarden item '%s': %s", title, error_blob)
        return False


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, _CredentialBackend] = {
    "1password": _OnePasswordBackend(),
    "keychain": _KeychainBackend(),
    "bitwarden": _BitwardenBackend(),
}

_READ_FALLBACK_ORDER = ["1password", "keychain", "bitwarden"]
_WRITE_FALLBACK_ORDER = ["1password", "keychain", "bitwarden"]


def _active_backend_name() -> str:
    raw = os.environ.get(_CREDENTIAL_BACKEND_ENV, "1password").strip().lower()
    if raw in _BACKENDS:
        return raw
    log.warning("Unknown credential backend '%s'; falling back to 1password", raw)
    return "1password"


def _active_backend() -> _CredentialBackend:
    return _BACKENDS[_active_backend_name()]


# ---------------------------------------------------------------------------
# Public CRUD (env-aware, with shared fallback)
# ---------------------------------------------------------------------------

def _fallback_envs(env: str) -> list[str]:
    """Return env search chain: specific env → shared → prod."""
    if env == "prod":
        return ["prod"]
    return [env, "shared", "prod"]


def get_credential(key: str, *, env: str = DEFAULT_ENV) -> str | None:
    """Retrieve credential from the active backend, with cross-backend and env fallbacks."""
    # Try the active backend first across env fallback chain
    active = _active_backend()
    for try_env in _fallback_envs(env):
        val = active.get(key, env=try_env)
        if val:
            log.debug("Credential '%s' resolved from %s (env=%s)", key, active.name, try_env)
            return val

    # Fall back through remaining backends (reads only)
    for fallback_name in _READ_FALLBACK_ORDER:
        if fallback_name == active.name:
            continue
        backend = _BACKENDS[fallback_name]
        for try_env in _fallback_envs(env):
            val = backend.get(key, env=try_env)
            if val:
                log.debug(
                    "Credential '%s' resolved from %s fallback (env=%s)",
                    key,
                    fallback_name,
                    try_env,
                )
                return val

    # Legacy keychain fallback (read-only)
    legacy_key = key.replace("_", ".")
    if _ensure_secure_keyring_backend():
        try:
            val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
            if val:
                log.debug("Credential '%s' resolved from legacy keychain entry", key)
                return val
        except Exception:
            pass

    # Environment variable fallback (non-secret)
    return os.environ.get(key.upper())


def store_credential(key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
    """Store credential in the active backend."""
    active = _active_backend()
    if active.store(key, value, env=env):
        log.info("Stored %s in %s (env=%s)", key, active.name, env)
        return True
    log.error("Failed to store %s in %s", key, active.name)
    return False


def delete_credential(key: str, *, env: str = DEFAULT_ENV) -> bool:
    """Remove credential from the active backend."""
    return _active_backend().delete(key, env=env)


# ---------------------------------------------------------------------------
# Router-specific helpers
# ---------------------------------------------------------------------------
_ROUTER_KEYS = ("router_username", "router_password")


def get_router_credentials() -> tuple[str | None, str | None]:
    """Return (username, password) for router access using the runtime env."""
    env = os.environ.get("ASUSROUTERCONTROL_RUNTIME_ENV", "prod")
    return (
        get_credential("router_username", env=env),
        get_credential("router_password", env=env),
    )


# ---------------------------------------------------------------------------
# Migration from legacy entries
# ---------------------------------------------------------------------------

_LEGACY_KEY_MAP: dict[str, str] = {
    "router_username": "router.username",
    "router_password": "router.password",
}


def migrate_legacy_credentials(*, env: str = DEFAULT_ENV, dry_run: bool = False) -> list[str]:
    """Migrate keychain fallback entries into canonical storage.

    Returns list of migrated key names.
    """
    migrated: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        # Skip if canonical entry already exists in active backend
        if _active_backend().get(new_key, env=env):
            log.info("Skip %s — already exists in canonical store", new_key)
            continue
        # Prefer canonical keychain entry, then old legacy service.
        val = None
        if _ensure_secure_keyring_backend():
            try:
                val = keyring.get_password(_service_name(new_key, env), _account_name(new_key, env))
            except Exception:
                pass
        source = _service_name(new_key, env)
        if not val:
            if _ensure_secure_keyring_backend():
                try:
                    val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
                    source = f"{_LEGACY_SERVICE}.{legacy_key}"
                except Exception:
                    pass
        if not val:
            log.info("Skip %s — no keychain fallback entry found", new_key)
            continue
        if dry_run:
            log.info("Would migrate %s → %s", source, _service_name(new_key, env))
            migrated.append(new_key)
            continue
        if store_credential(new_key, val, env=env):
            migrated.append(new_key)
            log.info("Migrated %s → %s", source, _service_name(new_key, env))
    return migrated


def delete_legacy_credentials() -> list[str]:
    """Remove keychain fallback entries after migration to canonical storage."""
    if not _ensure_secure_keyring_backend():
        return []
    removed: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        canonical_service = _service_name(new_key)
        canonical_account = _account_name(new_key)
        try:
            keyring.delete_password(canonical_service, canonical_account)
            removed.append(canonical_service)
        except (keyring.errors.PasswordDeleteError, keyring.errors.KeyringError):
            pass
        legacy_service = f"{_LEGACY_SERVICE}.{legacy_key}"
        try:
            keyring.delete_password(legacy_service, "default")
            removed.append(legacy_service)
        except (keyring.errors.PasswordDeleteError, keyring.errors.KeyringError):
            pass
    return removed
