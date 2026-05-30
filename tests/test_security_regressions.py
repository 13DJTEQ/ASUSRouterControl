from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import pytest
from asusroutercontrol.merlin.entware import _validate_package_name
from asusroutercontrol.merlin.jffs import _validate_script_name

from asusroutercontrol.ssh import (
    HostKeyMismatchError,
    RouterSSH,
    UnknownHostKeyError,
    _fingerprints_from_key_blob,
)


@dataclass
class _Cfg:
    router_host: str = "router.asus.com"
    ssh_port: int = 22
    data_dir: Path = Path(".")
    ssh_trust_mode: str = "tofu_confirm"
    ssh_host_key_fingerprint: str | None = None
    ssh_known_hosts_path: Path | None = None


class _FakeConn:
    def __init__(self, key_blob: str) -> None:
        self.closed = False
        self._key_blob = key_blob

    def get_server_host_key(self):
        return asyncssh.import_public_key(self._key_blob.encode())

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _make_key_blob() -> str:
    return asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()


def test_validate_script_name_rejects_injection() -> None:
    assert _validate_script_name("services-start") == "services-start"
    with pytest.raises(ValueError):
        _validate_script_name("services-start;rm -rf /")


def test_validate_package_name_rejects_injection() -> None:
    assert _validate_package_name("htop") == "htop"
    with pytest.raises(ValueError):
        _validate_package_name("htop && reboot")


@pytest.mark.asyncio
async def test_routerssh_strict_mode_unknown_host_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_blob = _make_key_blob()

    def _load_config():
        return _Cfg(data_dir=tmp_path, ssh_trust_mode="strict")

    def _get_creds():
        return ("admin", "secret")

    async def _fake_connect(hostname, **kwargs):
        return _FakeConn(key_blob)

    monkeypatch.setattr("asusroutercontrol.ssh.load_config", _load_config)
    monkeypatch.setattr("asusroutercontrol.ssh.get_router_credentials", _get_creds)
    monkeypatch.setattr("asusroutercontrol.ssh.asyncssh.connect", _fake_connect)

    ssh = RouterSSH(hostname="router.local", port=2222)
    with pytest.raises(UnknownHostKeyError):
        await ssh.connect()
    assert not (tmp_path / "known_hosts").exists()


@pytest.mark.asyncio
async def test_routerssh_tofu_confirm_emits_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_blob = _make_key_blob()
    exp_sha, exp_md5 = _fingerprints_from_key_blob(key_blob)

    def _load_config():
        return _Cfg(data_dir=tmp_path, ssh_trust_mode="tofu_confirm")

    def _get_creds():
        return ("admin", "secret")

    async def _fake_connect(hostname, **kwargs):
        return _FakeConn(key_blob)

    monkeypatch.setattr("asusroutercontrol.ssh.load_config", _load_config)
    monkeypatch.setattr("asusroutercontrol.ssh.get_router_credentials", _get_creds)
    monkeypatch.setattr("asusroutercontrol.ssh.asyncssh.connect", _fake_connect)

    ssh = RouterSSH(hostname="router.local", port=2222)
    with pytest.raises(UnknownHostKeyError) as exc:
        await ssh.connect()
    assert exc.value.details.sha256 == exp_sha
    assert exc.value.details.md5 == exp_md5
    assert exc.value.details.host_token == "[router.local]:2222"


@pytest.mark.asyncio
async def test_routerssh_mismatch_reports_old_and_new_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_blob = _make_key_blob()
    new_blob = _make_key_blob()
    old_sha, _ = _fingerprints_from_key_blob(old_blob)
    new_sha, _ = _fingerprints_from_key_blob(new_blob)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"router.local {old_blob}\n", encoding="utf-8")

    def _load_config():
        return _Cfg(data_dir=tmp_path, ssh_trust_mode="strict")

    def _get_creds():
        return ("admin", "secret")

    async def _fake_connect(hostname, **kwargs):
        return _FakeConn(new_blob)

    monkeypatch.setattr("asusroutercontrol.ssh.load_config", _load_config)
    monkeypatch.setattr("asusroutercontrol.ssh.get_router_credentials", _get_creds)
    monkeypatch.setattr("asusroutercontrol.ssh.asyncssh.connect", _fake_connect)

    ssh = RouterSSH(hostname="router.local", port=22)
    with pytest.raises(HostKeyMismatchError) as exc:
        await ssh.connect()
    assert exc.value.expected_sha256 == old_sha
    assert exc.value.presented_sha256 == new_sha


@pytest.mark.asyncio
async def test_routerssh_rotate_replaces_only_target_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_blob = _make_key_blob()
    new_blob = _make_key_blob()
    other_blob = _make_key_blob()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "# keep-comment\n"
        f"router.local {old_blob}\n"
        f"other.local {other_blob}\n",
        encoding="utf-8",
    )

    def _load_config():
        return _Cfg(data_dir=tmp_path, ssh_trust_mode="strict")

    def _get_creds():
        return ("admin", "secret")

    async def _fake_connect(hostname, **kwargs):
        return _FakeConn(new_blob if hostname == "router.local" else other_blob)

    monkeypatch.setattr("asusroutercontrol.ssh.load_config", _load_config)
    monkeypatch.setattr("asusroutercontrol.ssh.get_router_credentials", _get_creds)
    monkeypatch.setattr("asusroutercontrol.ssh.asyncssh.connect", _fake_connect)

    ssh = RouterSSH(hostname="router.local", port=22)
    details = await ssh.rotate_pinned_host_key()
    assert details.host_token == "router.local"
    content = known_hosts.read_text(encoding="utf-8")
    assert "# keep-comment" in content
    assert f"router.local {new_blob}" in content
    assert f"other.local {other_blob}" in content
    assert f"router.local {old_blob}" not in content


@pytest.mark.asyncio
async def test_routerssh_tofu_auto_pins_and_sets_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_blob = _make_key_blob()

    def _load_config():
        return _Cfg(data_dir=tmp_path, ssh_trust_mode="tofu_auto")

    def _get_creds():
        return ("admin", "secret")

    async def _fake_connect(hostname, **kwargs):
        return _FakeConn(key_blob)

    monkeypatch.setattr("asusroutercontrol.ssh.load_config", _load_config)
    monkeypatch.setattr("asusroutercontrol.ssh.get_router_credentials", _get_creds)
    monkeypatch.setattr("asusroutercontrol.ssh.asyncssh.connect", _fake_connect)

    ssh = RouterSSH(hostname="router.local", port=22)
    await ssh.connect()
    await ssh.disconnect()

    known_hosts = tmp_path / "known_hosts"
    assert known_hosts.exists()
    assert known_hosts.read_text(encoding="utf-8").strip() == f"router.local {key_blob}"
    assert stat.S_IMODE(known_hosts.stat().st_mode) == 0o600
