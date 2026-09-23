"""CLI tests for `asusrouter credentials bw-master`."""

from __future__ import annotations

import keyring
import pytest
from click.testing import CliRunner

import asusroutercontrol.credentials as creds_mod
from asusroutercontrol.cli import cli


class _InMemoryKeyring:
    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            raise keyring.errors.PasswordDeleteError(f"No entry for ({service}, {username})")
        del self._store[key]


@pytest.fixture
def mem_keyring(monkeypatch):
    backend = _InMemoryKeyring()
    state = {"backend": backend}
    monkeypatch.setattr(creds_mod.keyring, "get_keyring", lambda: state["backend"])
    monkeypatch.setattr(
        creds_mod.keyring, "set_keyring", lambda new_backend: state.update(backend=new_backend)
    )
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
    creds_mod._BACKENDS.clear()
    creds_mod._BACKENDS["1password"] = creds_mod._OnePasswordBackend()
    creds_mod._BACKENDS["keychain"] = creds_mod._KeychainBackend()
    creds_mod._BACKENDS["bitwarden"] = creds_mod._BitwardenBackend()
    return backend


def test_bw_master_set_skips_prompt_when_already_stored(monkeypatch, mem_keyring):
    assert creds_mod.store_bitwarden_master_password("already-there") is True

    def boom(*_a, **_k):
        raise AssertionError("click.prompt must not run when MP already stored")

    monkeypatch.setattr("asusroutercontrol.cli.click.prompt", boom)
    monkeypatch.setattr("asusroutercontrol.cli.ensure_bitwarden_unlocked", lambda: "unlocked")

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--set"])
    assert result.exit_code == 0, result.output
    assert "already stored" in result.output.lower()


def test_bw_master_set_force_prompts(monkeypatch, mem_keyring):
    assert creds_mod.store_bitwarden_master_password("old-mp") is True
    prompts: list[str] = []

    def fake_prompt(text, **kwargs):
        prompts.append(str(text))
        return "new-mp"

    monkeypatch.setattr("asusroutercontrol.cli.click.prompt", fake_prompt)
    monkeypatch.setattr("asusroutercontrol.cli.ensure_bitwarden_unlocked", lambda: "unlocked")

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--set", "--force"])
    assert result.exit_code == 0, result.output
    assert prompts, "expected prompt when --force is set"
    assert creds_mod.get_bitwarden_master_password() == "new-mp"
    assert "Stored Bitwarden master password" in result.output


def test_bw_master_status_reports_boolean(monkeypatch, mem_keyring):
    assert creds_mod.store_bitwarden_master_password("status-mp") is True

    monkeypatch.setattr(
        "asusroutercontrol.cli.bitwarden_unlock_status",
        lambda *, attempt_unlock=False: {
            "master_password_stored": True,
            "vault_status": "unlocked",
            "last_unlock_error": None,
            "bw_session_present": True,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--status"])
    assert result.exit_code == 0, result.output
    assert "master_password_stored: true" in result.output
    assert "Bitwarden vault: unlocked" in result.output
    assert "status-mp" not in result.output


def test_bw_master_status_missing(monkeypatch, mem_keyring):
    creds_mod.delete_bitwarden_master_password()

    monkeypatch.setattr(
        "asusroutercontrol.cli.bitwarden_unlock_status",
        lambda *, attempt_unlock=False: {
            "master_password_stored": False,
            "vault_status": "locked",
            "last_unlock_error": "no master password",
            "bw_session_present": False,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--status"])
    assert result.exit_code == 0, result.output
    assert "master_password_stored: false" in result.output
    assert "bw-master --set" in result.output
