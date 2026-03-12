"""Secure credential management via macOS Keychain (keyring).

Follows Keychain-first with .env fallback pattern.
Service naming: com.asusroutercontrol.<key>
"""

from __future__ import annotations

import logging
import os

import keyring

SERVICE = "com.asusroutercontrol"

log = logging.getLogger(__name__)


def get_credential(key: str) -> str | None:
    """Retrieve credential: Keychain first, env fallback."""
    val = keyring.get_password(f"{SERVICE}.{key}", "default")
    if val:
        return val
    return os.environ.get(key.upper())


def store_credential(key: str, value: str) -> bool:
    """Store credential in macOS Keychain."""
    try:
        keyring.set_password(f"{SERVICE}.{key}", "default", value)
        log.info("Stored %s in Keychain", key)
        return True
    except Exception as e:
        log.error("Failed to store %s: %s", key, e)
        return False


def delete_credential(key: str) -> bool:
    """Remove credential from Keychain."""
    try:
        keyring.delete_password(f"{SERVICE}.{key}", "default")
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def get_router_credentials() -> tuple[str | None, str | None]:
    """Return (username, password) for router access."""
    return get_credential("router.username"), get_credential("router.password")
