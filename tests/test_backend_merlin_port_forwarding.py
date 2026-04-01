from __future__ import annotations

import pytest
from asusrouter.modules.port_forwarding import PortForwardingRule as AsusPortForwardingRule

from asusroutercontrol.backends.merlin import MerlinBackend
from asusroutercontrol.models import ConnectionType, PortRule


class _FakeRouter:
    def __init__(self, current_rules: list[AsusPortForwardingRule], *, apply_result: bool = True):
        self.current_rules = current_rules
        self.apply_result = apply_result
        self.applied: list[AsusPortForwardingRule] | None = None
        self.get_calls = 0

    async def async_get_data(self, _datatype):
        self.get_calls += 1
        if self.applied is not None:
            return {"rules": self.applied}
        return {"rules": self.current_rules}

    async def async_apply_port_forwarding_rules(
        self, rules: list[AsusPortForwardingRule]
    ) -> bool:
        self.applied = rules
        return self.apply_result


@pytest.mark.asyncio
async def test_set_port_forwarding_noop_when_already_equal() -> None:
    current = [
        AsusPortForwardingRule(
            name="A",
            ip_address="192.168.1.10",
            port="8080",
            protocol="tcp",
            ip_external="",
            port_external="80",
        )
    ]
    backend = MerlinBackend(hostname="h", username="u", password="p")
    fake = _FakeRouter(current_rules=current)
    backend._router = fake  # type: ignore[attr-defined]

    ok = await backend.set_port_forwarding(
        [PortRule(name="A", dst_ip="192.168.1.10", dst_port="8080", src_port="80", protocol="tcp")]
    )
    assert ok is True
    assert fake.applied is None


@pytest.mark.asyncio
async def test_set_port_forwarding_applies_and_verifies() -> None:
    backend = MerlinBackend(hostname="h", username="u", password="p")
    fake = _FakeRouter(current_rules=[])
    backend._router = fake  # type: ignore[attr-defined]

    ok = await backend.set_port_forwarding(
        [
            PortRule(
                name="Game",
                dst_ip="192.168.1.20",
                dst_port="3074",
                src_port="3074",
                protocol="udp",
            )
        ]
    )
    assert ok is True
    assert fake.applied is not None
    assert fake.applied[0].ip_address == "192.168.1.20"
    assert fake.applied[0].port == "3074"
    assert fake.applied[0].protocol == "udp"


class _ConnKind:
    def __init__(self, name: str):
        self.name = name


class _ModernConn:
    def __init__(self, name: str):
        self.connection_type = _ConnKind(name)


class _LegacyConn:
    def __init__(self, name: str):
        self.type = _ConnKind(name)

class _State:
    def __init__(self, name: str):
        self.name = name


def test_parse_connection_obj_modern_wired() -> None:
    conn = _ModernConn("WIRED")
    assert MerlinBackend._parse_connection_obj(conn) == ConnectionType.WIRED


def test_parse_connection_obj_modern_wlan_5g() -> None:
    conn = _ModernConn("WLAN_5G")
    assert MerlinBackend._parse_connection_obj(conn) == ConnectionType.WIFI_5G


def test_parse_connection_obj_modern_disconnected() -> None:
    conn = _ModernConn("DISCONNECTED")
    assert MerlinBackend._parse_connection_obj(conn) == ConnectionType.UNKNOWN


def test_parse_connection_obj_legacy_type_fallback() -> None:
    conn = _LegacyConn("WLAN_2G")
    assert MerlinBackend._parse_connection_obj(conn) == ConnectionType.WIFI_2G


def test_parse_online_state_connected() -> None:
    conn = _ModernConn("WIRED")
    assert MerlinBackend._parse_online_state(_State("CONNECTED"), conn) is True


def test_parse_online_state_disconnected() -> None:
    conn = _ModernConn("DISCONNECTED")
    assert MerlinBackend._parse_online_state(_State("DISCONNECTED"), conn) is False


def test_parse_online_state_falls_back_to_conn_online() -> None:
    conn = _ModernConn("WIRED")
    conn.online = True
    assert MerlinBackend._parse_online_state(None, conn) is True
