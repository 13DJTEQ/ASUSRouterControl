"""Per-client traffic load analysis and health scoring."""

from __future__ import annotations

import logging
from datetime import datetime

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import ClientLoad, Device

log = logging.getLogger(__name__)

# Baseline link rates per band (Mbps) — conservative estimates for RT-AC68U.
BAND_LINK_RATES: dict[str, float] = {
    "2.4GHz": 150.0,
    "5GHz": 600.0,
    "wired": 1000.0,
}
DEFAULT_LINK_RATE = 150.0

# Thresholds
LOAD_WARN_PCT = 50.0
LOAD_CRIT_PCT = 80.0
RSSI_WEAK_DBM = -75


def _health_dot(load_pct: float, rssi: int | None) -> str:
    """Color-coded health indicator."""
    if rssi is not None and rssi < RSSI_WEAK_DBM:
        return "🔴"
    if load_pct >= LOAD_CRIT_PCT:
        return "🔴"
    if load_pct >= LOAD_WARN_PCT:
        return "🟡"
    return "🟢"


def compute_client_loads(devices: list[Device]) -> list[ClientLoad]:
    """Compute load percentage for each device based on tx/rx vs band link rate."""
    now = datetime.utcnow()
    results: list[ClientLoad] = []

    for dev in devices:
        if not dev.is_online:
            continue

        tx = dev.tx_rate_mbps or 0.0
        rx = dev.rx_rate_mbps or 0.0
        peak = max(tx, rx)

        # Determine link rate from connection type
        link_rate = BAND_LINK_RATES.get(dev.connection.value, DEFAULT_LINK_RATE)
        load_pct = min(100.0, (peak / link_rate) * 100.0) if link_rate > 0 else 0.0
        health = _health_dot(load_pct, dev.rssi)

        results.append(ClientLoad(
            timestamp=now,
            mac=dev.mac,
            hostname=dev.hostname,
            band=dev.band or dev.connection.value,
            rssi=dev.rssi,
            tx_rate_mbps=dev.tx_rate_mbps,
            rx_rate_mbps=dev.rx_rate_mbps,
            load_pct=round(load_pct, 1),
            health=health,
        ))

    # Sort by load descending
    results.sort(key=lambda c: c.load_pct, reverse=True)
    return results


async def get_client_load_summary(store: DataStore) -> list[dict]:
    """Fetch latest client loads and deduplicate per MAC (highest load wins)."""
    rows = await store.get_client_loads(hours=1)
    seen: dict[str, dict] = {}
    for row in rows:
        mac = row["mac"]
        if mac not in seen or (row.get("load_pct") or 0) > (seen[mac].get("load_pct") or 0):
            seen[mac] = row
    # Sort by load descending, cap at 15
    sorted_clients = sorted(seen.values(), key=lambda r: r.get("load_pct", 0), reverse=True)
    return sorted_clients[:15]
