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
    creds_mod._set_bw_unlock_error(None)
    creds_mod._clear_wrong_mp_cooldown()
    creds_mod._bw_unlock_miss_logged = False
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
    out = " ".join(result.output.lower().split())
    assert "one-time" in out
    assert "enter bitwarden master password to store in keychain" in out
    assert "will not ask for the master password" in out or (
        "no further master-password prompts" in out
    )
    assert creds_mod.get_bitwarden_master_password() == "new-mp"
    assert "Stored Bitwarden master password" in result.output


def test_bw_master_set_without_force_skips_prompt_when_quarantined(
    monkeypatch, mem_keyring
):
    """Quarantined wrong MP still counts as stored — require --force to re-prompt."""
    assert creds_mod.store_bitwarden_master_password("bad-mp") is True
    path = creds_mod.get_last_bitwarden_master_password_match()
    creds_mod._quarantine_wrong_mp(matched_path=path, secret="bad-mp")
    creds_mod._enter_wrong_mp_cooldown(matched_path=path)

    def boom(*_a, **_k):
        raise AssertionError("click.prompt must not run for --set when MP is quarantined")

    monkeypatch.setattr("asusroutercontrol.cli.click.prompt", boom)
    monkeypatch.setattr("asusroutercontrol.cli.ensure_bitwarden_unlocked", lambda: "locked")

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--set"])
    assert result.exit_code == 0, result.output
    assert "already stored" in result.output.lower()
    out = " ".join(result.output.split())
    assert "--force --set" in out
    assert "bw-master --status" in out
    assert "rejected by Bitwarden" in result.output


def test_bw_master_set_initial_shows_one_time_banner(monkeypatch, mem_keyring):
    creds_mod.delete_bitwarden_master_password()
    prompts: list[str] = []

    def fake_prompt(text, **kwargs):
        prompts.append(str(text))
        # confirmation_prompt may call prompt twice with different text
        return "fresh-mp"

    monkeypatch.setattr("asusroutercontrol.cli.click.prompt", fake_prompt)
    monkeypatch.setattr("asusroutercontrol.cli.ensure_bitwarden_unlocked", lambda: "unlocked")

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--set"])
    assert result.exit_code == 0, result.output
    assert prompts
    out = " ".join(result.output.split())
    assert "Enter Bitwarden master password to store in Keychain (one-time)" in out
    assert "One-time store complete" in out
    assert creds_mod.get_bitwarden_master_password() == "fresh-mp"


