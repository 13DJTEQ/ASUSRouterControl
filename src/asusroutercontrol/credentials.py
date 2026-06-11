"""Secure credential management via 1Password (primary) with keychain fallback.

Canonical naming follows the universal-keychain convention:
  Service / 1Password item title: universal-keychain-asusroutercontrol-{env}-{key}
  Account metadata: asusroutercontrol.{env}.{key}

Primary writes use the 1Password CLI (`op`). Reads fall back to existing
macOS Keychain entries for backward compatibility and migration.
"""

from __future__ import annotations

import logging
import os
import subprocess
from json import JSONDecodeError
from json import loads as json_loads

import keyring

# Universal-keychain canonical naming
PROJECT = "asusroutercontrol"
DEFAULT_ENV = "prod"
_OP_ITEM_CATEGORY = "password"
_OP_VAULT_ENV = "ASUSROUTERCONTROL_1PASSWORD_VAULT"
_OP_VAULT_ENV_FALLBACK = "OP_VAULT"

# Legacy prefix (deprecated — read-only fallback)
_LEGACY_SERVICE = "com.asusroutercontrol"

log = logging.getLogger(__name__)


def _backend_name(backend: object) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__name__}"


def _is_fail_backend(backend: object) -> bool:
    return _backend_name(backend) == "keyring.backends.fail.Keyring"


def _build_macos_keyring():
    from keyring.backends.macOS import Keyring as MacOSKeyring

    return MacOSKeyring()


def _op_vault() -> str | None:
    value = os.environ.get(_OP_VAULT_ENV, "").strip()
    if value:
        return value
    fallback = os.environ.get(_OP_VAULT_ENV_FALLBACK, "").strip()
    return fallback or None


def _op_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["op", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        log.error(
            "1Password CLI ('op') not found. Install it to manage project credentials."
        )
        return None
    except Exception as exc:
        log.error("Failed to execute 1Password CLI command: %s", exc)
        return None


def _op_item_not_found(message: str) -> bool:
    normalized = message.lower()
    candidates = (
        "isn't an item",
        "could not find",
        "not found",
        "doesn't exist",
        "no item",
    )
    return any(candidate in normalized for candidate in candidates)


def _op_item_title(key: str, env: str = DEFAULT_ENV) -> str:
    return _service_name(key, env)


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


def _op_get_secret(key: str, *, env: str = DEFAULT_ENV) -> str | None:
    title = _op_item_title(key, env)
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


def _op_store_secret(key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
    title = _op_item_title(key, env)
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
        _OP_ITEM_CATEGORY,
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


def _op_delete_secret(key: str, *, env: str = DEFAULT_ENV) -> bool:
    title = _op_item_title(key, env)
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

    active_backend = keyring.get_keyring()
    if _is_fail_backend(active_backend):
        log.error(
            "Secure keychain backend unavailable (still active backend=%s)",
            _backend_name(active_backend),
        )
        return False

    log.info("Activated secure keychain backend: %s", _backend_name(active_backend))
    return True


def _keyring_get_password(service: str, account: str) -> str | None:
    if not _ensure_secure_keyring_backend():
        return None
    try:
        return keyring.get_password(service, account)
    except Exception as exc:
        log.error("Failed to read keychain entry %s/%s: %s", service, account, exc)
        return None


def _service_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"universal-keychain-{PROJECT}-{env}-{key}"


def _account_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"{PROJECT}.{env}.{key}"


# ------------------------------------------------------------------
# Core CRUD
# ------------------------------------------------------------------

def get_credential(key: str, *, env: str = DEFAULT_ENV) -> str | None:
    """Retrieve credential: 1Password → keychain fallbacks → env fallback."""
    # 1. 1Password (canonical primary store)
    op_val = _op_get_secret(key, env=env)
    if op_val:
        return op_val

    # 2. Universal-keychain in Keychain (canonical fallback)
    val = _keyring_get_password(_service_name(key, env), _account_name(key, env))
    if val:
        log.debug("Credential '%s' resolved from keychain fallback", key)
        return val

    # 3. Legacy com.asusroutercontrol.{dotted_key} / account="default"
    legacy_key = key.replace("_", ".")
    val = _keyring_get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
    if val:
        log.debug("Credential '%s' resolved from legacy keychain entry", key)
        return val

    # 4. Environment variable (non-secret fallback)
    return os.environ.get(key.upper())


def store_credential(key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
    """Store credential in 1Password using universal-keychain naming."""
    if not _op_store_secret(key, value, env=env):
        log.error("Failed to store %s in 1Password", key)
        return False
    log.info("Stored %s in 1Password (env=%s)", key, env)
    return True


def delete_credential(key: str, *, env: str = DEFAULT_ENV) -> bool:
    """Remove credential from 1Password."""
    return _op_delete_secret(key, env=env)


# ------------------------------------------------------------------
# Router-specific helpers
# ------------------------------------------------------------------

_ROUTER_KEYS = ("router_username", "router_password")


def get_router_credentials() -> tuple[str | None, str | None]:
    """Return (username, password) for router access."""
    return (
        get_credential("router_username"),
        get_credential("router_password"),
    )


# ------------------------------------------------------------------
# Migration from legacy entries
# ------------------------------------------------------------------

# Maps new canonical key → legacy dotted key
_LEGACY_KEY_MAP: dict[str, str] = {
    "router_username": "router.username",
    "router_password": "router.password",
}


def migrate_legacy_credentials(*, env: str = DEFAULT_ENV, dry_run: bool = False) -> list[str]:
    """Migrate keychain fallback entries into 1Password canonical storage.

    Returns list of migrated key names.
    """
    migrated: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        # Skip if canonical 1Password entry already exists
        if _op_get_secret(new_key, env=env):
            log.info("Skip %s — already exists in 1Password canonical store", new_key)
            continue

        # Prefer canonical keychain entry, then old legacy service.
        val = _keyring_get_password(_service_name(new_key, env), _account_name(new_key, env))
        source = _service_name(new_key, env)
        if not val:
            val = _keyring_get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
            source = f"{_LEGACY_SERVICE}.{legacy_key}"
        if not val:
            log.info("Skip %s — no keychain fallback entry found", new_key)
            continue

        if dry_run:
            log.info("Would migrate %s → %s", source, _op_item_title(new_key, env))
            migrated.append(new_key)
            continue

        if store_credential(new_key, val, env=env):
            migrated.append(new_key)
            log.info("Migrated %s → %s", source, _op_item_title(new_key, env))
    return migrated


def delete_legacy_credentials() -> list[str]:
    """Remove keychain fallback entries after migration to 1Password."""
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
