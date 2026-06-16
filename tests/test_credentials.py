"""Tests for credentials.py — pluggable backends (1Password, Keychain, Bitwarden).

These tests exercise the backend registry, env-aware fallbacks, and the public
CRUD surface.  Backends are mocked in-memory so no real vault/keychain/CLI is
involved.
"""

from __future__ import annotations

import pytest

import asusroutercontrol.credentials as creds_mod

# ---------------------------------------------------------------------------
# In-memory stores for test isolation
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


@pytest.fixture(autouse=True)
def _reset_backend_registry(monkeypatch):
    """Ensure a clean backend registry for every test."""
    # Re-instantiate all backends so no state leaks between tests.
    creds_mod._BACKENDS.clear()
    creds_mod._BACKENDS["1password"] = creds_mod._OnePasswordBackend()
    creds_mod._BACKENDS["keychain"] = creds_mod._KeychainBackend()
    creds_mod._BACKENDS["bitwarden"] = creds_mod._BitwardenBackend()


@pytest.fixture
def credential_stores(monkeypatch):
    """Patch credentials.py to use in-memory 1Password + keyring stores."""
    backend = _InMemoryKeyring()
    op_store: dict[tuple[str, str], str] = {}
    bw_store: dict[tuple[str, str], str] = {}

    state = {"backend": backend}

    monkeypatch.setattr(creds_mod.keyring, "get_keyring", lambda: state["backend"])

    def _set_keyring(new_backend):
        state["backend"] = new_backend

    monkeypatch.setattr(creds_mod.keyring, "set_keyring", _set_keyring)
    monkeypatch.setattr(
        creds_mod.keyring,
        "get_password",
        lambda service, username: state["backend"].get_password(service, username),
    )
    monkeypatch.setattr(
        creds_mod.keyring,
        "set_password",
        lambda service, username, password: state["backend"].set_password(
            service, username, password
        ),
    )
    monkeypatch.setattr(
        creds_mod.keyring,
        "delete_password",
        lambda service, username: state["backend"].delete_password(service, username),
    )

    def _op_get_secret(key: str, *, env: str = "prod") -> str | None:
        return op_store.get((env, key))

    def _op_store_secret(key: str, value: str, *, env: str = "prod") -> bool:
        op_store[(env, key)] = value
        return True

    def _op_delete_secret(key: str, *, env: str = "prod") -> bool:
        return op_store.pop((env, key), None) is not None

    # Patch 1Password backend methods directly
    op_backend = creds_mod._BACKENDS["1password"]
    monkeypatch.setattr(op_backend, "get", _op_get_secret)
    monkeypatch.setattr(op_backend, "store", _op_store_secret)
    monkeypatch.setattr(op_backend, "delete", _op_delete_secret)

    # Patch Bitwarden backend methods directly
    def _bw_get_secret(key: str, *, env: str = "prod") -> str | None:
        return bw_store.get((env, key))

    def _bw_store_secret(key: str, value: str, *, env: str = "prod") -> bool:
        bw_store[(env, key)] = value
        return True

    def _bw_delete_secret(key: str, *, env: str = "prod") -> bool:
        return bw_store.pop((env, key), None) is not None

    bw_backend = creds_mod._BACKENDS["bitwarden"]
    monkeypatch.setattr(bw_backend, "get", _bw_get_secret)
    monkeypatch.setattr(bw_backend, "store", _bw_store_secret)
    monkeypatch.setattr(bw_backend, "delete", _bw_delete_secret)

    return {"keyring": backend, "op": op_store, "bw": bw_store}


@pytest.fixture
def mem_keyring(credential_stores):
    return credential_stores["keyring"]


@pytest.fixture
def op_store(credential_stores):
    return credential_stores["op"]


@pytest.fixture
def bw_store(credential_stores):
    return credential_stores["bw"]


