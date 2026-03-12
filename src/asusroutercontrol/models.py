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


class SpeedTestResult(BaseModel):
    """Speed test result — single provider or multi-source composite."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    download_bps: float | None = None
    upload_bps: float | None = None
    ping_ms: float | None = None
    jitter_ms: float | None = None
    server_name: str | None = None
    server_id: str | None = None
    is_peak: bool = False
    error: str | None = None
    source: str = "ookla"  # provider name or 'composite'
    session_id: str = ""  # groups providers from one test session
    provider_details_json: str = "{}"  # per-provider breakdown


class LatencyProbe(BaseModel):
    """Latency measurement to a single target."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    target: str = ""  # gateway / cloudflare / google
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float = 0.0
    samples: int = 0


class SystemSnapshot(BaseModel):
    """Point-in-time router health."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cpu_pct: float | None = None
    ram_pct: float | None = None
    temp_c: float | None = None
    uptime_s: int | None = None
    conntrack_count: int | None = None
    conntrack_max: int | None = None


class WiFiSnapshot(BaseModel):
    """Per-band WiFi health."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    band: str = ""  # "2.4" or "5"
    client_count: int = 0
    avg_rssi: float | None = None
    min_rssi: float | None = None
    channel: str | None = None
    noise_floor: float | None = None


class ConfigSnapshot(BaseModel):
    """Point-in-time capture of tracked NVRAM settings."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = "scheduled"  # scheduled / manual / pre-change / post-change
    nvram_json: str = "{}"  # JSON blob of key→value
    diff_summary: str = ""  # human-readable diff vs previous


class ConfigEvent(BaseModel):
    """Discrete router configuration or lifecycle event."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: str = ""  # reboot / config_change / service_restart / firmware_update
    description: str = ""
    nvram_changes_json: str = "{}"  # JSON of changed keys {key: [old, new]}
    triggered_by: str = "user"  # user / scheduler / auto


class RouterSnapshot(BaseModel):
    """Complete point-in-time router state."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    system: SystemInfo | None = None
    wan: WANStatus | None = None
    devices: list[Device] = Field(default_factory=list)
    traffic: TrafficSnapshot | None = None
