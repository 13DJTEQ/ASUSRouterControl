"""FreshTomato firmware backend (read-only MVP via SSH)."""

from __future__ import annotations

import logging
import re

import asyncssh

from asusroutercontrol._time import utcnow
from asusroutercontrol.backends.base import (
    BackendOperationUnsupported,
    FirmwareBackend,
)
from asusroutercontrol.models import (
    ConnectionType,
    Device,
    LanClient,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)

log = logging.getLogger(__name__)


class FreshTomatoBackend(FirmwareBackend):
    """FreshTomato backend using SSH command adapters."""

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
        self._conn: asyncssh.SSHClientConnection | None = None

    def supported_operations(self) -> set[str]:
        return {
            "read.devices",
            "read.traffic",
            "read.system",
            "read.wan",
            "read.wifi_clients",
            "read.lan_clients",
            "read.port_forwarding",
        }

    async def connect(self) -> None:
        self._conn = await asyncssh.connect(
            host=self._hostname,
            port=self._ssh_port,
            username=self._username,
            password=self._password,
            known_hosts=None,
        )

    async def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def _run(self, command: str) -> str:
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        result = await self._conn.run(command, check=False)
        return (result.stdout or "").strip()

    async def get_connected_devices(self) -> list[Device]:
        output = await self._run("cat /proc/net/arp")
        return self._parse_arp_table(output)

    async def get_traffic_stats(self) -> TrafficSnapshot:
        output = await self._run("cat /proc/net/dev")
        rx, tx = self._parse_net_dev(output)
        return TrafficSnapshot(
            timestamp=utcnow(),
            rx_bytes=rx,
            tx_bytes=tx,
        )

    async def get_system_info(self) -> SystemInfo:
        uptime = await self._run("cat /proc/uptime")
        mem = await self._run("cat /proc/meminfo")
        load = await self._run("cat /proc/loadavg")

        info = SystemInfo()
        if uptime:
            try:
                info.uptime_seconds = int(float(uptime.split()[0]))
            except (IndexError, ValueError):
                pass

        mem_total = self._match_int(mem, r"MemTotal:\s+(\d+)\s+kB")
        mem_free = self._match_int(mem, r"MemAvailable:\s+(\d+)\s+kB")
        if mem_total:
            info.ram_total_mb = mem_total / 1024
        if mem_total and mem_free is not None:
            used = max(0, mem_total - mem_free)
            info.ram_used_mb = used / 1024
            info.ram_usage_percent = round((used / mem_total) * 100, 1)

        if load:
            try:
                load_1m = float(load.split()[0])
                info.cpu_usage_percent = round(min(100.0, load_1m * 100.0), 1)
            except (IndexError, ValueError):
                pass
        return info

    async def get_wan_status(self) -> WANStatus:
        ip = await self._run("nvram get wan_ipaddr")
        gw = await self._run("nvram get wan_gateway")
        dns = await self._run("nvram get wan_dns")
        status = "connected" if ip and ip != "0.0.0.0" else "disconnected"
        dns_list = [part for part in dns.split() if part]
        return WANStatus(
            status=status,
            ip_address=ip or None,
            gateway=gw or None,
            dns=dns_list,
        )

    async def get_wifi_clients(self) -> list[WiFiClient]:
        output = await self._run("wl assoclist 2>/dev/null")
        clients: list[WiFiClient] = []
        for line in output.splitlines():
            m = re.search(r"([0-9A-Fa-f:]{17})", line)
            if not m:
                continue
            clients.append(
                WiFiClient(
                    mac=m.group(1).lower(),
                    band=None,
                )
            )
        return clients

    async def get_lan_clients(self) -> list[LanClient]:
        devices = await self.get_connected_devices()
        wifi_macs = {c.mac for c in await self.get_wifi_clients()}
        return [
            LanClient(mac=d.mac, ip=d.ip, hostname=d.hostname)
            for d in devices
            if d.mac not in wifi_macs
        ]

    async def set_state(self, action: str, **kwargs) -> bool:
        raise BackendOperationUnsupported(
            self.backend_name(),
            "write.state",
            "FreshTomato MVP is read-only; set_state is not implemented.",
        )

    async def get_port_forwarding(self) -> list[PortRule]:
        raw = await self._run("nvram get vts_rulelist")
        rules: list[PortRule] = []
        if not raw:
            return rules
        for chunk in raw.split("<"):
            if not chunk:
                continue
            parts = chunk.split(">")
            if len(parts) < 6:
                continue
            name, src_port, dst_ip, dst_port, protocol, _src_ip = parts[:6]
            rules.append(
                PortRule(
                    name=name,
                    protocol=protocol.lower() if protocol else "tcp",
                    src_port=src_port,
                    dst_port=dst_port,
                    dst_ip=dst_ip,
                    enabled=True,
                )
            )
        return rules

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        raise BackendOperationUnsupported(
            self.backend_name(),
            "write.port_forwarding",
            "FreshTomato MVP is read-only; port-forwarding writes are deferred.",
        )

    @staticmethod
    def _match_int(text: str, pattern: str) -> int | None:
        m = re.search(pattern, text)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _parse_arp_table(output: str) -> list[Device]:
        devices: list[Device] = []
        for line in output.splitlines()[1:]:
            cols = line.split()
            if len(cols) < 6:
                continue
            ip = cols[0]
            mac = cols[3].lower()
            if mac == "00:00:00:00:00:00":
                continue
            iface = cols[5]
            devices.append(
                Device(
                    mac=mac,
                    ip=ip,
                    hostname=None,
                    connection=ConnectionType.WIRED,
                    band=iface,
                    is_online=True,
                    last_seen=utcnow(),
                )
            )
        return devices

    @staticmethod
    def _parse_net_dev(output: str) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        for line in output.splitlines():
            if ":" not in line:
                continue
            iface, stats = [p.strip() for p in line.split(":", 1)]
            if iface in {"lo"}:
                continue
            cols = stats.split()
            if len(cols) < 16:
                continue
            try:
                rx_total += int(cols[0])
                tx_total += int(cols[8])
            except ValueError:
                continue
        return rx_total, tx_total
