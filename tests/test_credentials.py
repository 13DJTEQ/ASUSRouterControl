"""Tests for credentials.py — live backends (Bitwarden, Keychain) + 1Password scaffold.

These tests exercise the backend registry, env-aware fallbacks, and the public
CRUD surface.  Backends are mocked in-memory so no real vault/keychain/CLI is
involved. 1Password is kept only to assert the scaffold remains inert.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    """Patch credentials.py to use in-memory Bitwarden + keyring stores."""
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

    # Reset module-level unlock / wrong-MP cooldown between tests.
    creds_mod._set_bw_unlock_error(None)
    creds_mod._clear_wrong_mp_cooldown()
    creds_mod._bw_unlock_miss_logged = False
    creds_mod._last_bw_mp_matched_path = None
    creds_mod._last_bw_mp_lookups_tried = []

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
    def test_default_backend_is_bitwarden(self, monkeypatch):
        monkeypatch.delenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", raising=False)
        assert creds_mod._active_backend_name() == "bitwarden"

    def test_backend_env_switch(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        assert creds_mod._active_backend_name() == "keychain"

    def test_unknown_backend_falls_back_to_bitwarden(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keeper")
        assert creds_mod._active_backend_name() == "bitwarden"

    def test_1password_env_is_ignored_as_scaffold(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "1password")
        assert creds_mod._active_backend_name() == "bitwarden"

    def test_1password_not_in_live_fallback_order(self):
        assert "1password" not in creds_mod._READ_FALLBACK_ORDER
        assert "1password" not in creds_mod._WRITE_FALLBACK_ORDER
        assert "1password" in creds_mod._BACKENDS  # scaffold still registered
        assert "1password" not in creds_mod._ACTIVE_BACKENDS


# ---------------------------------------------------------------------------
# store_credential / get_credential via active backend
# ---------------------------------------------------------------------------


class TestStoreAndGet:
    def test_store_then_get(self, monkeypatch, mem_keyring, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
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

    def test_bitwarden_beats_env(self, monkeypatch, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        from asusroutercontrol.credentials import get_credential, store_credential

        monkeypatch.setenv("ROUTER_PASSWORD", "fromenv")
        store_credential("router_password", "frombitwarden")
        assert get_credential("router_password") == "frombitwarden"

    def test_scaffold_1password_write_rejected(self, monkeypatch, op_store):
        from asusroutercontrol.credentials import store_credential

        assert store_credential("router_password", "nope", backend="1password") is False
        assert ("prod", "router_password") not in op_store

    def test_scaffold_1password_not_used_in_get_fallback(self, monkeypatch, op_store, bw_store):
        """Even with a seeded 1Password scaffold store, live get ignores it."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
        op_store[("prod", "router_password")] = "from1password"
        from asusroutercontrol.credentials import get_credential

        assert get_credential("router_password") is None

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
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.setattr(
            creds_mod._BACKENDS["bitwarden"], "store", lambda *a, **kw: False
        )
        from asusroutercontrol.credentials import store_credential

        assert store_credential("router_password", "value") is False

    def test_get_with_shared_env_fallback(self, monkeypatch, bw_store):
        """When env=dev and dev item is missing, fall back to shared."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
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
    def test_delete_existing(self, monkeypatch, mem_keyring, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        from asusroutercontrol.credentials import delete_credential, store_credential

        store_credential("router_username", "admin")
        assert delete_credential("router_username") is True

    def test_delete_nonexistent_returns_false(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.setattr(
            creds_mod._BACKENDS["bitwarden"], "delete", lambda *a, **kw: False
        )
        from asusroutercontrol.credentials import delete_credential

        assert delete_credential("router_username") is False


# ---------------------------------------------------------------------------
# get_router_credentials helper
# ---------------------------------------------------------------------------


class TestGetRouterCredentials:
    def test_returns_both(self, monkeypatch, mem_keyring, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
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

    def test_migrates_router_password(self, monkeypatch, mem_keyring, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
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

    def test_dry_run_does_not_write(self, monkeypatch, mem_keyring, bw_store):
        from asusroutercontrol.credentials import migrate_legacy_credentials

        self._seed_legacy(mem_keyring, "router.password", "legacypass")
        migrate_legacy_credentials(dry_run=True)
        assert ("prod", "router_password") not in bw_store

    def test_skips_already_migrated(self, monkeypatch, mem_keyring, bw_store):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
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


# ---------------------------------------------------------------------------
# Router SSH port + connect defaults
# ---------------------------------------------------------------------------


class TestRouterConnectionSecrets:
    def test_store_and_get_ssh_port(self, monkeypatch, mem_keyring):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        from asusroutercontrol.credentials import (
            get_router_ssh_port,
            store_router_credentials,
        )

        store_router_credentials("admin", "secret", ssh_port=1313, backend="keychain")
        assert get_router_ssh_port() == 1313

    def test_resolve_connect_login_defaults_prefills(self, monkeypatch, mem_keyring):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        from asusroutercontrol.credentials import (
            resolve_connect_login_defaults,
            store_router_credentials,
        )

        store_router_credentials("labadmin", "pw", ssh_port=2222, backend="keychain")
        defaults = resolve_connect_login_defaults(
            suggested_host="192.168.50.1",
            config_ssh_port=22,
            preferred_backend="keychain",
        )
        assert defaults["host"] == "192.168.50.1"
        assert defaults["username"] == "labadmin"
        assert defaults["password"] == "pw"
        assert defaults["ssh_port"] == 2222
        assert defaults["credential_backend"] == "keychain"
        assert defaults["password_from_store"] is True

    def test_get_router_ssh_port_from_active_backend(self, monkeypatch, mem_keyring):
        """SSH port is read from the active credential backend (BW or Keychain)."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        from asusroutercontrol.credentials import get_router_ssh_port, store_credential

        assert store_credential("router_ssh_port", "1313", backend="keychain")
        assert get_router_ssh_port() == 1313

    def test_host_hint_bw_ssh_port_overrides_stale_canonical(
        self, monkeypatch, mem_keyring
    ):
        """Stale Keychain port 22 must not mask the BW item SSH Port field."""
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        from asusroutercontrol import credentials as creds

        assert creds.store_credential("router_ssh_port", "22", backend="keychain")
        item = {
            "id": "abc",
            "name": "router.asus.com (13Maschine)",
            "login": {"username": "admin", "password": "pw", "uris": []},
            "fields": [{"name": "SSH Port", "value": "1313"}],
        }
        monkeypatch.setattr(
            creds, "lookup_bitwarden_router_item", lambda host_hint=None: item
        )
        assert creds.get_router_ssh_port(host_hint="router.asus.com") == 1313
        # Without a host hint, canonical store still wins.
        assert creds.get_router_ssh_port() == 22

    def test_invalid_ssh_port_rejected(self, monkeypatch, mem_keyring):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        from asusroutercontrol.credentials import store_router_credentials

        try:
            store_router_credentials("a", "b", ssh_port=70000, backend="keychain")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


