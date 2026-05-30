"""Traffic analysis — bandwidth trending and anomaly detection."""

from __future__ import annotations

from asusroutercontrol.datastore import DataStore


def _fmt_bytes(b: float | None) -> str:
    if b is None or b == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _fmt_rate(bps: float | None) -> str:
    if bps is None or bps == 0:
        return "0 bps"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} Kbps"
    return f"{bps:.0f} bps"


async def get_traffic_summary(store: DataStore, *, hours: int = 24) -> dict:
    """Aggregate traffic over the given window with formatted values."""
    agg = await store.get_traffic_aggregates(hours=hours)
    if not agg or not agg.get("samples"):
        return {"period_hours": hours, "samples": 0}
    return {
        "period_hours": hours,
        "samples": agg["samples"],
        "total_download": _fmt_bytes(agg.get("total_rx")),
        "total_upload": _fmt_bytes(agg.get("total_tx")),
        "avg_download_rate": _fmt_rate(agg.get("avg_rx_rate")),
        "avg_upload_rate": _fmt_rate(agg.get("avg_tx_rate")),
        "peak_download_rate": _fmt_rate(agg.get("peak_rx_rate")),
        "peak_upload_rate": _fmt_rate(agg.get("peak_tx_rate")),
        "raw": agg,
    }


async def detect_anomalies(store: DataStore, *, window_hours: int = 24) -> list[dict]:
    """Flag traffic snapshots where rate exceeds 3x the average."""
    agg = await store.get_traffic_aggregates(hours=window_hours)
    if not agg or not agg.get("avg_rx_rate"):
        return []

    avg_rx = agg["avg_rx_rate"] or 0
    avg_tx = agg["avg_tx_rate"] or 0
    threshold_rx = avg_rx * 3
    threshold_tx = avg_tx * 3

    history = await store.get_traffic_history(hours=window_hours)
    anomalies = []
    for snap in history:
        rx = snap.get("rx_rate_bps") or 0
        tx = snap.get("tx_rate_bps") or 0
        if rx > threshold_rx or tx > threshold_tx:
            anomalies.append({
                "timestamp": snap["timestamp"],
                "rx_rate": _fmt_rate(rx),
                "tx_rate": _fmt_rate(tx),
                "rx_factor": round(rx / avg_rx, 1) if avg_rx else 0,
                "tx_factor": round(tx / avg_tx, 1) if avg_tx else 0,
            })
    return anomalies
