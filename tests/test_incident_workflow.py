from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from asusroutercontrol._cli import cli
from asusroutercontrol.incident import (
    IncidentSnapshot,
    PathSnapshot,
    RouterPathSnapshot,
    build_repair_stage_commands,
    classify_snapshot,
)


def _healthy_path(path_key: str, service: str, device: str, ip: str) -> PathSnapshot:
    return PathSnapshot(
        path_key=path_key,
        service=service,
        device=device,
        service_enabled=True,
        status_active=True,
        ip=ip,
        link_local_ip=False,
        source_ping_ok=True,
        dhcp_lease_ip=ip,
    )


def _degraded_link_local(path_key: str, service: str, device: str) -> PathSnapshot:
    return PathSnapshot(
        path_key=path_key,
        service=service,
        device=device,
        service_enabled=True,
        status_active=True,
        ip="169.254.10.20",
        link_local_ip=True,
        source_ping_ok=None,
        dhcp_lease_ip=None,
    )


def test_classify_path_isolated_when_single_path_is_link_local() -> None:
    snapshot = IncidentSnapshot(
        timestamp=0.0,
        paths={
            "ethernet-primary": _healthy_path(
                "ethernet-primary", "Ethernet 2", "en1", "192.168.1.242"
            ),
            "wifi-secondary": _healthy_path(
                "wifi-secondary", "Wi-Fi", "en2", "192.168.1.240"
            ),
            "ethernet-secondary": _degraded_link_local(
                "ethernet-secondary", "Ethernet 1", "en0"
            ),
        },
        default_interface="en1",
        default_gateway="192.168.1.1",
        gateway_ping_ok=True,
        router_paths={
            "ethernet-secondary": RouterPathSnapshot(
                path_key="ethernet-secondary",
                mac="00:3e:e1:c9:2c:0c",
                ip=None,
                online=False,
                connection="unknown",
            )
        },
    )
    classification = classify_snapshot(snapshot)
    assert classification.category == "path-isolated"
    assert "ethernet-secondary" in classification.degraded_paths
    assert any("link-local IP" in reason for reason in classification.reasons)


def test_classify_reporting_only_when_paths_healthy_but_ui_conflicts() -> None:
    snapshot = IncidentSnapshot(
        timestamp=0.0,
        paths={
            "ethernet-primary": _healthy_path(
                "ethernet-primary", "Ethernet 2", "en1", "192.168.1.242"
            ),
            "wifi-secondary": _healthy_path(
                "wifi-secondary", "Wi-Fi", "en2", "192.168.1.240"
            ),
            "ethernet-secondary": _healthy_path(
                "ethernet-secondary", "Ethernet 1", "en0", "192.168.1.243"
            ),
        },
        default_interface="en1",
        default_gateway="192.168.1.1",
        gateway_ping_ok=True,
        ui_conflicts=["Wi-Fi reports disabled while packets succeed."],
    )
    classification = classify_snapshot(snapshot)
    assert classification.category == "reporting-only"
    assert classification.degraded_paths == []
    assert classification.has_reporting_conflict


def test_build_repair_commands_are_single_path_for_ethernet_stage_a() -> None:
    commands = build_repair_stage_commands(stage="A", path_key="ethernet-primary")
    rendered = [" ".join(cmd.argv) for cmd in commands]
    assert any("ifconfig en1 down" in cmd for cmd in rendered)
    assert all("en2" not in cmd for cmd in rendered)
    assert all("setairportpower" not in cmd for cmd in rendered)


def test_build_stage_c_global_reset_is_opt_in() -> None:
    default_cmds = build_repair_stage_commands(stage="C", path_key="wifi-secondary")
    forced_cmds = build_repair_stage_commands(
        stage="C",
        path_key="wifi-secondary",
        allow_global_reset=True,
    )
    default_rendered = [" ".join(cmd.argv) for cmd in default_cmds]
    forced_rendered = [" ".join(cmd.argv) for cmd in forced_cmds]
    assert not any(
        "launchctl kickstart -k system/com.apple.configd" in cmd
        for cmd in default_rendered
    )
    assert any(
        "launchctl kickstart -k system/com.apple.configd" in cmd
        for cmd in forced_rendered
    )


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


def test_optimize_apply_clear_sets_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    async def _fake_apply_nvram_setting(
        ssh,
        store,
        key,
        value,
        *,
        dry_run=False,
        triggered_by="user",
    ):
        calls.append(
            {
                "key": key,
                "value": value,
                "dry_run": dry_run,
                "triggered_by": triggered_by,
            }
        )
        return SimpleNamespace(
            key=key,
            old_value="1",
            new_value=value,
            success=True,
            error="",
            service_restarted="",
        )

    monkeypatch.setattr("asusroutercontrol._cli.DataStore", _FakeDataStoreCtx)
    monkeypatch.setattr("asusroutercontrol.ssh.RouterSSH", _FakeRouterSSHCtx)
    monkeypatch.setattr(
        "asusroutercontrol.executor.apply_nvram_setting",
        _fake_apply_nvram_setting,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["optimize", "apply", "--key", "wan_dns1_x", "--clear", "--dry-run"],
    )
    assert result.exit_code == 0
    assert calls
    assert calls[0]["value"] == ""


def test_optimize_apply_rejects_value_and_clear_together() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["optimize", "apply", "--key", "wan_dns1_x", "--value", "1.1.1.1", "--clear"],
    )
    assert result.exit_code != 0
    assert "Use either --value or --clear" in result.output
