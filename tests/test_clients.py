"""Tests for WiFi and LAN client functionality."""

from __future__ import annotations

import pytest

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.models import ConnectionType, Device, LanClient, WiFiClient


class FakeBackend(FirmwareBackend):
    """Minimal fake backend for testing client methods."""

    def __init__(self, devices: list[Device] | None = None):
        self._devices = devices or []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def get_connected_devices(self) -> list[Device]:
        return self._devices

    async def get_traffic_stats(self):
        from asusroutercontrol.models import TrafficSnapshot

        return TrafficSnapshot()

    async def get_system_info(self):
        from asusroutercontrol.models import SystemInfo

        return SystemInfo()

    async def get_wan_status(self):
        from asusroutercontrol.models import WANStatus

        return WANStatus()

    async def get_wifi_clients(self) -> list[WiFiClient]:
        return [
            WiFiClient(
                mac=d.mac,
                ip=d.ip,
                hostname=d.hostname,
                band=d.band,
                rssi=d.rssi,
            )
            for d in self._devices
            if d.connection
            in (ConnectionType.WIFI_2G, ConnectionType.WIFI_5G, ConnectionType.WIFI_6G)
        ]

    async def get_lan_clients(self) -> list[LanClient]:
        return [
            LanClient(mac=d.mac, ip=d.ip, hostname=d.hostname)
            for d in self._devices
            if d.connection == ConnectionType.WIRED
        ]

    async def set_state(self, action: str, **kwargs) -> bool:
        return False

    async def get_port_forwarding(self):
        return []

    async def set_port_forwarding(self, rules):
        return False


@pytest.fixture
def mixed_devices() -> list[Device]:
    """Fixture with mixed wired and wireless devices."""
    return [
        Device(
            mac="aa:bb:cc:dd:ee:01",
            ip="192.168.1.10",
            hostname="wired-desktop",
            connection=ConnectionType.WIRED,
        ),
        Device(
            mac="aa:bb:cc:dd:ee:02",
            ip="192.168.1.11",
            hostname="wired-nas",
            connection=ConnectionType.WIRED,
        ),
        Device(
            mac="aa:bb:cc:dd:ee:03",
            ip="192.168.1.20",
            hostname="phone-wifi",
            connection=ConnectionType.WIFI_5G,
            band="5GHz",
            rssi=-55,
        ),
        Device(
            mac="aa:bb:cc:dd:ee:04",
            ip="192.168.1.21",
            hostname="laptop-wifi",
            connection=ConnectionType.WIFI_2G,
            band="2.4GHz",
            rssi=-62,
        ),
        Device(
            mac="aa:bb:cc:dd:ee:05",
            ip="192.168.1.22",
            hostname="tablet-wifi6",
            connection=ConnectionType.WIFI_6G,
            band="6GHz",
            rssi=-48,
        ),
    ]


async def test_get_lan_clients_returns_only_wired(mixed_devices: list[Device]):
    """get_lan_clients returns only WIRED devices."""
    backend = FakeBackend(mixed_devices)
    lan = await backend.get_lan_clients()

    assert len(lan) == 2
    macs = {c.mac for c in lan}
    assert macs == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}


async def test_get_wifi_clients_returns_only_wireless(mixed_devices: list[Device]):
    """get_wifi_clients returns only WiFi devices."""
    backend = FakeBackend(mixed_devices)
    wifi = await backend.get_wifi_clients()

    assert len(wifi) == 3
    macs = {c.mac for c in wifi}
    assert macs == {"aa:bb:cc:dd:ee:03", "aa:bb:cc:dd:ee:04", "aa:bb:cc:dd:ee:05"}


async def test_lan_client_model_fields():
    """LanClient model has expected fields."""
    client = LanClient(mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.100", hostname="test-host")

    assert client.mac == "aa:bb:cc:dd:ee:ff"
    assert client.ip == "192.168.1.100"
    assert client.hostname == "test-host"


async def test_lan_client_optional_fields():
    """LanClient allows optional ip and hostname."""
    client = LanClient(mac="aa:bb:cc:dd:ee:ff")

    assert client.mac == "aa:bb:cc:dd:ee:ff"
    assert client.ip is None
    assert client.hostname is None


async def test_empty_devices_returns_empty_lists():
    """Empty device list returns empty client lists."""
    backend = FakeBackend([])

    lan = await backend.get_lan_clients()
    wifi = await backend.get_wifi_clients()

    assert lan == []
    assert wifi == []


async def test_backend_supported_operations_includes_lan_clients():
    """FirmwareBackend.supported_operations includes read.lan_clients."""
    backend = FakeBackend()
    ops = backend.supported_operations()

    assert "read.lan_clients" in ops
    assert "read.wifi_clients" in ops


async def test_wifi_and_lan_clients_are_disjoint(mixed_devices: list[Device]):
    """WiFi and LAN client sets have no overlap."""
    backend = FakeBackend(mixed_devices)

    lan = await backend.get_lan_clients()
    wifi = await backend.get_wifi_clients()

    lan_macs = {c.mac for c in lan}
    wifi_macs = {c.mac for c in wifi}

    assert lan_macs.isdisjoint(wifi_macs)
    assert len(lan_macs) + len(wifi_macs) == len(mixed_devices)
