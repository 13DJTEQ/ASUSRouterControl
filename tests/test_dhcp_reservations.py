from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from asusroutercontrol._cli import cli
from asusroutercontrol.dhcp_reservations import (
    parse_reservations,
    remove_reservation,
    upsert_reservation,
)


class _Result:
    def __init__(
        self,
        ok: bool = True,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _FakeSSH:
    def __init__(self, state: dict[str, str]) -> None:
        self.state = state

    async def run(self, command: str) -> _Result:
        if command.startswith("nvram get "):
            key = command.split()[2]
            return _Result(ok=True, stdout=self.state.get(key, ""))
        if command.startswith("nvram set "):
            kv = command[len("nvram set ") :]
            key, value = kv.split("=", 1)
            value = value.strip()
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            self.state[key] = value
            return _Result(ok=True, stdout="")
        if command == "nvram commit":
            return _Result(ok=True, stdout="")
        if command == "service restart_dnsmasq":
            return _Result(ok=True, stdout="")
        return _Result(ok=False, stderr=f"unexpected command: {command}", exit_code=1)


class _FakeStore:
    def __init__(self) -> None:
        self.events = []

    async def insert_config_event(self, event, *, commit: bool = True) -> None:
        self.events.append(event)


def _base_state() -> dict[str, str]:
    return {
        "dhcp_static_x": "0",
        "dhcp_staticlist": "",
        "dhcp_hostnames": "",
        "lan_ipaddr": "192.168.1.1",
        "lan_netmask": "255.255.255.0",
        "dhcp_start": "192.168.1.2",
        "dhcp_end": "192.168.1.254",
    }


def test_parse_reservations_round_trip_with_hostnames() -> None:
    reservations = parse_reservations(
        "<74:1B:B2:F1:C4:31>192.168.1.240<AA:BB:CC:DD:EE:FF>192.168.1.10",
        "<74:1B:B2:F1:C4:31>MacPro12Core<AA:BB:CC:DD:EE:FF>Printer",
    )
    assert reservations[0].mac == "74:1b:b2:f1:c4:31"
    assert reservations[0].ip == "192.168.1.240"
    assert reservations[0].hostname == "MacPro12Core"
    assert reservations[1].mac == "aa:bb:cc:dd:ee:ff"
    assert reservations[1].ip == "192.168.1.10"
    assert reservations[1].hostname == "Printer"


@pytest.mark.asyncio
async def test_upsert_reservation_replaces_existing_mac_and_sets_hostname() -> None:
    state = _base_state()
    state["dhcp_static_x"] = "1"
    state["dhcp_staticlist"] = "<74:1B:B2:F1:C4:31>192.168.1.28"
    state["dhcp_hostnames"] = "<74:1B:B2:F1:C4:31>MacPro12Core"

    ssh = _FakeSSH(state)
    store = _FakeStore()
    result = await upsert_reservation(
        ssh=ssh,
        store=store,
        mac="74:1B:B2:F1:C4:31",
        ip="192.168.1.240",
        hostname="MacPro12Core",
        dry_run=True,
    )

    assert result.success
    assert result.dry_run
    assert result.changed
    assert result.new_values["dhcp_static_x"] == "1"
    assert result.new_values["dhcp_staticlist"] == "<74:1b:b2:f1:c4:31>192.168.1.240"
    assert result.new_values["dhcp_hostnames"] == "<74:1b:b2:f1:c4:31>MacPro12Core"


@pytest.mark.asyncio
async def test_upsert_reservation_rejects_duplicate_ip_for_different_mac() -> None:
    state = _base_state()
    state["dhcp_static_x"] = "1"
    state["dhcp_staticlist"] = "<AA:BB:CC:DD:EE:FF>192.168.1.240"
    state["dhcp_hostnames"] = "<AA:BB:CC:DD:EE:FF>Existing"

    ssh = _FakeSSH(state)
    store = _FakeStore()
    with pytest.raises(ValueError, match="already reserved"):
        await upsert_reservation(
            ssh=ssh,
            store=store,
            mac="74:1B:B2:F1:C4:31",
            ip="192.168.1.240",
            hostname="MacPro12Core",
            dry_run=True,
        )


@pytest.mark.asyncio
async def test_remove_reservation_disables_static_when_last_entry_removed() -> None:
    state = _base_state()
    state["dhcp_static_x"] = "1"
    state["dhcp_staticlist"] = "<74:1B:B2:F1:C4:31>192.168.1.240"
    state["dhcp_hostnames"] = "<74:1B:B2:F1:C4:31>MacPro12Core"

    ssh = _FakeSSH(state)
    store = _FakeStore()
    result = await remove_reservation(
        ssh=ssh,
        store=store,
        mac="74:1B:B2:F1:C4:31",
        dry_run=True,
    )

    assert result.success
    assert result.changed
    assert result.new_values["dhcp_static_x"] == "0"
    assert result.new_values["dhcp_staticlist"] == ""
    assert result.new_values["dhcp_hostnames"] == ""


def test_cli_dhcp_set_invalid_mac_fails_fast() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dhcp", "set", "--mac", "invalid-mac", "--ip", "192.168.1.240"],
    )
    assert result.exit_code != 0
    assert "MAC must look like AA:BB:CC:DD:EE:FF" in result.output


