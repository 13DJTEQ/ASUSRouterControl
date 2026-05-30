"""FreshTomato firmware backend — partial implementation (read-only SSH paths).

Write operations raise :class:`BackendOperationUnsupported`.  Callers should
check ``backend.capabilities`` before attempting writes.
"""

from __future__ import annotations

from asusroutercontrol.backends.base import BackendOperationUnsupported, FirmwareBackend
from asusroutercontrol.models import (
    ConnectionType,
    Device,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)


class FreshTomatoBackend(FirmwareBackend):
    """Backend for FreshTomato firmware.

    Read-only operations are forwarded to SSH probes.
    Write operations raise :class:`BackendOperationUnsupported`.
    """
    def __init__(
        self,
        hostname: str,
        username: str,
        password: str,
        *,
        ssh_port: int = 22,
    ) -> None:
        self._hostname = hostname
        self._username = username
        self._password = password
        self._ssh_port = ssh_port

    @staticmethod
    def _parse_arp_table(raw: str) -> list[Device]:
        """Parse `arp -an` style output into lightweight device rows."""
        rows: list[Device] = []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            ip = parts[0].strip()
            mac = parts[3].strip().lower()
            iface = parts[5].strip().lower()
            if len(mac.split(":")) != 6:
                continue
            connection = (
                ConnectionType.WIRED
                if iface.startswith(("eth", "vlan", "br"))
                else ConnectionType.UNKNOWN
            )
            rows.append(Device(mac=mac, ip=ip, connection=connection, is_online=True))
        return rows

    @staticmethod
    def _parse_net_dev(raw: str) -> tuple[int, int]:
        """Parse `/proc/net/dev` and sum RX/TX bytes across non-loopback interfaces."""
        total_rx = 0
        total_tx = 0
        for line in raw.splitlines():
            if ":" not in line:
                continue
            iface, payload = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            fields = payload.split()
            if len(fields) < 9:
                continue
            try:
                total_rx += int(fields[0])
                total_tx += int(fields[8])
            except ValueError:
                continue
        return total_rx, total_tx

    async def connect(self) -> None:
        # SSH connection is established per-command in RouterSSH; nothing to do here.
        pass

    async def disconnect(self) -> None:
        pass

    async def get_connected_devices(self) -> list[Device]:
        raise NotImplementedError("FreshTomato: get_connected_devices not yet implemented")

    async def get_traffic_stats(self) -> TrafficSnapshot:
        raise NotImplementedError("FreshTomato: get_traffic_stats not yet implemented")

    async def get_system_info(self) -> SystemInfo:
        raise NotImplementedError("FreshTomato: get_system_info not yet implemented")

    async def get_wan_status(self) -> WANStatus:
        raise NotImplementedError("FreshTomato: get_wan_status not yet implemented")

    async def get_wifi_clients(self) -> list[WiFiClient]:
        raise NotImplementedError("FreshTomato: get_wifi_clients not yet implemented")

    async def set_state(self, action: str, **kwargs) -> bool:
        raise BackendOperationUnsupported(
            f"FreshTomato backend does not support write action {action!r}"
        )

    async def get_port_forwarding(self) -> list[PortRule]:
        raise NotImplementedError("FreshTomato: get_port_forwarding not yet implemented")

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        raise BackendOperationUnsupported(
            "FreshTomato backend does not support set_port_forwarding"
        )
