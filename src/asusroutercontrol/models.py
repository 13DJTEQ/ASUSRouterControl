"""Pydantic data models for router state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ConnectionType(StrEnum):
    WIRED = "wired"
    WIFI_2G = "2.4GHz"
    WIFI_5G = "5GHz"
    WIFI_6G = "6GHz"
    UNKNOWN = "unknown"


class Device(BaseModel):
    mac: str
    ip: str | None = None
    hostname: str | None = None
    connection: ConnectionType = ConnectionType.UNKNOWN
    band: str | None = None
    rssi: int | None = None
    tx_rate_mbps: float | None = None
    rx_rate_mbps: float | None = None
    is_online: bool = True
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class TrafficSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate_bps: float | None = None
    tx_rate_bps: float | None = None


class SystemInfo(BaseModel):
    model: str | None = None
    firmware_version: str | None = None
    uptime_seconds: int | None = None
    cpu_usage_percent: float | None = None
    ram_total_mb: float | None = None
    ram_used_mb: float | None = None
    ram_usage_percent: float | None = None
    temperature_c: float | None = None


class WANStatus(BaseModel):
    status: str = "unknown"
    ip_address: str | None = None
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)
    uptime_seconds: int | None = None


class WiFiClient(BaseModel):
    mac: str
    ip: str | None = None
    hostname: str | None = None
    band: str | None = None
    rssi: int | None = None
    tx_rate_mbps: float | None = None
    rx_rate_mbps: float | None = None


class PortRule(BaseModel):
    name: str = ""
    protocol: str = "tcp"
    src_port: str = ""
    dst_port: str = ""
    dst_ip: str = ""
    enabled: bool = True


class RouterSnapshot(BaseModel):
    """Complete point-in-time router state."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    system: SystemInfo | None = None
    wan: WANStatus | None = None
    devices: list[Device] = Field(default_factory=list)
    traffic: TrafficSnapshot | None = None
