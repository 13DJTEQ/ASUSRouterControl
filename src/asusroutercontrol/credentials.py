"""Secure credential management via macOS Keychain (keyring).

Uses the universal-keychain naming convention:
  Service: universal-keychain-asusroutercontrol-{env}-{key}
  Account: asusroutercontrol.{env}.{key}

Falls back to legacy ``com.asusroutercontrol.{key}`` entries for
backward compatibility.  Use ``migrate_legacy_credentials()`` to
promote old entries to the canonical format.
"""

from __future__ import annotations

import logging
import os

import keyring

# Universal-keychain canonical naming
PROJECT = "asusroutercontrol"
DEFAULT_ENV = "prod"

# Legacy prefix (deprecated — read-only fallback)
_LEGACY_SERVICE = "com.asusroutercontrol"

log = logging.getLogger(__name__)


def _service_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"universal-keychain-{PROJECT}-{env}-{key}"


def _account_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"{PROJECT}.{env}.{key}"


# ------------------------------------------------------------------
# Core CRUD
# ------------------------------------------------------------------

def get_credential(key: str, *, env: str = DEFAULT_ENV) -> str | None:
    """Retrieve credential: universal-keychain → legacy → env fallback."""
    # 1. Universal-keychain (canonical)
    val = keyring.get_password(_service_name(key, env), _account_name(key, env))
    if val:
        return val

    # 2. Legacy com.asusroutercontrol.{dotted_key} / account="default"
    legacy_key = key.replace("_", ".")
    val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
    if val:
        log.debug("Credential '%s' resolved from legacy keychain entry", key)
        return val

    # 3. Environment variable (non-secret fallback)
    return os.environ.get(key.upper())


def store_credential(key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
    """Store credential in macOS Keychain using universal-keychain naming."""
    try:
        keyring.set_password(
            _service_name(key, env),
            _account_name(key, env),
            value,
        )
        log.info("Stored %s in Keychain (env=%s)", key, env)
        return True
    except Exception as e:
        log.error("Failed to store %s: %s", key, e)
        return False


def delete_credential(key: str, *, env: str = DEFAULT_ENV) -> bool:
    """Remove credential from Keychain."""
    try:
        keyring.delete_password(_service_name(key, env), _account_name(key, env))
        return True
    except keyring.errors.PasswordDeleteError:
        return False


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
    """Promote legacy ``com.asusroutercontrol.*`` entries to universal-keychain format.

    Returns list of migrated key names.
    """
    migrated: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        # Skip if canonical entry already exists
        if keyring.get_password(_service_name(new_key, env), _account_name(new_key, env)):
            log.info("Skip %s — already exists in universal-keychain", new_key)
            continue

        val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
        if not val:
            log.info("Skip %s — no legacy entry found", new_key)
            continue

        if dry_run:
            log.info("Would migrate %s → %s", legacy_key, _service_name(new_key, env))
            migrated.append(new_key)
            continue

        store_credential(new_key, val, env=env)
        migrated.append(new_key)
        log.info("Migrated %s → %s", legacy_key, _service_name(new_key, env))
    return migrated


def delete_legacy_credentials() -> list[str]:
    """Remove deprecated ``com.asusroutercontrol.*`` entries after migration."""
    removed: list[str] = []
    for _new_key, legacy_key in _LEGACY_KEY_MAP.items():
        try:
            keyring.delete_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
            removed.append(legacy_key)
        except keyring.errors.PasswordDeleteError:
            pass
    return removed