class TestBitwardenRouterItemLookup:
    def test_ssh_port_from_human_bw_item_custom_field(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        item = {
            "id": "abc",
            "name": "router.asus.com (13Maschine)",
            "login": {
                "username": "admin",
                "password": "s3cret",
                "uris": [{"uri": "http://router.asus.com"}],
            },
            "fields": [{"name": "SSH Port", "value": "1313"}],
        }

        monkeypatch.setattr(creds, "get_credential", lambda key, env="prod": None)
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: item)
        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "unlocked")

        assert creds.get_router_ssh_port(host_hint="router.asus.com") == 1313
        user, pw = creds.get_router_credentials(host_hint="router.asus.com")
        assert user == "admin"
        assert pw == "s3cret"

        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            config_ssh_port=22,
            preferred_backend="bitwarden",
        )
        assert defaults["ssh_port"] == 1313
        assert defaults["username"] == "admin"
        assert defaults["password"] == "s3cret"
        assert defaults["credential_backend"] == "bitwarden"
        assert "1313" in str(defaults.get("store_detail") or "")

    def test_bw_password_strips_whitespace(self):
        from asusroutercontrol.credentials import _credentials_from_bitwarden_item

        item = {
            "login": {
                "username": " admin ",
                "password": " s3cret\n",
            }
        }
        user, pw = _credentials_from_bitwarden_item(item)
        assert user == "admin"
        assert pw == "s3cret"

    def test_fuzzy_and_uri_ssh_port_parsing(self):
        from asusroutercontrol.credentials import _ssh_port_from_bitwarden_item

        fuzzy = {
            "fields": [{"name": "Router SSH Port Number", "value": "1313"}],
            "login": {"uris": []},
        }
        assert _ssh_port_from_bitwarden_item(fuzzy) == 1313

        uri_item = {
            "fields": [],
            "login": {"uris": [{"uri": "ssh://admin@router.asus.com:2222"}]},
        }
        assert _ssh_port_from_bitwarden_item(uri_item) == 2222

    def test_locked_vault_defaults_to_keychain_backend(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "locked")
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "get_last_bitwarden_unlock_error", lambda: "no master password")
        assert creds.store_credential("router_username", "13Maschine", backend="keychain")
        assert creds.store_credential("router_password", "lab-secret", backend="keychain")
        assert creds.store_credential("router_ssh_port", "1313", backend="keychain")
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
        )
        assert defaults["credential_backend"] == "keychain"
        assert defaults["username"] == "13Maschine"
        assert defaults["password_from_store"] is True
        assert defaults["ssh_port"] == 1313
        detail = str(defaults.get("store_detail") or "").lower()
        assert "keychain-mirrored" in detail or "using keychain" in detail

    def test_resolve_blank_connect_password_fills_store(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        assert creds.store_credential(
            "router_username", "13Maschine", env="dev", backend="keychain"
        )
        assert creds.store_credential(
            "router_password", "lab-secret", env="dev", backend="keychain"
        )
        user, pw, source = creds.resolve_blank_connect_password(
            host="router.asus.com",
            username="admin",
            password="",
        )
        # Without env override, blank password fills from store; admin stays unless env set.
        assert pw == "lab-secret"
        assert "store" in source
        user2, pw2, source2 = creds.resolve_blank_connect_password(
            host="router.asus.com",
            username="",
            password="",
        )
        assert user2 == "13Maschine"
        assert pw2 == "lab-secret"

    def test_keychain_get_falls_back_to_security_cli(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setattr(creds, "_ensure_login_keychain_env", lambda: None)
        monkeypatch.setattr(creds, "_ensure_secure_keyring_backend", lambda: False)
        monkeypatch.setattr(
            creds,
            "_security_get_generic_password",
            lambda svc, acct: "from-security" if "router_password" in svc else None,
        )
        backend = creds._KeychainBackend()
        assert backend.get("router_password", env="dev") == "from-security"

    def test_locked_vault_detail_always_shown(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "locked")
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        monkeypatch.setattr(creds, "get_credential", lambda key, env="prod": None)
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            preferred_backend="keychain",
        )
        assert defaults["ssh_port"] == 22
        assert defaults["username"] == ""
        assert "locked" in str(defaults.get("store_detail") or "").lower()
        assert "login name" in str(defaults.get("store_detail") or "").lower()
        assert defaults["credential_backend"] == "keychain"

    def test_env_router_username_used_when_store_empty(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_USERNAME", "13Maschine")
        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "locked")
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        monkeypatch.setattr(creds, "get_credential", lambda key, env="prod": None)
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            preferred_backend="keychain",
        )
        assert defaults["username"] == "13Maschine"

    def test_env_username_overrides_stale_keychain_admin(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_USERNAME", "13Maschine")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        assert creds.store_credential("router_username", "admin", backend="keychain")
        assert creds.store_credential("router_password", "secret", backend="keychain")
        user, pw = creds.get_router_credentials(host_hint="router.asus.com")
        assert user == "13Maschine"
        assert pw == "secret"

    def test_env_ssh_port_used_when_bw_item_missing(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_SSH_PORT", "1313")
        monkeypatch.delenv("SSH_PORT", raising=False)
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        monkeypatch.setattr(creds, "get_credential", lambda key, env="prod": None)
        assert creds.get_router_ssh_port(host_hint="router.asus.com") == 1313

    def test_dev_env_file_username_not_overwritten_by_prod(
        self, monkeypatch, tmp_path: Path
    ):
        from asusroutercontrol import credentials as creds

        home = tmp_path / "home"
        dev_dir = home / ".asusroutercontrol.dev"
        prod_dir = home / ".asusroutercontrol"
        dev_dir.mkdir(parents=True)
        prod_dir.mkdir(parents=True)
        (dev_dir / ".env").write_text(
            "ASUSROUTERCONTROL_ROUTER_USERNAME=13Maschine\n"
            "ASUSROUTERCONTROL_ROUTER_SSH_PORT=1313\n"
            "BW_SESSION=dev-session\n",
            encoding="utf-8",
        )
        (prod_dir / ".env").write_text(
            "ASUSROUTERCONTROL_ROUTER_USERNAME=admin\n"
            "ASUSROUTERCONTROL_ROUTER_SSH_PORT=22\n"
            "BW_SESSION=prod-session\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setattr(creds.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(creds, "_ensure_bw_session_env", lambda: None)
        for key in (
            "ASUSROUTERCONTROL_ROUTER_USERNAME",
            "ROUTER_USERNAME",
            "ASUSROUTERCONTROL_ROUTER_SSH_PORT",
            "SSH_PORT",
            "BW_SESSION",
            "BITWARDEN_SESSION",
        ):
            monkeypatch.delenv(key, raising=False)

        creds.load_runtime_env_files()
        assert os.environ.get("ASUSROUTERCONTROL_ROUTER_USERNAME") == "13Maschine"
        assert os.environ.get("BW_SESSION") == "dev-session"
        assert os.environ.get("SSH_PORT") == "1313"
        # Re-bind through monkeypatch so values do not leak to later tests.
        for key in (
            "ASUSROUTERCONTROL_ROUTER_USERNAME",
            "ASUSROUTERCONTROL_ROUTER_SSH_PORT",
            "SSH_PORT",
            "BW_SESSION",
        ):
            monkeypatch.setenv(key, os.environ[key])

    def test_item_match_prefers_title_with_host(self):
        from asusroutercontrol.credentials import _bw_item_matches_host

        item = {"name": "router.asus.com (13Maschine)", "login": {"uris": []}}
        assert _bw_item_matches_host(item, "router.asus.com") is True
        assert _bw_item_matches_host(item, "other.example") is False



    def test_resolve_reports_locked_vault(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.delenv("ASUSROUTERCONTROL_ROUTER_SSH_PORT", raising=False)
        monkeypatch.delenv("SSH_PORT", raising=False)
        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "locked")
        monkeypatch.setattr(creds, "lookup_bitwarden_router_item", lambda host_hint=None: None)
        monkeypatch.setattr(creds, "get_credential", lambda key, env="prod": None)
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            preferred_backend="bitwarden",
        )
        assert defaults["ssh_port"] == 22
        assert "locked" in str(defaults.get("store_detail") or "").lower()



class TestConnectGuiSessionHelpers:
    def test_ensure_bw_session_reads_dev_env_file(self, monkeypatch, tmp_path: Path):
        from asusroutercontrol import credentials as creds

        home = tmp_path / "home"
        env_dir = home / ".asusroutercontrol.dev"
        env_dir.mkdir(parents=True)
        (env_dir / ".env").write_text("BW_SESSION=from-dev-env\n", encoding="utf-8")
        monkeypatch.setattr(creds.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.delenv("BW_SESSION", raising=False)
        monkeypatch.delenv("BITWARDEN_SESSION", raising=False)
        creds._ensure_bw_session_env()
        assert os.environ.get("BW_SESSION") == "from-dev-env"

    def test_persist_bw_session_writes_session_file(self, monkeypatch, tmp_path: Path):
        from asusroutercontrol import credentials as creds

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(creds.Path, "home", classmethod(lambda cls: home))
        monkeypatch.delenv("BW_SESSION", raising=False)
        creds._persist_bw_session("tok-persist-session")
        session_path = home / ".asusroutercontrol.dev" / "bw_session"
        assert session_path.read_text(encoding="utf-8").strip() == "tok-persist-session"
        assert os.environ.get("BW_SESSION") == "tok-persist-session"

    def test_format_connect_failure_includes_vault_status(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setattr(creds, "bitwarden_vault_status", lambda: "locked")
        monkeypatch.setattr(
            creds, "get_last_bitwarden_unlock_error", lambda: "no master password"
        )
        msg = creds.format_connect_failure_detail(
            ConnectionError("HTTP admin login failed"),
            host="router.asus.com",
            username="13Maschine",
            ssh_port=1313,
            password_present=True,
        )
        assert "HTTP admin login failed" in msg
        assert "Bitwarden vault: locked" in msg
        assert "no master password" in msg
        assert "host=router.asus.com" in msg
        assert "user='13Maschine'" in msg
        assert "ssh_port=1313" in msg
        assert "password=set" in msg

    def test_mirror_router_login_to_keychain(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(
            creds, "lookup_bitwarden_router_item", lambda host_hint=None: None
        )
        backend = creds.mirror_router_login_to_keychain(
            "13Maschine", "pw-secret", ssh_port=1313, env="dev"
        )
        assert backend == "keychain"
        user, pw = creds.get_router_credentials(host_hint="router.asus.com")
        assert user == "13Maschine"
        assert pw == "pw-secret"
        assert creds.get_router_ssh_port(host_hint="router.asus.com") == 1313
        # Also mirrored into prod so a mismatched runtime env still finds it.
        assert creds.get_credential("router_password", env="prod") == "pw-secret"
        assert creds.get_credential("router_username", env="prod") == "13Maschine"

    def test_keychain_get_falls_back_to_security_cli(self, monkeypatch, mem_keyring):
        """DEV.app ACL misses must still resolve via security find-generic-password."""
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        svc = creds._service_name("router_password", "dev")
        acct = creds._account_name("router_password", "dev")

        def fake_security(service: str, account: str | None) -> str | None:
            if service == svc and account == acct:
                return "mirrored-pw"
            return None

        monkeypatch.setattr(creds, "_security_get_generic_password", fake_security)
        # keyring has nothing — simulates ACL deny / wrong identity.
        assert creds._BACKENDS["keychain"].get("router_password", env="dev") == "mirrored-pw"

    def test_keychain_store_also_calls_security_allow_all(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        calls: list[tuple[str, str]] = []

        def fake_security_set(service: str, account: str, password: str) -> bool:
            calls.append((service, account))
            assert password == "secret-pw"
            return True

        monkeypatch.setattr(creds, "_security_set_generic_password", fake_security_set)
        assert creds._BACKENDS["keychain"].store(
            "router_password", "secret-pw", env="dev"
        )
        assert calls == [
            (
                creds._service_name("router_password", "dev"),
                creds._account_name("router_password", "dev"),
            )
        ]
        assert (
            creds._BACKENDS["keychain"].get("router_password", env="dev") == "secret-pw"
        )

    def test_keychain_store_requires_security_for_router_password(
        self, monkeypatch, mem_keyring
    ):
        """macOS: keyring-only write must not count as success for router_password."""
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        monkeypatch.setattr(creds, "_security_cli_available", lambda: True)
        monkeypatch.setattr(
            creds, "_security_set_generic_password", lambda *a, **k: False
        )
        ok = creds._BACKENDS["keychain"].store(
            "router_password", "secret-pw", env="dev"
        )
        assert ok is False
        # Username does not require security -A.
        assert creds._BACKENDS["keychain"].store(
            "router_username", "13Maschine", env="dev"
        )

    def test_keychain_store_requires_security_for_bw_master(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        monkeypatch.setattr(creds, "_security_cli_available", lambda: True)
        monkeypatch.setattr(
            creds, "_security_set_generic_password", lambda *a, **k: False
        )
        assert (
            creds._BACKENDS["keychain"].store(
                "bw_master_password", "mp-secret", env="prod"
            )
            is False
        )

    def test_mirror_fails_when_security_write_fails(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "_security_cli_available", lambda: True)
        monkeypatch.setattr(
            creds, "_security_set_generic_password", lambda *a, **k: False
        )
        with pytest.raises(RuntimeError, match="Keychain mirror failed"):
            creds.mirror_router_login_to_keychain(
                "13Maschine", "pw-secret", ssh_port=1313, env="dev"
            )

    def test_assert_connect_password_blocks_blank_when_locked(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "bitwarden_vault_status", lambda: "locked")
        monkeypatch.setattr(
            creds, "get_router_credentials", lambda host_hint=None: (None, None)
        )
        with pytest.raises(ConnectionError, match="bw_sync_router_env"):
            creds.assert_connect_password_ready("", host="router.asus.com")

    def test_assert_connect_password_allows_present(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        creds.assert_connect_password_ready("secret", host="router.asus.com")

    def test_resolve_blank_connect_password_fills_store_and_env(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_USERNAME", "13Maschine")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(
            creds, "lookup_bitwarden_router_item", lambda host_hint=None: None
        )
        creds.store_router_credentials(
            "admin", "from-keychain", ssh_port=1313, backend="keychain", env="dev"
        )
        user, pw, source = creds.resolve_blank_connect_password(
            host="router.asus.com",
            username="admin",
            password="",
        )
        assert user == "13Maschine"
        assert pw == "from-keychain"
        assert "store" in source
        assert "env-username" in source

    def test_locked_vault_uses_keychain_password_for_defaults(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_USERNAME", "13Maschine")
        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_SSH_PORT", "1313")
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(creds, "ensure_bitwarden_unlocked", lambda: "locked")
        monkeypatch.setattr(
            creds, "lookup_bitwarden_router_item", lambda host_hint=None: None
        )
        # Store via keychain backend explicitly (active is bitwarden).
        assert creds.store_credential(
            "router_password", "kc-pw", env="dev", backend="keychain"
        )
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            config_ssh_port=22,
        )
        assert defaults["username"] == "13Maschine"
        assert defaults["password"] == "kc-pw"
        assert defaults["ssh_port"] == 1313
        assert defaults["credential_backend"] == "keychain"
        assert defaults["password_from_store"] is True
        assert "Keychain-mirrored" in str(defaults.get("store_detail") or "")


class TestBitwardenMasterPasswordUnlock:
    def test_store_and_get_bw_master_password(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("s3cret-mp") is True
        assert creds.get_bitwarden_master_password() == "s3cret-mp"
        assert creds.delete_bitwarden_master_password() is True
        assert creds.get_bitwarden_master_password() is None

    def test_store_rejects_empty_password(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("   ") is False
        assert creds.get_bitwarden_master_password() is None

    def test_store_fails_without_keychain_backend(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", None)
        assert creds.store_bitwarden_master_password("s3cret-mp") is False
        assert creds.get_bitwarden_master_password() is None
        assert creds.delete_bitwarden_master_password() is False

    def test_ensure_already_unlocked_skips_unlock(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("s3cret-mp") is True
        calls: list[list[str]] = []

        def fake_login_check(self):
            return "unlocked"

        def fake_bw_run(arguments, *, extra_env=None):
            calls.append(list(arguments))
            raise AssertionError("unlock must not run when already unlocked")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert calls == []

    def test_ensure_unlocks_with_keychain_master(self, monkeypatch, mem_keyring, tmp_path):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("s3cret-mp") is True
        states = {"n": 0}
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(creds.Path, "home", classmethod(lambda cls: home))
        monkeypatch.delenv("BW_SESSION", raising=False)

        def fake_login_check(self):
            states["n"] += 1
            return "locked" if states["n"] == 1 else "unlocked"

        def fake_bw_run(arguments, *, extra_env=None):
            assert arguments[:2] == ["unlock", "--passwordenv"]
            assert arguments[2] == creds._BW_PASSWORD_ENV
            assert "--raw" in arguments
            assert extra_env == {creds._BW_PASSWORD_ENV: "s3cret-mp"}
            return SimpleNamespace(
                returncode=0,
                stdout="export BW_SESSION=\"tok-abc-session-value\"\n",
                stderr="",
            )

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert os.environ.get("BW_SESSION") == "tok-abc-session-value"
        env_text = (home / ".asusroutercontrol.dev" / ".env").read_text(encoding="utf-8")
        assert "BW_SESSION=tok-abc-session-value" in env_text
        assert creds.get_last_bitwarden_unlock_error() is None

    def test_ensure_unlock_raw_token(self, monkeypatch, mem_keyring):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("s3cret-mp") is True
        states = {"n": 0}

        def fake_login_check(self):
            states["n"] += 1
            return "locked" if states["n"] == 1 else "unlocked"

        def fake_bw_run(arguments, *, extra_env=None):
            return SimpleNamespace(returncode=0, stdout="SESSIONTOKEN\n", stderr="")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)

        def _persist(token: str) -> None:
            os.environ["BW_SESSION"] = token

        monkeypatch.setattr(creds, "_persist_bw_session", _persist)
        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert os.environ.get("BW_SESSION") == "SESSIONTOKEN"

    def test_ensure_stays_locked_without_master_password(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        # Ensure no leftover master password
        creds.delete_bitwarden_master_password()
        creds._set_bw_unlock_error(None)

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            raise AssertionError("unlock must not run without Keychain master password")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "locked"
        err = creds.get_last_bitwarden_unlock_error() or ""
        assert "import-from-keychain" in err
        assert "no master password" in err.lower()
        assert "bw-master --set" not in err or "import-from-keychain" in err

    def test_ensure_stays_locked_when_unlock_fails(self, monkeypatch, mem_keyring):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("wrong-mp") is True
        creds._set_bw_unlock_error(None)
        creds._clear_wrong_mp_cooldown()

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid master password.")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "locked"
        err = creds.get_last_bitwarden_unlock_error() or ""
        assert "wrong or corrupt" in err.lower() or "rejected by bitwarden" in err.lower()
        assert "bw-master --status" in err
        assert "bw-master --force --set" not in err
        assert creds.wrong_mp_cooldown_active() is True

    def test_decryption_failure_enters_cooldown_and_dedupes_log(
        self, monkeypatch, mem_keyring, caplog
    ):
        import logging
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("stale-mp") is True
        creds._clear_wrong_mp_cooldown()
        creds._set_bw_unlock_error(None)
        unlock_calls = {"n": 0}

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            unlock_calls["n"] += 1
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=(
                    "ERROR bitwarden_crypto::keys::master_key: "
                    "error=The decryption operation failed"
                ),
            )

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)

        with caplog.at_level(logging.WARNING):
            assert creds.ensure_bitwarden_unlocked() == "locked"
            assert creds.ensure_bitwarden_unlocked() == "locked"
            assert creds.ensure_bitwarden_unlocked() == "locked"

        assert unlock_calls["n"] == 1  # cooldown skips further bw unlock
        err = creds.get_last_bitwarden_unlock_error() or ""
        assert "Keychain master password rejected by Bitwarden" in err
        assert "bw-master --status" in err
        assert "bw-master --force --set" not in err
        assert creds.get_bitwarden_master_password_quarantine()
        warn_lines = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and (
                "master password rejected" in r.getMessage().lower()
                or "unlock via Keychain master password failed" in r.getMessage()
            )
        ]
        assert len(warn_lines) == 1

        info = creds.bitwarden_unlock_status(attempt_unlock=False)
        assert info["wrong_mp_cooldown_active"] is True
        assert info["master_password_quarantined_path"]
        assert info["master_password_matched_path"]
        assert "rejected by Bitwarden" in str(info["last_unlock_error"])

        # Successful store clears cooldown (rare --force --set repair path)
        assert creds.store_bitwarden_master_password("fresh-mp") is True
        assert creds.wrong_mp_cooldown_active() is False
        assert creds.get_bitwarden_master_password_quarantine() is None

    def test_store_purges_noncanonical_alt_mp_entries(self, monkeypatch, mem_keyring):
        """After store, discovery must not fall back to stale wrong alt keys."""
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        alt_svc = creds._service_name("bitwarden_master_password", "shared")
        alt_acct = creds._account_name("bitwarden_master_password", "shared")
        mem_keyring.set_password(alt_svc, alt_acct, "stale-wrong-mp")
        assert mem_keyring.get_password(alt_svc, alt_acct) == "stale-wrong-mp"

        assert creds.store_bitwarden_master_password("canonical-good-mp") is True
        assert mem_keyring.get_password(alt_svc, alt_acct) is None
        assert creds.get_bitwarden_master_password() == "canonical-good-mp"
        matched = creds.get_last_bitwarden_master_password_match() or ""
        assert "bw_master_password" in matched
        assert "shared" not in matched or "prod" in matched

    def test_discovery_skips_quarantined_path_prefers_other(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("bad-canonical") is True
        canon_path = creds.get_last_bitwarden_master_password_match()
        assert canon_path

        alt_svc = creds._service_name("bw_master_password", "dev")
        alt_acct = creds._account_name("bw_master_password", "dev")
        mem_keyring.set_password(alt_svc, alt_acct, "alt-good-mp")

        creds._quarantine_wrong_mp(matched_path=canon_path, secret="bad-canonical")
        # Quarantine skips canonical; discovery should land on the alt entry.
        assert creds.get_bitwarden_master_password() == "alt-good-mp"
        assert "dev" in (creds.get_last_bitwarden_master_password_match() or "")

        # Presence still true even if we only had the quarantined entry.
        assert creds.bitwarden_master_password_keychain_present() is True

    def test_ensure_reports_status_when_only_quarantined_mp(
        self, monkeypatch, mem_keyring
    ):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("only-bad") is True
        path = creds.get_last_bitwarden_master_password_match()
        creds._quarantine_wrong_mp(matched_path=path, secret="only-bad")
        creds._enter_wrong_mp_cooldown(matched_path=path)

        def fake_login_check(self):
            return "locked"

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(
            creds,
            "_bw_run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not unlock")),
        )

        assert creds.ensure_bitwarden_unlocked() == "locked"
        err = creds.get_last_bitwarden_unlock_error() or ""
        assert "bw-master --status" in err
        assert "bw-master --force --set" not in err
        assert "no master password" not in err.lower()

        info = creds.bitwarden_unlock_status(attempt_unlock=False)
        assert info["master_password_stored"] is True
        assert info["master_password_quarantined_path"]
        assert info["master_password_matched_path"]

    def test_ensure_tries_next_keychain_candidate_after_wrong_mp(
        self, monkeypatch, mem_keyring
    ):
        """Quarantine bad MP and unlock with another Keychain candidate — no prompt."""
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds._BACKENDS["keychain"].store(
            "bw_master_password", "wrong-mp", env="prod"
        )
        assert creds._BACKENDS["keychain"].store(
            "bw_master_password", "good-mp", env="shared"
        )
        creds._clear_wrong_mp_cooldown()
        unlock_passwords: list[str] = []

        def fake_login_check(self):
            if os.environ.get("BW_SESSION") == "GOODSESSION":
                return "unlocked"
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            pw = (extra_env or {}).get(creds._BW_PASSWORD_ENV, "")
            unlock_passwords.append(pw)
            if pw == "good-mp":
                return SimpleNamespace(returncode=0, stdout="GOODSESSION\n", stderr="")
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Invalid master password.",
            )

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)

        def _persist(token: str) -> None:
            os.environ["BW_SESSION"] = token

        monkeypatch.setattr(creds, "_persist_bw_session", _persist)
        monkeypatch.delenv("BW_SESSION", raising=False)

        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert unlock_passwords[0] == "wrong-mp"
        assert "good-mp" in unlock_passwords
        assert unlock_passwords.index("good-mp") > 0
        assert creds.wrong_mp_cooldown_active() is False
        assert os.environ.get("BW_SESSION") == "GOODSESSION"

    def test_connect_proceeds_with_mirror_when_wrong_mp_unlock_fails(
        self, monkeypatch, mem_keyring
    ):
        """Vault stays locked after wrong MP, but Keychain mirror still fills Connect."""
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "bitwarden")
        monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "dev")
        monkeypatch.setenv("ASUSROUTERCONTROL_ROUTER_USERNAME", "13Maschine")
        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        monkeypatch.setattr(creds, "load_runtime_env_files", lambda: None)
        monkeypatch.setattr(
            creds, "lookup_bitwarden_router_item", lambda host_hint=None: None
        )
        assert creds.store_bitwarden_master_password("wrong-mp") is True
        assert creds.store_credential(
            "router_password", "mirrored-secret", env="dev", backend="keychain"
        )
        creds._clear_wrong_mp_cooldown()

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=(
                    "ERROR bitwarden_crypto::keys::master_key: "
                    "error=The decryption operation failed"
                ),
            )

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)

        assert creds.ensure_bitwarden_unlocked() == "locked"
        user, pw, source = creds.resolve_blank_connect_password(
            host="router.asus.com",
            username="admin",
            password="",
        )
        assert user == "13Maschine"
        assert pw == "mirrored-secret"
        assert "store" in source
        # Fail-fast only when mirror is also missing — present password is fine.
        creds.assert_connect_password_ready(pw, host="router.asus.com")
        defaults = creds.resolve_connect_login_defaults(
            suggested_host="router.asus.com",
            config_ssh_port=22,
        )
        assert defaults["credential_backend"] == "keychain"
        assert defaults["password"] == "mirrored-secret"
        assert defaults["bw_status"] == "locked"

    def test_ensure_login_keychain_env_unsets_keychain_path(self, monkeypatch):
        from asusroutercontrol import credentials as creds

        monkeypatch.setenv("KEYCHAIN_PATH", "/tmp/fake.keychain-db")
        creds._ensure_login_keychain_env()
        assert "KEYCHAIN_PATH" not in os.environ

    def test_unlock_status_reports_fields(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        creds.delete_bitwarden_master_password()
        creds._set_bw_unlock_error("probe error")
        monkeypatch.delenv("BW_SESSION", raising=False)

        def fake_login_check(self):
            return "locked"

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        info = creds.bitwarden_unlock_status(attempt_unlock=False)
        assert info["master_password_stored"] is False
        assert info["vault_status"] == "locked"
        assert info["last_unlock_error"] == "probe error"
        assert info["bw_session_present"] is False
        assert info["keychain_path"] is None

    def test_bw_run_passes_stdin_devnull(self, monkeypatch):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        seen: dict = {}

        def fake_run(*args, **kwargs):
            seen["stdin"] = kwargs.get("stdin")
            return SimpleNamespace(returncode=0, stdout='{"status":"locked"}', stderr="")

        monkeypatch.setattr(creds.subprocess, "run", fake_run)
        monkeypatch.setattr(creds, "_bw_binaries", lambda: ["/usr/bin/bw"])
        monkeypatch.setattr(creds, "_ensure_bw_session_env", lambda: None)
        result = creds._bw_run(["status"])
        assert result is not None
        assert seen["stdin"] is creds.subprocess.DEVNULL

    def test_parse_bw_session_token_variants(self):
        from asusroutercontrol.credentials import _parse_bw_session_token

        assert _parse_bw_session_token("bare-token-value") == "bare-token-value"
        assert (
            _parse_bw_session_token('export BW_SESSION="quoted-token"\n') == "quoted-token"
        )
        assert _parse_bw_session_token("Enter master password:") == ""

    def test_classify_bw_unlock_failure(self):
        from asusroutercontrol.credentials import _classify_bw_unlock_failure

        assert "rejected by bitwarden" in _classify_bw_unlock_failure(
            "Invalid master password."
        ).lower()
        assert "bw-master --status" in _classify_bw_unlock_failure(
            "ERROR bitwarden_crypto::keys::master_key: error=The decryption operation failed"
        )
        assert "bw-master --force --set" not in _classify_bw_unlock_failure(
            "Invalid master password."
        )
        assert "not logged in" in _classify_bw_unlock_failure("You are not logged in").lower()

    def test_is_login_blocked_error_detects_captcha(self):
        from asusroutercontrol.scheduler import _is_login_blocked_error

        assert _is_login_blocked_error(Exception("AccessError.CAPTCHA"))
        assert not _is_login_blocked_error(Exception("timeout contacting host"))


class TestBitwardenMasterPasswordFallbacks:
    def test_get_reads_dev_env_without_eager_normalize(self, monkeypatch, mem_keyring):
        """Fallback reads must not overwrite canonical until unlock proves the MP."""
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        # Seed only under env=dev (not canonical prod).
        assert creds._BACKENDS["keychain"].store(
            "bw_master_password", "legacy-dev-mp", env="dev"
        )
        assert creds._BACKENDS["keychain"].get("bw_master_password", env="prod") is None

        assert creds.get_bitwarden_master_password() == "legacy-dev-mp"
        # Still not written to canonical — normalize happens after successful unlock.
        assert creds._BACKENDS["keychain"].get("bw_master_password", env="prod") is None

    def test_get_reads_alternate_key_name(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds._BACKENDS["keychain"].store(
            "bitwarden_master_password", "alt-key-mp", env="prod"
        )
        assert creds.get_bitwarden_master_password() == "alt-key-mp"
        # No eager normalize on get.
        assert creds._BACKENDS["keychain"].get("bw_master_password", env="prod") is None

    def test_get_reads_legacy_service_account(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        mem_keyring.set_password(
            "com.asusroutercontrol.bw_master_password",
            "default",
            "legacy-svc-mp",
        )
        assert creds.get_bitwarden_master_password() == "legacy-svc-mp"
        assert (
            mem_keyring.get_password(
                creds._service_name("bw_master_password", "prod"),
                creds._account_name("bw_master_password", "prod"),
            )
            is None
        )

    def test_ensure_unlocks_using_fallback_entry(self, monkeypatch, mem_keyring):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds._BACKENDS["keychain"].store(
            "bw_master_password", "fallback-mp", env="dev"
        )
        states = {"n": 0}

        def fake_login_check(self):
            states["n"] += 1
            return "locked" if states["n"] == 1 else "unlocked"

        def fake_bw_run(arguments, *, extra_env=None):
            assert extra_env == {creds._BW_PASSWORD_ENV: "fallback-mp"}
            return SimpleNamespace(returncode=0, stdout="SESSIONFROMFALLBACK\n", stderr="")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        def _persist(token: str) -> None:
            os.environ["BW_SESSION"] = token

        monkeypatch.setattr(creds, "_persist_bw_session", _persist)
        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert os.environ.get("BW_SESSION") == "SESSIONFROMFALLBACK"
        # Proven-good fallback is normalized to canonical after unlock.
        assert (
            creds._BACKENDS["keychain"].get("bw_master_password", env="prod")
            == "fallback-mp"
        )

    def test_status_reports_matched_path(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds._BACKENDS["keychain"].store(
            "bw_master_password", "path-mp", env="dev"
        )

        def fake_login_check(self):
            return "locked"

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        info = creds.bitwarden_unlock_status(attempt_unlock=False)
        assert info["master_password_stored"] is True
        assert info["master_password_matched_path"]
        assert "bw_master_password" in str(info["master_password_matched_path"])
        assert info["master_password_canonical"] == (
            f"{creds._bw_mp_canonical_service()}/{creds._bw_mp_canonical_account()}"
        )
        assert info["lookups_tried"] >= 1

    def test_get_via_security_fallback(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        # Ensure keyring has nothing; security stub returns the secret.
        creds.delete_bitwarden_master_password()

        svc = creds._service_name("bw_master_password", "shared")
        acct = creds._account_name("bw_master_password", "shared")

        def fake_security(service, account):
            if service == svc and account == acct:
                return "security-only-mp"
            return None

        monkeypatch.setattr(creds, "_security_get_generic_password", fake_security)
        monkeypatch.setattr(creds, "_security_set_generic_password", lambda *a, **k: True)
        assert creds.get_bitwarden_master_password() == "security-only-mp"
        match = creds.get_last_bitwarden_master_password_match()
        assert match is not None
        assert match.startswith("security:")
        assert svc in match
        # No eager normalize on get — canonical stays empty until unlock proves MP.
        assert creds._BACKENDS["keychain"].get("bw_master_password", env="prod") is None

    def test_get_logs_lookup_miss(self, monkeypatch, mem_keyring, caplog):
        import logging

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        creds.delete_bitwarden_master_password()
        monkeypatch.setattr(creds, "_security_get_generic_password", lambda *a, **k: None)
        with caplog.at_level(logging.INFO, logger="asusroutercontrol.credentials"):
            assert creds.get_bitwarden_master_password() is None
        assert any("lookup miss" in r.message.lower() for r in caplog.records)

    def test_sanitize_rejects_bw_session_and_strips_whitespace(self):
        from asusroutercontrol import credentials as creds

        assert creds._sanitize_bw_master_password_candidate("  good-mp\n") == "good-mp"
        assert creds._sanitize_bw_master_password_candidate('"quoted-mp"') == "quoted-mp"
        assert (
            creds._sanitize_bw_master_password_candidate('export BW_SESSION="tok"') is None
        )
        assert creds._sanitize_bw_master_password_candidate("BW_SESSION=tok") is None
        assert creds._mp_fingerprint("abcdef") == "len=6"

    def test_store_rejects_bw_session_token(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password('export BW_SESSION="abc"') is False
        assert creds.store_bitwarden_master_password("BW_SESSION=abc") is False
        assert creds.get_bitwarden_master_password() is None

    def test_discovery_finds_grok_shared_project(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        creds.delete_bitwarden_master_password()
        grok_svc = creds._uk_service("grok", "bw_master_password", "prod")
        grok_acct = creds._uk_account("grok", "bw_master_password", "prod")
        mem_keyring.set_password(grok_svc, grok_acct, "grok-good-mp\n")
        assert creds.get_bitwarden_master_password() == "grok-good-mp"
        matched = creds.get_last_bitwarden_master_password_match() or ""
        assert "universal-keychain-grok-prod-bw_master_password" in matched
        pairs = creds._bw_master_password_candidate_pairs()
        assert (grok_svc, grok_acct) in pairs
        shared = creds._shared_mp_services_catalog()
        assert any("grok" in s for s in shared)

    def test_purge_preserves_grok_shared_keychain_items(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        grok_svc = creds._uk_service("grok", "bw_master_password", "prod")
        grok_acct = creds._uk_account("grok", "bw_master_password", "prod")
        mem_keyring.set_password(grok_svc, grok_acct, "grok-keep-me")
        alt_svc = creds._service_name("bitwarden_master_password", "shared")
        alt_acct = creds._account_name("bitwarden_master_password", "shared")
        mem_keyring.set_password(alt_svc, alt_acct, "stale-asus-alt")

        assert creds.store_bitwarden_master_password("canonical-good-mp") is True
        # ASUSRouterControl alt purged; Grok shared item preserved.
        assert mem_keyring.get_password(alt_svc, alt_acct) is None
        assert mem_keyring.get_password(grok_svc, grok_acct) == "grok-keep-me"

    def test_discovery_expansion_clears_cooldown_for_new_grok_key(
        self, monkeypatch, mem_keyring
    ):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("bad-canonical") is True
        creds._clear_wrong_mp_cooldown()

        def fake_login_check(self):
            if os.environ.get("BW_SESSION") == "GROKSESSION":
                return "unlocked"
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            pw = (extra_env or {}).get(creds._BW_PASSWORD_ENV, "")
            if pw == "grok-good-mp":
                return SimpleNamespace(returncode=0, stdout="GROKSESSION\n", stderr="")
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Invalid master password.",
            )

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        monkeypatch.delenv("BW_SESSION", raising=False)

        def _persist(token: str) -> None:
            os.environ["BW_SESSION"] = token

        monkeypatch.setattr(creds, "_persist_bw_session", _persist)

        # First pass: only bad canonical → cooldown.
        assert creds.ensure_bitwarden_unlocked() == "locked"
        assert creds.wrong_mp_cooldown_active() is True

        # Simulate discovery expansion (new code with Grok pairs) by forcing
        # signature change + adding a Grok Keychain hit.
        creds._bw_mp_discovery_signature = "old-signature"
        grok_svc = creds._uk_service("grok", "bw_master_password", "prod")
        grok_acct = creds._uk_account("grok", "bw_master_password", "prod")
        mem_keyring.set_password(grok_svc, grok_acct, "grok-good-mp")

        assert creds.ensure_bitwarden_unlocked() == "unlocked"
        assert os.environ.get("BW_SESSION") == "GROKSESSION"
        assert creds.wrong_mp_cooldown_active() is False

    def test_import_from_keychain_copies_grok_mp(self, monkeypatch, mem_keyring):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        creds.delete_bitwarden_master_password()
        grok_svc = creds._uk_service("grok", "bw_master_password", "prod")
        grok_acct = creds._uk_account("grok", "bw_master_password", "prod")
        mem_keyring.set_password(grok_svc, grok_acct, "  grok-import-mp\n")

        def fake_login_check(self):
            if os.environ.get("BW_SESSION") == "IMPSESSION":
                return "unlocked"
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            pw = (extra_env or {}).get(creds._BW_PASSWORD_ENV, "")
            assert pw == "grok-import-mp"  # stripped
            return SimpleNamespace(returncode=0, stdout="IMPSESSION\n", stderr="")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        monkeypatch.setattr(creds, "_security_set_generic_password", lambda *a, **k: True)
        monkeypatch.delenv("BW_SESSION", raising=False)

        def _persist(token: str) -> None:
            os.environ["BW_SESSION"] = token

        monkeypatch.setattr(creds, "_persist_bw_session", _persist)

        info = creds.import_bitwarden_master_password_from_keychain()
        assert info["ok"] is True
        assert info["fingerprint"] == "len=14"
        assert "grok" in str(info["source_path"])
        assert creds._BACKENDS["keychain"].get("bw_master_password", env="prod") == (
            "grok-import-mp"
        )
        # Source Grok item left intact.
        assert mem_keyring.get_password(grok_svc, grok_acct) == "  grok-import-mp\n"

    def test_env_bw_password_candidate(self, monkeypatch, mem_keyring):
        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        creds.delete_bitwarden_master_password()
        monkeypatch.setattr(creds, "_security_get_generic_password", lambda *a, **k: None)
        monkeypatch.setenv("BW_PASSWORD", " env-injected-mp \n")
        assert creds.get_bitwarden_master_password() == "env-injected-mp"
        assert (creds.get_last_bitwarden_master_password_match() or "").startswith("env:")

