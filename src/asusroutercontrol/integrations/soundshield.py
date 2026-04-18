"""SoundShield integration — JSON export for network-aware audio device discovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from asusroutercontrol._time import utcnow
from asusroutercontrol.models import Device, WANStatus

log = logging.getLogger(__name__)

# OUI prefixes / hostname patterns for known audio device manufacturers
AUDIO_DEVICE_HINTS = {
    "denon", "marantz", "sonos", "bose", "apple", "airplay",
    "homepod", "heos", "yamaha", "bluesound", "harman",
    "jbl", "bang", "olufsen", "kef", "devialet",
}


def _is_audio_device(dev: Device) -> bool:
    """Heuristic: check hostname/MAC for audio device patterns."""
    name = (dev.hostname or "").lower()
    return any(hint in name for hint in AUDIO_DEVICE_HINTS)


def _assess_network_health(
    devices: list[Device], wan: WANStatus
) -> str:
    if wan.status != "connected":
        return "degraded"
    wifi_clients = [d for d in devices if "GHz" in (d.connection or "")]
    weak_signal = [d for d in wifi_clients if d.rssi is not None and d.rssi < -75]
    if len(weak_signal) > len(wifi_clients) * 0.5 and wifi_clients:
        return "fair"
    return "good"


async def export_soundshield_json(
    devices: list[Device],
    wan: WANStatus,
    output_path: Path,
) -> None:
    """Write enriched device/network data for SoundShield consumption."""
    payload = {
        "timestamp": utcnow().isoformat() + "Z",
        "devices": [
            {
                "mac": d.mac,
                "ip": d.ip,
                "hostname": d.hostname,
                "connection": d.connection.value,
                "band": d.band,
                "rssi": d.rssi,
                "tx_rate_mbps": d.tx_rate_mbps,
                "is_audio_device": _is_audio_device(d),
            }
            for d in devices
        ],
        "wan": {
            "status": wan.status,
            "ip_address": wan.ip_address,
        },
        "network_health": _assess_network_health(devices, wan),
        "audio_devices": [
            {
                "mac": d.mac,
                "ip": d.ip,
                "hostname": d.hostname,
                "connection": d.connection.value,
                "rssi": d.rssi,
                "tx_rate_mbps": d.tx_rate_mbps,
            }
            for d in devices
            if _is_audio_device(d)
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))
    log.debug("SoundShield export written: %s", output_path)
