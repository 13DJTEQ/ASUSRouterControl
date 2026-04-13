"""AsusWRT / Merlin firmware backend using the asusrouter library."""

from __future__ import annotations

import logging
import socket
from typing import Any

import aiohttp
from asusrouter import AsusData, AsusRouter
from asusrouter.modules.port_forwarding import PortForwardingRule as AsusPortForwardingRule

from asusroutercontrol._time import utcnow
from asusroutercontrol.backends.base import FirmwareBackend
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


class MerlinBackend(FirmwareBackend):
    """Backend for stock AsusWRT and AsusWRT-Merlin firmware."""

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
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(family=socket.AF_INET)
        )
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

    async def get_connected_devices(self) -> list[Device]:
        router = self._ensure_connected()
        now = utcnow()
        try:
            data = await router.async_get_data(AsusData.CLIENTS)
        except Exception:
            log.exception("Failed to get connected devices")
            return []

        devices: list[Device] = []
        if not data or not isinstance(data, dict):
            return devices

        for mac, client in data.items():
            try:
                desc = getattr(client, "description", None)
                conn_obj = getattr(client, "connection", None)
                state = getattr(client, "state", None)

                hostname = getattr(desc, "name", None) if desc else None
                if hostname and hostname.upper() == str(mac).upper():
                    hostname = None

                conn_type = self._parse_connection_obj(conn_obj)
                ip_addr = getattr(conn_obj, "ip_address", None)
                rssi = getattr(conn_obj, "rssi", None)
                tx_rate = (
                    getattr(conn_obj, "tx_rate", None)
                    or getattr(conn_obj, "tx_speed", None)
                )
                rx_rate = (
                    getattr(conn_obj, "rx_rate", None)
                    or getattr(conn_obj, "rx_speed", None)
                )
                is_online = self._parse_online_state(state, conn_obj)

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

        wan_net = data.get("wan", {})
        return TrafficSnapshot(
            timestamp=utcnow(),
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

        try:
            sys_data = await router.async_get_data(AsusData.SYSINFO)
            if sys_data and isinstance(sys_data, dict):
                sys_info = sys_data.get("sys", {})
                uptime_str = sys_info.get("uptimeStr", "")
                import re

                m = re.search(r"\((\d+)\s+secs? since boot\)", uptime_str)
                if m:
                    info.uptime_seconds = int(m.group(1))
        except Exception:
            log.debug("SYSINFO data unavailable")

        try:
            temp_data = await router.async_get_data(AsusData.TEMPERATURE)
            if temp_data and isinstance(temp_data, dict):
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

        internet = data.get("internet", {})
        primary = data.get("0", data.get(0, {}))
        link = internet.get("link", None)
        link_name = getattr(link, "name", str(link)).upper() if link else ""
        status = "connected" if "CONNECTED" in link_name else "disconnected"
        ip_addr = internet.get("ip_address") or primary.get("real_ip")
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

    async def get_lan_clients(self) -> list[LanClient]:
        devices = await self.get_connected_devices()
        return [
            LanClient(mac=d.mac, ip=d.ip, hostname=d.hostname)
            for d in devices
            if d.connection == ConnectionType.WIRED
        ]

    async def set_state(self, action: str, **kwargs) -> bool:
        router = self._ensure_connected()
        try:
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
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return [
                self._from_asus_port_forwarding_rule(rule)
                for rule in data["rules"]
                if rule is not None
            ]
        if isinstance(data, list):
            return [
                self._from_asus_port_forwarding_rule(rule)
                for rule in data
                if rule is not None
            ]
        if not isinstance(data, dict):
            return []

        rules: list[PortRule] = []
        for _key, rule in data.items():
            if isinstance(rule, dict):
                rules.append(self._from_asus_port_forwarding_rule(rule))
        return rules

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        router = self._ensure_connected()
        desired_rules = [
            self._to_asus_port_forwarding_rule(rule)
            for rule in rules
            if rule.enabled
        ]

        try:
            current_data = await router.async_get_data(AsusData.PORT_FORWARDING)
        except Exception:
            log.exception("Failed to fetch current port forwarding rules before apply")
            current_data = None

        current_rules = self._extract_asus_port_forwarding_rules(current_data)
        if self._pf_signature(current_rules) == self._pf_signature(desired_rules):
            log.info("Port forwarding rules already match desired state; no changes needed")
            return True

        try:
            applied = await router.async_apply_port_forwarding_rules(desired_rules)
        except Exception:
            log.exception("Failed to apply port forwarding rules")
            return False

        if not applied:
            log.warning("Router refused port forwarding rule update")
            return False

        verify = await self.get_port_forwarding()
        verify_signature = self._port_rule_signature(
            [rule for rule in verify if rule.enabled]
        )
        desired_signature = self._port_rule_signature(
            [self._normalize_port_rule(rule) for rule in rules if rule.enabled]
        )
        if verify_signature != desired_signature:
            log.warning("Port forwarding verification mismatch after apply")
            return False
        return True

    @staticmethod
    def _parse_online_state(state, conn_obj) -> bool:
        state_name = getattr(state, "name", None)
        if state_name:
            state_upper = str(state_name).upper()
            if "DISCONNECTED" in state_upper:
                return False
            if "CONNECTED" in state_upper:
                return True
        state_str = str(state).upper() if state is not None else ""
        if "DISCONNECTED" in state_str:
            return False
        if "CONNECTED" in state_str:
            return True
        online = getattr(conn_obj, "online", None)
        if online is not None:
            return bool(online)
        if state is None:
            return True
        return bool(state)

    @staticmethod
    def _parse_connection_obj(conn_obj) -> ConnectionType:
        if conn_obj is None:
            return ConnectionType.UNKNOWN
        conn_type = (
            getattr(conn_obj, "connection_type", None)
            or getattr(conn_obj, "type", None)
        )
        # Prefer enum .name (e.g. "WLAN_5G") over str() which may include module path
        if conn_type is not None:
            type_str = (getattr(conn_type, "name", None) or str(conn_type)).upper()
        else:
            type_str = str(conn_obj).upper()

        if not type_str or "DISCONNECTED" in type_str:
            return ConnectionType.UNKNOWN
        if "WIRED" in type_str or "ETHERNET" in type_str:
            return ConnectionType.WIRED
        # 6GHz must be checked before 5G to avoid ambiguous partial matches
        if "6G" in type_str or "WLAN6" in type_str or "WIFI6" in type_str:
            return ConnectionType.WIFI_6G
        # 5GHz: explicit tag or WLAN_5 / WLAN5 naming variants
        if "5G" in type_str or "WLAN_5" in type_str or "WLAN5" in type_str:
            return ConnectionType.WIFI_5G
        # 2.4GHz: explicit tag or WLAN_2 / WLAN2 naming variants
        if "2G" in type_str or "2.4" in type_str or "WLAN_2" in type_str or "WLAN2" in type_str:
            return ConnectionType.WIFI_2G
        # Generic wireless label without explicit band — inspect class name for hints
        cls_name = type(conn_obj).__name__
        if "WLAN" in type_str or "WIFI" in type_str or "WIRELESS" in type_str or "Wlan" in cls_name:
            cls_upper = cls_name.upper()
            if "5" in cls_upper:
                return ConnectionType.WIFI_5G
            if "6" in cls_upper:
                return ConnectionType.WIFI_6G
            return ConnectionType.WIFI_2G  # default: 2.4GHz is the most common band
        return ConnectionType.UNKNOWN

    @staticmethod
    def _safe_str(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    @classmethod
    def _normalize_port_rule(cls, rule: PortRule) -> PortRule:
        return PortRule(
            name=cls._safe_str(rule.name),
            protocol=cls._safe_str(rule.protocol).lower() or "tcp",
            src_port=cls._safe_str(rule.src_port),
            dst_port=cls._safe_str(rule.dst_port),
            dst_ip=cls._safe_str(rule.dst_ip),
            enabled=bool(rule.enabled),
        )

    @classmethod
    def _port_rule_signature(cls, rules: list[PortRule]) -> list[tuple[str, ...]]:
        normalized = [cls._normalize_port_rule(rule) for rule in rules]
        return sorted(
            (
                rule.name,
                rule.protocol,
                rule.src_port,
                rule.dst_ip,
                rule.dst_port,
            )
            for rule in normalized
        )

    @classmethod
    def _pf_signature(cls, rules: list[AsusPortForwardingRule]) -> list[tuple[str, ...]]:
        return sorted(
            (
                cls._safe_str(rule.name),
                cls._safe_str(rule.protocol).lower(),
                cls._safe_str(rule.port_external),
                cls._safe_str(rule.ip_address),
                cls._safe_str(rule.port),
            )
            for rule in rules
        )

    @classmethod
    def _extract_asus_port_forwarding_rules(
        cls, data: Any
    ) -> list[AsusPortForwardingRule]:
        if isinstance(data, dict):
            data = data.get("rules", [])
        if not isinstance(data, list):
            return []
        results: list[AsusPortForwardingRule] = []
        for item in data:
            if isinstance(item, AsusPortForwardingRule):
                results.append(item)
            elif isinstance(item, dict):
                results.append(
                    AsusPortForwardingRule(
                        name=cls._safe_str(item.get("name")),
                        ip_address=cls._safe_str(item.get("dst_ip") or item.get("ip_address")),
                        port=cls._safe_str(item.get("dst_port") or item.get("port")),
                        protocol=cls._safe_str(item.get("protocol")).lower(),
                        ip_external=cls._safe_str(item.get("ip_external")),
                        port_external=cls._safe_str(
                            item.get("src_port") or item.get("port_external")
                        ),
                    )
                )
        return results

    @classmethod
    def _to_asus_port_forwarding_rule(cls, rule: PortRule) -> AsusPortForwardingRule:
        normalized = cls._normalize_port_rule(rule)
        return AsusPortForwardingRule(
            name=normalized.name,
            ip_address=normalized.dst_ip,
            port=normalized.dst_port,
            protocol=normalized.protocol,
            ip_external="",
            port_external=normalized.src_port,
        )

    @classmethod
    def _from_asus_port_forwarding_rule(cls, rule: Any) -> PortRule:
        if isinstance(rule, dict):
            return cls._normalize_port_rule(
                PortRule(
                    name=cls._safe_str(rule.get("name")),
                    protocol=cls._safe_str(rule.get("protocol")),
                    src_port=cls._safe_str(rule.get("src_port") or rule.get("port_external")),
                    dst_port=cls._safe_str(rule.get("dst_port") or rule.get("port")),
                    dst_ip=cls._safe_str(rule.get("dst_ip") or rule.get("ip_address")),
                    enabled=bool(rule.get("enabled", True)),
                )
            )

        return cls._normalize_port_rule(
            PortRule(
                name=cls._safe_str(getattr(rule, "name", "")),
                protocol=cls._safe_str(getattr(rule, "protocol", "")),
                src_port=cls._safe_str(getattr(rule, "port_external", "")),
                dst_port=cls._safe_str(getattr(rule, "port", "")),
                dst_ip=cls._safe_str(getattr(rule, "ip_address", "")),
                enabled=True,
            )
        )