def test_bw_master_status_reports_boolean(monkeypatch, mem_keyring):
    assert creds_mod.store_bitwarden_master_password("status-mp") is True

    monkeypatch.setattr(
        "asusroutercontrol.cli.bitwarden_unlock_status",
        lambda *, attempt_unlock=False: {
            "master_password_stored": True,
            "master_password_matched_path": (
                "keyring:universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "master_password_quarantined_path": None,
            "wrong_mp_cooldown_active": False,
            "wrong_mp_cooldown_remaining_seconds": 0,
            "master_password_canonical": (
                "universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "lookups_tried": 1,
            "vault_status": "unlocked",
            "last_unlock_error": None,
            "bw_session": "valid",
            "keychain_path": None,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--status"])
    assert result.exit_code == 0, result.output
    assert "master_password_stored: true" in result.output
    assert "matched_path:" in result.output
    assert "universal-keychain-asusroutercontrol-prod-bw_master_password" in result.output
    assert "Bitwarden vault: unlocked" in result.output
    assert "bw_session: valid" in result.output
    assert "status-mp" not in result.output


def test_bw_master_status_wrong_mp_shows_status_not_force_set(monkeypatch, mem_keyring):
    assert creds_mod.store_bitwarden_master_password("bad-mp") is True

    monkeypatch.setattr(
        "asusroutercontrol.cli.bitwarden_unlock_status",
        lambda *, attempt_unlock=False: {
            "master_password_stored": True,
            "master_password_matched_path": (
                "keyring:universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "master_password_quarantined_path": (
                "keyring:universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "master_password_quarantined_count": 1,
            "master_password_candidate_count": 1,
            "master_password_usable_count": 0,
            "wrong_mp_cooldown_active": True,
            "wrong_mp_cooldown_remaining_seconds": 800,
            "master_password_canonical": (
                "universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "lookups_tried": 1,
            "shared_projects": ["grok", "shared"],
            "shared_services_searched": [
                "universal-keychain-grok-prod-bw_master_password",
                "universal-keychain-shared-prod-bw_master_password",
            ],
            "vault_status": "locked",
            "last_unlock_error": (
                "Keychain master password rejected by Bitwarden (wrong or corrupt). "
                "The asusroutercontrol Keychain item appears wrong/corrupt; also searched "
                "shared/Grok universal-keychain-* candidates automatically. "
                "Vault remains locked. Check: asusrouter credentials bw-master --status"
            ),
            "bw_session": "absent",
            "keychain_path": None,
            "master_password_usable": False,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--status"])
    assert result.exit_code == 0, result.output
    assert "Last unlock error:" in result.output
    assert "rejected by Bitwarden" in result.output
    assert "quarantined_path:" in result.output
    assert "wrong_mp_cooldown: active" in result.output
    assert "bw_session: absent" in result.output
    assert "import-from-keychain" in result.output or "bw unlock" in result.output
    # --force --set is dim/rare typing repair only, not the primary path.
    assert "[yellow]Repair:[/yellow]" not in result.output
    assert "not a BW outage" in result.output or "Decryption failed" in result.output
    assert "Rare typing repair" in result.output
    assert "bw-master --force --set" in result.output


def test_bw_master_status_missing(monkeypatch, mem_keyring):
    creds_mod.delete_bitwarden_master_password()

    monkeypatch.setattr(
        "asusroutercontrol.cli.bitwarden_unlock_status",
        lambda *, attempt_unlock=False: {
            "master_password_stored": False,
            "master_password_matched_path": None,
            "master_password_quarantined_path": None,
            "wrong_mp_cooldown_active": False,
            "wrong_mp_cooldown_remaining_seconds": 0,
            "master_password_canonical": (
                "universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "lookups_tried": 12,
            "shared_projects": ["grok", "shared"],
            "shared_services_searched": [
                "universal-keychain-grok-prod-bw_master_password",
            ],
            "vault_status": "locked",
            "last_unlock_error": "no master password",
            "bw_session": "absent",
            "keychain_path": None,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--status"])
    assert result.exit_code == 0, result.output
    assert "master_password_stored: false" in result.output
    assert "matched_path: (none)" in result.output
    assert "bw_session: absent" in result.output
    assert "bw unlock" in result.output or "scan-keychain" in result.output


def test_bw_master_import_from_keychain_success(monkeypatch, mem_keyring):
    monkeypatch.setattr(
        "asusroutercontrol.cli.import_bitwarden_master_password_from_keychain",
        lambda: {
            "ok": True,
            "source_path": (
                "keyring:universal-keychain-grok-prod-bw_master_password/"
                "grok.prod.bw_master_password"
            ),
            "canonical_path": (
                "universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "vault_status": "unlocked",
            "error": None,
            "fingerprint": "len=12",
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--import-from-keychain"])
    assert result.exit_code == 0, result.output
    assert "Copied proven-good" in result.output
    assert "universal-keychain-grok-prod-bw_master_password" in result.output
    assert "fingerprint: len=12" in result.output


def test_bw_master_import_from_keychain_failure(monkeypatch, mem_keyring):
    monkeypatch.setattr(
        "asusroutercontrol.cli.import_bitwarden_master_password_from_keychain",
        lambda: {
            "ok": False,
            "source_path": None,
            "canonical_path": (
                "universal-keychain-asusroutercontrol-prod-bw_master_password/"
                "asusroutercontrol.prod.bw_master_password"
            ),
            "vault_status": "locked",
            "error": "no non-ASUS / no valid session candidate (usable=0)",
            "fingerprint": None,
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--import-from-keychain"])
    assert result.exit_code == 1
    assert "Import failed" in result.output
    assert "source_path: (none)" in result.output
    assert "no non-ASUS" in result.output


def test_bw_master_scan_keychain(monkeypatch, mem_keyring):
    monkeypatch.setattr(
        "asusroutercontrol.cli.scan_keychain_bw_labels",
        lambda: [
            {"svce": "BW_SESSION", "acct": "dave", "labl": "Bitwarden session"},
            {
                "svce": "universal-keychain-asusroutercontrol-prod-bw_master_password",
                "acct": "asusroutercontrol.prod.bw_master_password",
                "labl": "",
            },
        ],
    )
    cleared: list[str] = []

    def fake_soft(*, reason=None):
        cleared.append(reason or "")

    monkeypatch.setattr("asusroutercontrol.cli.soft_clear_wrong_mp_cooldown", fake_soft)
    runner = CliRunner()
    result = runner.invoke(cli, ["credentials", "bw-master", "--scan-keychain"])
    assert result.exit_code == 0, result.output
    assert "keychain_labels: 2" in result.output
    assert "svce='BW_SESSION'" in result.output
    assert "acct='dave'" in result.output
    # Never print secrets — only metadata keys.
    assert "password" not in result.output.lower() or "Secrets omitted" in result.output
    assert cleared
