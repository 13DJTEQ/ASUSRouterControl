"""AsusWRT / Merlin firmware backend using the asusrouter library."""

from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from asusrouter import AsusData, AsusRouter

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.models import (
    ConnectionType,
    Device,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)

log = logging.getLogger(__name__)


class MerlinBackend(FirmwareBackend):
    """Backend for stock AsusWRT and AsusWRT-Merlin firmware.

    Uses the `asusrouter` library (HTTP API wrapper).
    Works identically on stock and Merlin — same HTTP endpoints.
    """

    def __init__(
        self,
        hostname: str,
        username: str,
        password: str,
        *,
        use_ssl: bool = False,
        port: int | None = None,
    ) -> None:
        self._hostname = hostname
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._port = port
        self._session: aiohttp.ClientSession | None = None
        self._router: AsusRouter | None = None

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._router = AsusRouter(
            hostname=self._hostname,
            username=self._username,
            password=self._password,
            use_ssl=self._use_ssl,
            port=self._port,
            session=self._session,
        )
        await self._router.async_connect()
        log.info("Connected to router at %s", self._hostname)

    async def disconnect(self) -> None:
        if self._router:
            await self._router.async_disconnect()
        if self._session:
            await self._session.close()
        log.info("Disconnected from router")

    def _ensure_connected(self) -> AsusRouter:
        if not self._router:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._router

    # --- Data retrieval ---

    async def get_connected_devices(self) -> list[Device]:
        router = self._ensure_connected()
        now = datetime.utcnow()
        try:
            data = await router.async_get_data(AsusData.CLIENTS)
        except Exception:
            log.exception("Failed to get connected devices")
            return []

        devices: list[Device] = []
        if not data or not isinstance(data, dict):
            return devices

        # asusrouter returns dict[MAC, AsusClient] with rich objects
        for mac, client in data.items():
            try:
                desc = getattr(client, "description", None)
                conn_obj = getattr(client, "connection", None)
                state = getattr(client, "state", None)

                hostname = getattr(desc, "name", None) if desc else None
                # Library uses MAC as name when hostname is unknown
                if hostname and hostname.upper() == str(mac).upper():
                    hostname = None

                conn_type = self._parse_connection_obj(conn_obj)
                ip_addr = getattr(conn_obj, "ip_address", None)
                rssi = getattr(conn_obj, "rssi", None)
                tx_rate = getattr(conn_obj, "tx_rate", None)
                rx_rate = getattr(conn_obj, "rx_rate", None)

                # Determine online from state enum
                is_online = "CONNECTED" in str(state) if state else True

                devices.append(
                    Device(
                        mac=str(mac),
                        ip=str(ip_addr) if ip_addr else None,
                        hostname=hostname,
                        connection=conn_type,
                        band=str(conn_type.value) if conn_type != ConnectionType.WIRED else None,
                        rssi=int(rssi) if rssi is not None else None,
                        tx_rate_mbps=float(tx_rate) if tx_rate is not None else None,
                        rx_rate_mbps=float(rx_rate) if rx_rate is not None else None,
                        is_online=is_online,
                        last_seen=now,
                    )
                )
            except Exception:
                log.debug("Failed to parse client %s", mac)
        return devices

    async def get_traffic_stats(self) -> TrafficSnapshot:
        router = self._ensure_connected()
        try:
            data = await router.async_get_data(AsusData.NETWORK)
        except Exception:
            log.exception("Failed to get traffic stats")
            return TrafficSnapshot()

        if not data or not isinstance(data, dict):
            return TrafficSnapshot()

        # Network data is nested: data["wan"] has rx/tx/rx_speed/tx_speed
        wan_net = data.get("wan", {})
        return TrafficSnapshot(
            timestamp=datetime.utcnow(),
            rx_bytes=wan_net.get("rx", 0),
            tx_bytes=wan_net.get("tx", 0),
            rx_rate_bps=wan_net.get("rx_speed"),
            tx_rate_bps=wan_net.get("tx_speed"),
        )

    async def get_system_info(self) -> SystemInfo:
        router = self._ensure_connected()
        info = SystemInfo()

        try:
            cpu_data = await router.async_get_data(AsusData.CPU)
            if cpu_data and isinstance(cpu_data, dict):
                total_cpu = cpu_data.get("total", {})
                cpu_total = total_cpu.get("total", 0)
                cpu_used = total_cpu.get("used", 0)
                if cpu_total:
                    info.cpu_usage_percent = round((cpu_used / cpu_total) * 100, 1)
        except Exception:
            log.debug("CPU data unavailable")

        try:
            ram_data = await router.async_get_data(AsusData.RAM)
            if ram_data and isinstance(ram_data, dict):
                total = ram_data.get("total", 0)
                used = ram_data.get("used", 0)
                info.ram_total_mb = total / 1024 if total else None
                info.ram_used_mb = used / 1024 if used else None
                info.ram_usage_percent = ram_data.get("usage")
        except Exception:
            log.debug("RAM data unavailable")

        try:
            fw_data = await router.async_get_data(AsusData.FIRMWARE)
            if fw_data and isinstance(fw_data, dict):
                raw_fw = fw_data.get("current")
                info.firmware_version = str(raw_fw) if raw_fw is not None else None
        except Exception:
            log.debug("Firmware data unavailable")

        # Uptime + model from SYSINFO
        try:
            sys_data = await router.async_get_data(AsusData.SYSINFO)
            if sys_data and isinstance(sys_data, dict):
                sys_info = sys_data.get("sys", {})
                uptime_str = sys_info.get("uptimeStr", "")
                # Parse "...(<N> secs since boot)" pattern
                import re
                m = re.search(r"\((\d+)\s+secs? since boot\)", uptime_str)
                if m:
                    info.uptime_seconds = int(m.group(1))
        except Exception:
            log.debug("SYSINFO data unavailable")

        # Temperature
        try:
            temp_data = await router.async_get_data(AsusData.TEMPERATURE)
            if temp_data and isinstance(temp_data, dict):
                # Keys may be enum or string; find CPU temp
                for k, v in temp_data.items():
                    if "cpu" in str(k).lower():
                        info.temperature_c = float(v)
                        break
        except Exception:
            log.debug("Temperature data unavailable")

        return info

    async def get_wan_status(self) -> WANStatus:
        router = self._ensure_connected()
        try:
            data = await router.async_get_data(AsusData.WAN)
        except Exception:
            log.exception("Failed to get WAN status")
            return WANStatus()

        if not data or not isinstance(data, dict):
            return WANStatus()

        # Parse nested WAN structure:
        # data["internet"] has link status + ip_address
        # data["0"] is primary WAN with real_ip, gateway, dns, state
        internet = data.get("internet", {})
        primary = data.get("0", data.get(0, {}))

        # Resolve link status from enum — str() gives value ("2"), .name gives "CONNECTED"
        link = internet.get("link", None)
        link_name = getattr(link, "name", str(link)).upper() if link else ""
        status = "connected" if "CONNECTED" in link_name else "disconnected"

        ip_addr = internet.get("ip_address") or primary.get("real_ip")

        # Gateway and DNS may be in primary or from DEVICEMAP; handle None gracefully
        gateway = primary.get("gateway")
        dns_raw = primary.get("dns")
        if dns_raw is None:
            dns_list = []
        elif isinstance(dns_raw, str):
            dns_list = [d.strip() for d in dns_raw.split(",") if d.strip()]
        elif isinstance(dns_raw, list):
            dns_list = [str(d) for d in dns_raw]
        else:
            dns_list = []

        return WANStatus(
            status=status,
            ip_address=str(ip_addr) if ip_addr else None,
            gateway=str(gateway) if gateway else None,
            dns=dns_list,
        )

    async def get_wifi_clients(self) -> list[WiFiClient]:
        devices = await self.get_connected_devices()
        return [
            WiFiClient(
                mac=d.mac,
                ip=d.ip,
                hostname=d.hostname,
                band=d.band,
                rssi=d.rssi,
                tx_rate_mbps=d.tx_rate_mbps,
                rx_rate_mbps=d.rx_rate_mbps,
            )
            for d in devices
            if d.connection
            in (ConnectionType.WIFI_2G, ConnectionType.WIFI_5G, ConnectionType.WIFI_6G)
        ]

    async def set_state(self, action: str, **kwargs) -> bool:
        router = self._ensure_connected()
        try:
            # Import action enums dynamically based on action string
            from asusrouter.modules.system import AsusSystem

            action_map = {
                "reboot": AsusSystem.REBOOT,
                "restart_httpd": AsusSystem.RESTART_HTTPD,
            }
            sys_action = action_map.get(action)
            if sys_action:
                result = await router.async_set_state(sys_action)
                log.info("Executed system action: %s -> %s", action, result)
                return bool(result)

            log.warning("Unknown action: %s", action)
            return False
        except Exception:
            log.exception("Failed to execute action: %s", action)
            return False

    async def get_port_forwarding(self) -> list[PortRule]:
        router = self._ensure_connected()
        try:
            data = await router.async_get_data(AsusData.PORT_FORWARDING)
        except Exception:
            log.exception("Failed to get port forwarding rules")
            return []

        if not data or not isinstance(data, dict):
            return []

        rules: list[PortRule] = []
        for _key, rule in data.items():
            if isinstance(rule, dict):
                rules.append(
                    PortRule(
                        name=rule.get("name", ""),
                        protocol=rule.get("protocol", "tcp"),
                        src_port=str(rule.get("src_port", "")),
                        dst_port=str(rule.get("dst_port", "")),
                        dst_ip=rule.get("dst_ip", ""),
                        enabled=rule.get("enabled", True),
                    )
                )
        return rules

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        # TODO: Implement via async_set_state with AsusPortForwarding
        log.warning("set_port_forwarding not yet implemented")
        return False

    # --- Helpers ---

    @staticmethod
    def _parse_connection_obj(conn_obj) -> ConnectionType:
        """Map asusrouter connection object to our ConnectionType."""
        if conn_obj is None:
            return ConnectionType.UNKNOWN
        conn_type = getattr(conn_obj, "type", None)
        type_str = getattr(conn_type, "name", str(conn_type)).upper() if conn_type else ""
        if "WIRED" in type_str:
            return ConnectionType.WIRED
        if "5G" in type_str:
            return ConnectionType.WIFI_5G
        if "6G" in type_str:
            return ConnectionType.WIFI_6G
        if "2G" in type_str or "WLAN" in type_str:
            return ConnectionType.WIFI_2G
        # Fallback: if object is a WLAN subclass, it's wireless
        cls_name = type(conn_obj).__name__
        if "Wlan" in cls_name:
            return ConnectionType.WIFI_2G
        return ConnectionType.UNKNOWN
