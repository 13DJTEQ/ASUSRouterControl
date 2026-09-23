"""Tests for credentials.py — pluggable backends (Bitwarden, 1Password, Keychain).

These tests exercise the backend registry, env-aware fallbacks, and the public
CRUD surface.  Backends are mocked in-memory so no real vault/keychain/CLI is
involved.
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
    def test_default_backend_is_bitwarden(self, monkeypatch):
        monkeypatch.delenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", raising=False)
        assert creds_mod._active_backend_name() == "bitwarden"

    def test_backend_env_switch(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keychain")
        assert creds_mod._active_backend_name() == "keychain"

    def test_unknown_backend_falls_back_to_bitwarden(self, monkeypatch):
        monkeypatch.setenv("ASUSROUTERCONTROL_CREDENTIAL_BACKEND", "keeper")
        assert creds_mod._active_backend_name() == "bitwarden"


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

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            raise AssertionError("unlock must not run without Keychain master password")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "locked"

    def test_ensure_stays_locked_when_unlock_fails(self, monkeypatch, mem_keyring):
        from types import SimpleNamespace

        from asusroutercontrol import credentials as creds

        monkeypatch.setitem(creds._BACKENDS, "keychain", creds._KeychainBackend())
        assert creds.store_bitwarden_master_password("wrong-mp") is True

        def fake_login_check(self):
            return "locked"

        def fake_bw_run(arguments, *, extra_env=None):
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid master password.")

        monkeypatch.setattr(creds._BitwardenBackend, "login_check", fake_login_check)
        monkeypatch.setattr(creds, "_bw_run", fake_bw_run)
        assert creds.ensure_bitwarden_unlocked() == "locked"

    def test_parse_bw_session_token_variants(self):
        from asusroutercontrol.credentials import _parse_bw_session_token

        assert _parse_bw_session_token("bare-token-value") == "bare-token-value"
        assert (
            _parse_bw_session_token('export BW_SESSION="quoted-token"\n') == "quoted-token"
        )
        assert _parse_bw_session_token("Enter master password:") == ""

    def test_is_login_blocked_error_detects_captcha(self):
        from asusroutercontrol.scheduler import _is_login_blocked_error

        assert _is_login_blocked_error(Exception("AccessError.CAPTCHA"))
        assert not _is_login_blocked_error(Exception("timeout contacting host"))
