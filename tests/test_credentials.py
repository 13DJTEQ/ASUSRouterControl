"""Tests for credentials.py — Keychain CRUD, legacy migration, env fallback.

These tests use monkeypatching to avoid touching the real macOS Keychain.
The keyring backend is replaced with a simple in-memory dict store.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# In-memory keyring backend for test isolation
# ---------------------------------------------------------------------------


class _InMemoryKeyring:
    """Minimal in-memory keyring backend — no macOS Keychain involvement."""

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            import keyring.errors
            raise keyring.errors.PasswordDeleteError(f"No entry for ({service}, {username})")
        del self._store[key]


@pytest.fixture
def mem_keyring(monkeypatch):
    """Patch the keyring module used by credentials.py with an in-memory store."""
    backend = _InMemoryKeyring()
    import asusroutercontrol.credentials as creds_mod
    monkeypatch.setattr(creds_mod.keyring, "get_password", backend.get_password)
    monkeypatch.setattr(creds_mod.keyring, "set_password", backend.set_password)
    monkeypatch.setattr(creds_mod.keyring, "delete_password", backend.delete_password)
    return backend


# ---------------------------------------------------------------------------
# store_credential / get_credential
# ---------------------------------------------------------------------------


class TestStoreAndGet:
    def test_store_then_get(self, mem_keyring):
        from asusroutercontrol.credentials import get_credential, store_credential

        assert store_credential("router_password", "s3cr3t") is True
        assert get_credential("router_password") == "s3cr3t"

    def test_missing_key_returns_none(self, mem_keyring, monkeypatch):
        from asusroutercontrol.credentials import get_credential

        monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
        assert get_credential("router_password") is None

    def test_env_fallback(self, mem_keyring, monkeypatch):
        from asusroutercontrol.credentials import get_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        # Nothing in keychain → falls through to env var
        assert get_credential("router_password") == "fromenv"

    def test_keychain_beats_env(self, mem_keyring, monkeypatch):
        from asusroutercontrol.credentials import get_credential, store_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        store_credential("router_password", "fromkeychain")
        assert get_credential("router_password") == "fromkeychain"

    def test_store_failure_returns_false(self, monkeypatch):
        """If keyring.set_password raises, store_credential returns False."""
        import asusroutercontrol.credentials as creds_mod

        def _raise(*a, **kw):
            raise RuntimeError("keychain locked")

        monkeypatch.setattr(creds_mod.keyring, "set_password", _raise)
        from asusroutercontrol.credentials import store_credential
        assert store_credential("router_password", "value") is False


# ---------------------------------------------------------------------------
# delete_credential
# ---------------------------------------------------------------------------


class TestDeleteCredential:
    def test_delete_existing(self, mem_keyring):
        from asusroutercontrol.credentials import delete_credential, store_credential

        store_credential("router_username", "admin")
        assert delete_credential("router_username") is True

    def test_delete_nonexistent_returns_false(self, mem_keyring):
        from asusroutercontrol.credentials import delete_credential

        assert delete_credential("router_username") is False


# ---------------------------------------------------------------------------
# get_router_credentials helper
# ---------------------------------------------------------------------------


class TestGetRouterCredentials:
    def test_returns_both(self, mem_keyring):
        from asusroutercontrol.credentials import get_router_credentials, store_credential

        store_credential("router_username", "admin")
        store_credential("router_password", "hunter2")
        user, pw = get_router_credentials()
        assert user == "admin"
        assert pw == "hunter2"

    def test_returns_none_none_when_empty(self, mem_keyring, monkeypatch):
        from asusroutercontrol.credentials import get_router_credentials

        monkeypatch.delenv("ROUTER_USERNAME", raising=False)
        monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
        user, pw = get_router_credentials()
        assert user is None
        assert pw is None


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


class TestMigrateLegacyCredentials:
    def _seed_legacy(self, mem_keyring, key: str, value: str):
        """Write a legacy-format entry directly into the in-memory keyring."""
        mem_keyring.set_password(f"com.asusroutercontrol.{key}", "default", value)

    def test_migrates_router_password(self, mem_keyring):
        from asusroutercontrol.credentials import (
            get_credential,
            migrate_legacy_credentials,
        )

        self._seed_legacy(mem_keyring, "router.password", "legacypass")
        migrated = migrate_legacy_credentials()
        assert "router_password" in migrated
        assert get_credential("router_password") == "legacypass"

    def test_dry_run_does_not_write(self, mem_keyring):
        from asusroutercontrol.credentials import (
            _account_name,
            _service_name,
            migrate_legacy_credentials,
        )

        self._seed_legacy(mem_keyring, "router.password", "legacypass")
        migrate_legacy_credentials(dry_run=True)
        # Dry run must NOT have written the canonical (universal-keychain) entry.
        # We check the keyring directly rather than via get_credential, because
        # get_credential falls back to the legacy entry and would return a value.
        assert mem_keyring.get_password(
            _service_name("router_password"), _account_name("router_password")
        ) is None

    def test_skips_already_migrated(self, mem_keyring):
        from asusroutercontrol.credentials import (
            migrate_legacy_credentials,
            store_credential,
        )

        store_credential("router_password", "already")
        self._seed_legacy(mem_keyring, "router.password", "old")
        migrated = migrate_legacy_credentials()
        assert "router_password" not in migrated
