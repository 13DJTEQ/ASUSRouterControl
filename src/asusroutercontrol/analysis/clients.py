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

def _row_has_signal(row: dict) -> bool:
    has_signal = row.get("has_signal")
    if has_signal is not None:
        return bool(has_signal)
    return (
        row.get("tx_rate_mbps") is not None
        or row.get("rx_rate_mbps") is not None
        or row.get("rssi") is not None
    )


def _row_rank(row: dict) -> tuple[int, float, str]:
    return (
        1 if _row_has_signal(row) else 0,
        float(row.get("load_pct") or 0.0),
        str(row.get("timestamp") or ""),
    )


def format_client_load_display(load_pct: float | int | None) -> str:
    """Format client load for menu display with explicit idle semantics."""
    if load_pct is None:
        return "n/a"
    try:
        value = float(load_pct)
    except (TypeError, ValueError):
        return "n/a"
    if value <= 0:
        return "idle"
    if value < 10:
        return f"{value:.1f}%"
    return f"{value:.0f}%"


async def get_client_load_summary(store: DataStore) -> list[dict]:
    """Fetch latest loads and dedupe per MAC, preferring measurable rows."""
    rows = await store.get_client_loads(hours=1)
    best_valid: dict[str, dict] = {}
    best_fallback: dict[str, dict] = {}
    for row in rows:
        mac = row.get("mac")
        if not mac:
            continue
        if _row_has_signal(row):
            prev = best_valid.get(mac)
            if prev is None or _row_rank(row) > _row_rank(prev):
                best_valid[mac] = row
            continue
        prev = best_fallback.get(mac)
        if prev is None or _row_rank(row) > _row_rank(prev):
            best_fallback[mac] = row

    combined = list(best_valid.values())
    for mac, row in best_fallback.items():
        if mac not in best_valid:
            combined.append(row)

    # Sort by signal quality then load descending, cap at 15
    sorted_clients = sorted(combined, key=_row_rank, reverse=True)
    return sorted_clients[:15]
