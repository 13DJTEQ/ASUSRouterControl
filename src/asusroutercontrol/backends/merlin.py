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
        if not data:
            return devices

        # asusrouter returns client data as a dict keyed by MAC
        clients = data if isinstance(data, dict) else {}
        for mac, info in clients.items():
            if not isinstance(info, dict):
                continue
            conn = self._parse_connection_type(info)
            devices.append(
                Device(
                    mac=str(mac),
                    ip=info.get("ip"),
                    hostname=info.get("name") or info.get("hostname"),
                    connection=conn,
                    band=info.get("band"),
                    rssi=info.get("rssi"),
                    tx_rate_mbps=info.get("tx_rate"),
                    rx_rate_mbps=info.get("rx_rate"),
                    is_online=info.get("online", True),
                    last_seen=now,
                )
            )
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

        return TrafficSnapshot(
            timestamp=datetime.utcnow(),
            rx_bytes=data.get("rx", 0),
            tx_bytes=data.get("tx", 0),
            rx_rate_bps=data.get("rx_speed"),
            tx_rate_bps=data.get("tx_speed"),
        )

    async def get_system_info(self) -> SystemInfo:
        router = self._ensure_connected()
        info = SystemInfo()

        try:
            cpu_data = await router.async_get_data(AsusData.CPU)
            if cpu_data and isinstance(cpu_data, dict):
                info.cpu_usage_percent = cpu_data.get("total", {}).get("usage")
        except Exception:
            log.debug("CPU data unavailable")

        try:
            ram_data = await router.async_get_data(AsusData.RAM)
            if ram_data and isinstance(ram_data, dict):
                total = ram_data.get("total", 0)
                used = ram_data.get("used", 0)
                info.ram_total_mb = total / 1024 if total else None
                info.ram_used_mb = used / 1024 if used else None
                if total:
                    info.ram_usage_percent = round((used / total) * 100, 1)
        except Exception:
            log.debug("RAM data unavailable")

        try:
            fw_data = await router.async_get_data(AsusData.FIRMWARE)
            if fw_data and isinstance(fw_data, dict):
                info.firmware_version = fw_data.get("current")
                info.model = fw_data.get("model")
        except Exception:
            log.debug("Firmware data unavailable")

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

        return WANStatus(
            status=data.get("status", "unknown"),
            ip_address=data.get("ip"),
            gateway=data.get("gateway"),
            dns=data.get("dns", []) if isinstance(data.get("dns"), list) else [],
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
        if not data:
            return []
        rows = self._extract_port_forwarding_rows(data)

        rules: list[PortRule] = []
        for row in rows:
            rule = self._normalize_port_forwarding_row(row)
            rules.append(
                PortRule(
                    name=rule["name"],
                    protocol=rule["protocol"],
                    src_port=rule["src_port"],
                    dst_port=rule["dst_port"],
                    dst_ip=rule["dst_ip"],
                    enabled=rule["enabled"],
                )
            )
        return sorted(
            rules,
            key=lambda r: (r.name, r.dst_ip, r.dst_port, r.src_port, r.protocol),
        )

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        from asusrouter.modules.port_forwarding import (
            PortForwardingRule as AsusPortForwardingRule,
        )

        router = self._ensure_connected()
        desired_rows = [self._normalize_port_forwarding_row(rule) for rule in rules]
        desired_asus = [
            AsusPortForwardingRule(
                name=row["name"],
                ip_address=row["dst_ip"],
                port=row["dst_port"],
                protocol=row["protocol"],
                ip_external="",
                port_external=row["src_port"],
            )
            for row in desired_rows
        ]

        try:
            current = await router.async_get_data(AsusData.PORT_FORWARDING)
        except Exception:
            log.exception("Failed to fetch current port forwarding rules")
            return False

        current_rows = [
            self._normalize_port_forwarding_row(row)
            for row in self._extract_port_forwarding_rows(current)
        ]
        if self._sorted_port_forwarding_rows(current_rows) == self._sorted_port_forwarding_rows(
            desired_rows
        ):
            return True

        try:
            applied = await router.async_apply_port_forwarding_rules(desired_asus)
        except Exception:
            log.exception("Failed to apply port forwarding rules")
            return False
        if not applied:
            return False

        try:
            verify = await router.async_get_data(AsusData.PORT_FORWARDING)
        except Exception:
            log.exception("Failed to verify port forwarding rules after apply")
            return True

        verify_rows = [
            self._normalize_port_forwarding_row(row)
            for row in self._extract_port_forwarding_rows(verify)
        ]
        return self._sorted_port_forwarding_rows(verify_rows) == self._sorted_port_forwarding_rows(
            desired_rows
        )

    # --- Helpers ---

    @staticmethod
    def _extract_port_forwarding_rows(data: object) -> list[object]:
        if isinstance(data, dict):
            rules = data.get("rules")
            if isinstance(rules, list):
                return rules
            return list(data.values())
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _normalize_port_forwarding_row(row: object) -> dict[str, str | bool]:
        if isinstance(row, PortRule):
            return {
                "name": str(row.name or ""),
                "protocol": str(row.protocol or "tcp").lower(),
                "src_port": str(row.src_port or ""),
                "dst_port": str(row.dst_port or ""),
                "dst_ip": str(row.dst_ip or ""),
                "enabled": bool(row.enabled),
            }

        if isinstance(row, dict):
            return {
                "name": str(row.get("name", "") or ""),
                "protocol": str(row.get("protocol", "tcp") or "tcp").lower(),
                "src_port": str(
                    row.get("src_port", row.get("port_external", "")) or ""
                ),
                "dst_port": str(row.get("dst_port", row.get("port", "")) or ""),
                "dst_ip": str(row.get("dst_ip", row.get("ip_address", "")) or ""),
                "enabled": bool(row.get("enabled", True)),
            }

        return {
            "name": str(getattr(row, "name", "") or ""),
            "protocol": str(getattr(row, "protocol", "tcp") or "tcp").lower(),
            "src_port": str(
                getattr(
                    row,
                    "src_port",
                    getattr(row, "port_external", ""),
                )
                or ""
            ),
            "dst_port": str(getattr(row, "dst_port", getattr(row, "port", "")) or ""),
            "dst_ip": str(
                getattr(row, "dst_ip", getattr(row, "ip_address", "")) or ""
            ),
            "enabled": bool(getattr(row, "enabled", True)),
        }

    @staticmethod
    def _sorted_port_forwarding_rows(
        rows: list[dict[str, str | bool]],
    ) -> list[dict[str, str | bool]]:
        return sorted(
            rows,
            key=lambda row: (
                str(row["name"]),
                str(row["dst_ip"]),
                str(row["dst_port"]),
                str(row["src_port"]),
                str(row["protocol"]),
                bool(row["enabled"]),
            ),
        )

    @classmethod
    def _parse_connection_obj(cls, conn: object) -> ConnectionType:
        if conn is None:
            return ConnectionType.UNKNOWN
        for attr in ("connection_type", "type", "connection"):
            value = getattr(conn, attr, None)
            if value is None:
                continue
            raw = str(getattr(value, "name", value) or "").upper()
            if raw:
                return cls._parse_connection_name(raw)
        raw = str(getattr(conn, "name", "") or "").upper()
        if raw:
            return cls._parse_connection_name(raw)
        return ConnectionType.UNKNOWN

    @classmethod
    def _parse_online_state(cls, state: object | None, conn: object | None) -> bool:
        if state is not None:
            state_name = str(getattr(state, "name", state) or "").upper()
            if state_name in {"DISCONNECTED", "OFFLINE"}:
                return False
            if state_name in {"CONNECTED", "ONLINE"}:
                return True

        if conn is not None:
            explicit_online = getattr(conn, "online", None)
            if explicit_online is not None:
                return bool(explicit_online)

        return cls._parse_connection_obj(conn) != ConnectionType.UNKNOWN

    @staticmethod
    def _parse_connection_name(value: str) -> ConnectionType:
        normalized = value.upper()
        if normalized in {"DISCONNECTED", "UNKNOWN", ""}:
            return ConnectionType.UNKNOWN
        if "WIRED" in normalized or "ETH" in normalized:
            return ConnectionType.WIRED
        if "6" in normalized:
            return ConnectionType.WIFI_6G
        if "5" in normalized:
            return ConnectionType.WIFI_5G
        if "2" in normalized or "WLAN" in normalized:
            return ConnectionType.WIFI_2G
        return ConnectionType.UNKNOWN

    @staticmethod
    def _parse_connection_type(info: dict) -> ConnectionType:
        conn = info.get("connection_type", "").lower()
        if "wired" in conn or info.get("isWL") == 0:
            return ConnectionType.WIRED
        band = str(info.get("band", "")).lower()
        if "5" in band:
            return ConnectionType.WIFI_5G
        if "6" in band:
            return ConnectionType.WIFI_6G
        if "2" in band:
            return ConnectionType.WIFI_2G
        if info.get("isWL"):
            return ConnectionType.WIFI_2G  # Default wireless to 2.4
        return ConnectionType.UNKNOWN
