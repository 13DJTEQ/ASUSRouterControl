"""Device analysis — presence tracking and new device detection."""

from __future__ import annotations

from datetime import datetime

from asusroutercontrol.datastore import DataStore


async def get_device_summary(store: DataStore) -> dict:
    """High-level device stats."""
    all_devs = await store.get_all_devices()
    unknown = await store.get_unknown_devices()
    return {
        "total_known": len(all_devs),
        "unknown_count": len(unknown),
        "unknown_devices": unknown,
    }


async def get_presence_history(store: DataStore, mac: str, *, limit: int = 50) -> list[dict]:
    """Return session timeline for a specific device."""
    return await store.get_device_sessions(mac, limit=limit)


async def detect_absent_devices(
    store: DataStore, current_macs: set[str], *, threshold_minutes: int = 10
) -> list[dict]:
    """Identify devices that were recently seen but are now absent."""
    all_devs = await store.get_all_devices()
    absent = []
    now = datetime.utcnow()
    for dev in all_devs:
        if dev["mac"] in current_macs:
            continue
        last = datetime.fromisoformat(dev["last_seen"])
        delta = (now - last).total_seconds() / 60
        if delta <= threshold_minutes * 5:  # Only flag recently active
            dev["absent_minutes"] = round(delta)
            absent.append(dev)
    return absent