def test_cli_dhcp_set_apply_requires_confirmation() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dhcp",
            "set",
            "--mac",
            "74:1B:B2:F1:C4:31",
            "--ip",
            "192.168.1.240",
            "--apply",
        ],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Cancelled." in result.output


class _FakeRouterSSHCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeDataStoreCtx:
    def __init__(self, *_args, **_kwargs):
        pass

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _fake_result(*, success: bool = True, message: str = ""):
    return SimpleNamespace(success=success, message=message)


def _patch_profile_command_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the old _cli module (legacy code still runs from there)
    monkeypatch.setattr("asusroutercontrol._cli._render_dhcp_apply_result", lambda _r: None)
    monkeypatch.setattr(
        "asusroutercontrol._cli._print_profile_device_match_summary",
        lambda *_a: None,
    )
    monkeypatch.setattr("asusroutercontrol._cli.DataStore", _FakeDataStoreCtx)
    monkeypatch.setattr("asusroutercontrol.ssh.RouterSSH", _FakeRouterSSHCtx)


def test_cli_reserve_denon_second_port_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def _fake_upsert_reservation(**kwargs):
        calls.append(kwargs)
        return _fake_result()

    _patch_profile_command_deps(monkeypatch)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.upsert_reservation",
        _fake_upsert_reservation,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["dhcp", "reserve-denon-second-port"])
    assert result.exit_code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["mac"] == "00:05:cd:d4:a5:3c"
    assert call["ip"] == "192.168.1.241"
    assert call["hostname"] == "Denon150"
    assert call["dry_run"] is True
    assert call["triggered_by"] == "dhcp:reserve-denon-second-port"


def test_cli_reserve_denon_second_port_override_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def _fake_upsert_reservation(**kwargs):
        calls.append(kwargs)
        return _fake_result()

    _patch_profile_command_deps(monkeypatch)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.upsert_reservation",
        _fake_upsert_reservation,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dhcp",
            "reserve-denon-second-port",
            "--mac",
            "AA:BB:CC:DD:EE:FF",
            "--ip",
            "192.168.1.242",
            "--hostname",
            "DenonZone2",
        ],
    )
    assert result.exit_code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["mac"] == "aa:bb:cc:dd:ee:ff"
    assert call["ip"] == "192.168.1.242"
    assert call["hostname"] == "DenonZone2"


def test_cli_reserve_denon_second_port_apply_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def _fake_upsert_reservation(**kwargs):
        calls.append(kwargs)
        return _fake_result()

    _patch_profile_command_deps(monkeypatch)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.upsert_reservation",
        _fake_upsert_reservation,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dhcp", "reserve-denon-second-port", "--apply"],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Cancelled." in result.output
    assert calls == []


def test_cli_unreserve_denon_second_port_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def _fake_remove_reservation(**kwargs):
        calls.append(kwargs)
        return _fake_result()

    _patch_profile_command_deps(monkeypatch)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.remove_reservation",
        _fake_remove_reservation,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["dhcp", "unreserve-denon-second-port"])
    assert result.exit_code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["mac"] == "00:05:cd:d4:a5:3c"
    assert call["dry_run"] is True
    assert call["triggered_by"] == "dhcp:unreserve-denon-second-port"


def test_cli_dhcp_health_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_reservations(_ssh):
        return [
            SimpleNamespace(mac="74:1b:b2:f1:c4:31", ip="192.168.1.240"),
            SimpleNamespace(mac="00:05:cd:d4:a5:3c", ip="192.168.1.241"),
            SimpleNamespace(mac="00:3e:e1:c9:2c:0b", ip="192.168.1.242"),
            SimpleNamespace(mac="00:3e:e1:c9:2c:0c", ip="192.168.1.243"),
        ]

    monkeypatch.setattr("asusroutercontrol.ssh.RouterSSH", _FakeRouterSSHCtx)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.get_reservations",
        _fake_get_reservations,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["dhcp", "health"])
    assert result.exit_code == 0
    assert "All required DHCP reservation mappings are healthy." in result.output


def test_cli_dhcp_health_fail_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_reservations(_ssh):
        return [
            SimpleNamespace(mac="74:1b:b2:f1:c4:31", ip="192.168.1.240"),
            SimpleNamespace(mac="00:05:cd:d4:a5:3c", ip="192.168.1.241"),
            SimpleNamespace(mac="00:3e:e1:c9:2c:0b", ip="192.168.1.111"),
            SimpleNamespace(mac="00:3e:e1:c9:2c:0c", ip="192.168.1.243"),
        ]

    monkeypatch.setattr("asusroutercontrol.ssh.RouterSSH", _FakeRouterSSHCtx)
    monkeypatch.setattr(
        "asusroutercontrol.dhcp_reservations.get_reservations",
        _fake_get_reservations,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["dhcp", "health"])
    assert result.exit_code != 0
    assert "Reservation health check failed" in result.output
    assert "MacPro Ethernet 2 expected 192.168.1.242" in result.output