# ---------------------------------------------------------------------------
# Backend selection tests
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_default_backend_is_1password(self, monkeypatch):
        monkeypatch.delenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", raising=False)
        assert creds_mod._active_backend_name() == "1password"

    def test_backend_env_switch(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        assert creds_mod._active_backend_name() == "keychain"

    def test_unknown_backend_falls_back_to_1password(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keeper")
        assert creds_mod._active_backend_name() == "1password"


# ---------------------------------------------------------------------------
# store_credential / get_credential via active backend
# ---------------------------------------------------------------------------


class TestStoreAndGet:
    def test_store_then_get(self, monkeypatch, mem_keyring, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import get_credential, store_credential

        assert store_credential("router_password", "s3cr3t") is True
        assert get_credential("router_password") == "s3cr3t"

    def test_missing_key_returns_none(self, monkeypatch, mem_keyring):
        monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
        from asusroutercontrol.credentials import get_credential

        assert get_credential("router_password") is None

    def test_env_fallback(self, monkeypatch, mem_keyring):
        from asusroutercontrol.credentials import get_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        assert get_credential("router_password") == "fromenv"

    def test_1password_beats_env(self, monkeypatch, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import get_credential, store_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        store_credential("router_password", "from1password")
        assert get_credential("router_password") == "from1password"

    def test_keychain_fallback_beats_env(self, monkeypatch, mem_keyring):
        from asusroutercontrol.credentials import _account_name, _service_name, get_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        mem_keyring.set_password(
            _service_name("router_password"),
            _account_name("router_password"),
            "fromkeychain",
        )
        assert get_credential("router_password") == "fromkeychain"

    def test_legacy_keychain_fallback(self, mem_keyring):
        from asusroutercontrol.credentials import get_credential

        mem_keyring.set_password(
            "com.asusroutercontrol.router.password",
            "default",
            "legacy",
        )
        assert get_credential("router_password") == "legacy"

    def test_store_failure_returns_false(self, monkeypatch):
        """If backend write fails, store_credential returns False."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        monkeypatch.setattr(
            creds_mod._BACKENDS["1password"], "store", lambda *a, **kw: False
        )
        from asusroutercontrol.credentials import store_credential

        assert store_credential("router_password", "value") is False

    def test_get_with_shared_env_fallback(self, monkeypatch, op_store):
        """When env=dev and dev item is missing, fall back to shared."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import get_credential, store_credential

        store_credential("router_password", "sharedpass", env="shared")
        assert get_credential("router_password", env="dev") == "sharedpass"

    def test_bitwarden_backend_store_and_get(self, monkeypatch, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        from asusroutercontrol.credentials import get_credential, store_credential

        assert store_credential("router_password", "bwsecret") is True
        assert get_credential("router_password") == "bwsecret"


# ---------------------------------------------------------------------------
# delete_credential
# ---------------------------------------------------------------------------


class TestDeleteCredential:
    def test_delete_existing(self, monkeypatch, mem_keyring, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import delete_credential, store_credential

        store_credential("router_username", "admin")
        assert delete_credential("router_username") is True

    def test_delete_nonexistent_returns_false(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        monkeypatch.setattr(
            creds_mod._BACKENDS["1password"], "delete", lambda *a, **kw: False
        )
        from asusroutercontrol.credentials import delete_credential

        assert delete_credential("router_username") is False


# ---------------------------------------------------------------------------
# get_router_credentials helper
# ---------------------------------------------------------------------------


class TestGetRouterCredentials:
    def test_returns_both(self, monkeypatch, mem_keyring, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import get_router_credentials, store_credential

        store_credential("router_username", "admin")
        store_credential("router_password", "hunter2")
        user, pw = get_router_credentials()
        assert user == "admin"
        assert pw == "hunter2"

    def test_returns_none_none_when_empty(self, monkeypatch, mem_keyring):
        monkeypatch.delenv("ROUTER_USERNAME", raising=False)
        monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
        from asusroutercontrol.credentials import get_router_credentials

        user, pw = get_router_credentials()
        assert user is None
        assert pw is None


# ---------------------------------------------------------------------------
# Migration + cleanup
# ---------------------------------------------------------------------------


class TestMigrateLegacyCredentials:
    def _seed_legacy(self, mem_keyring, key: str, value: str):
        mem_keyring.set_password(f"com.asusroutercontrol.{key}", "default", value)

    def test_migrates_router_password(self, monkeypatch, mem_keyring, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import get_credential, migrate_legacy_credentials

        self._seed_legacy(mem_keyring, "router.password", "legacypass")
        migrated = migrate_legacy_credentials()
        assert "router_password" in migrated
        assert get_credential("router_password") == "legacypass"

    def test_migrates_from_canonical_keychain_fallback(self, monkeypatch, mem_keyring, op_store):
        from asusroutercontrol.credentials import (
            _account_name,
            _service_name,
            get_credential,
            migrate_legacy_credentials,
        )

        mem_keyring.set_password(
            _service_name("router_password"),
            _account_name("router_password"),
            "canonical-fallback",
        )
        migrated = migrate_legacy_credentials()
        assert "router_password" in migrated
        assert get_credential("router_password") == "canonical-fallback"

    def test_dry_run_does_not_write(self, monkeypatch, mem_keyring, op_store):
        from asusroutercontrol.credentials import migrate_legacy_credentials

        self._seed_legacy(mem_keyring, "router.password", "legacypass")
        migrate_legacy_credentials(dry_run=True)
        assert ("prod", "router_password") not in op_store

    def test_skips_already_migrated(self, monkeypatch, mem_keyring, op_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        from asusroutercontrol.credentials import migrate_legacy_credentials, store_credential

        store_credential("router_password", "already")
        self._seed_legacy(mem_keyring, "router.password", "old")
        migrated = migrate_legacy_credentials()
        assert "router_password" not in migrated


class TestDeleteLegacyCredentials:
    def test_cleanup_removes_canonical_and_legacy_entries(self, mem_keyring):
        from asusroutercontrol.credentials import (
            _account_name,
            _service_name,
            delete_legacy_credentials,
        )

        mem_keyring.set_password(
            _service_name("router_password"),
            _account_name("router_password"),
            "canonical",
        )
        mem_keyring.set_password(
            "com.asusroutercontrol.router.password",
            "default",
            "legacy",
        )

        removed = delete_legacy_credentials()

        assert _service_name("router_password") in removed
        assert "com.asusroutercontrol.router.password" in removed
        assert (
            mem_keyring.get_password(
                _service_name("router_password"),
                _account_name("router_password"),
            )
            is None
        )
        assert (
            mem_keyring.get_password(
                "com.asusroutercontrol.router.password",
                "default",
            )
            is None
        )
