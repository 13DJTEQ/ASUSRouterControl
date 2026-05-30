"""ISP + client speed/load dashboard composition layer."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from statistics import mean

from asusroutercontrol.datastore import DataStore


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _timestamp_sort_key(row: dict) -> str:
    ts = row.get("timestamp")
    return str(ts) if ts is not None else ""


def _in_window(ts: str | None, *, start: datetime, end: datetime) -> bool:
    parsed = _parse_iso(ts)
    if not parsed:
        return False
    return start <= parsed <= end


def _round_optional(value: float | int | None, *, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _mbps_optional(value_bps: float | int | None, *, digits: int = 2) -> float | None:
    if value_bps is None:
        return None
    try:
        return round(float(value_bps) / 1_000_000, digits)
    except (TypeError, ValueError):
        return None


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(mean(values), 2)


def _extract_confidence(row: dict) -> float | None:
    raw = row.get("provider_details_json")
    if not raw:
        return None
    try:
        details = json.loads(raw)
    except Exception:
        return None
    confidence = details.get("confidence")
    if isinstance(confidence, (int, float)):
        return float(confidence)
    return None


def _normalize_client_row(row: dict) -> dict:
    return {
        "mac": row.get("mac"),
        "hostname": row.get("hostname"),
        "band": row.get("band"),
        "timestamp": row.get("timestamp"),
        "has_signal": bool(row.get("has_signal")),
        "sample_count": int(row.get("sample_count") or 0),
        "signal_samples": int(row.get("signal_samples") or 0),
        "placeholder_samples": int(row.get("placeholder_samples") or 0),
        "latest_load_pct": _round_optional(row.get("load_pct"), digits=1),
        "avg_load_pct": _round_optional(row.get("avg_load_pct"), digits=1),
        "peak_load_pct": _round_optional(row.get("peak_load_pct"), digits=1),
        "tx_rate_mbps": _round_optional(row.get("tx_rate_mbps"), digits=2),
        "rx_rate_mbps": _round_optional(row.get("rx_rate_mbps"), digits=2),
        "rssi": row.get("rssi"),
    }


def _build_isp_panel(speed_rows: list[dict]) -> dict:
    ordered = sorted(speed_rows, key=_timestamp_sort_key, reverse=True)
    quality_counts = {"ok": 0, "suspect": 0, "error": 0, "other": 0}
    source_counts: dict[str, int] = {}

    downloads_mbps: list[float] = []
    uploads_mbps: list[float] = []
    pings_ms: list[float] = []
    jitters_ms: list[float] = []
    confidences: list[float] = []
    for row in ordered:
        quality = str(row.get("quality") or "ok").lower()
        if quality in quality_counts:
            quality_counts[quality] += 1
        else:
            quality_counts["other"] += 1

        source = str(row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

        dl_mbps = _mbps_optional(row.get("download_bps"))
        if dl_mbps is not None:
            downloads_mbps.append(dl_mbps)
        ul_mbps = _mbps_optional(row.get("upload_bps"))
        if ul_mbps is not None:
            uploads_mbps.append(ul_mbps)

        ping_ms = _round_optional(row.get("ping_ms"), digits=2)
        if ping_ms is not None:
            pings_ms.append(ping_ms)
        jitter_ms = _round_optional(row.get("jitter_ms"), digits=2)
        if jitter_ms is not None:
            jitters_ms.append(jitter_ms)

        confidence = _extract_confidence(row)
        if confidence is not None:
            confidences.append(confidence)

    latest = ordered[0] if ordered else None
    latest_test = (
        {
            "timestamp": latest.get("timestamp"),
            "source": latest.get("source"),
            "quality": latest.get("quality"),
            "download_mbps": _mbps_optional(latest.get("download_bps")),
            "upload_mbps": _mbps_optional(latest.get("upload_bps")),
            "ping_ms": _round_optional(latest.get("ping_ms"), digits=2),
            "jitter_ms": _round_optional(latest.get("jitter_ms"), digits=2),
            "error": latest.get("error"),
            "session_id": latest.get("session_id"),
        }
        if latest
        else None
    )

    return {
        "tests_total": len(ordered),
        "quality_counts": quality_counts,
        "source_counts": source_counts,
        "avg_download_mbps": _safe_mean(downloads_mbps),
        "avg_upload_mbps": _safe_mean(uploads_mbps),
        "avg_ping_ms": _safe_mean(pings_ms),
        "avg_jitter_ms": _safe_mean(jitters_ms),
        "avg_confidence": _safe_mean(confidences),
        "latest_test": latest_test,
    }


async def _build_context_timeline(
    store: DataStore,
    *,
    speed_rows: list[dict],
    timeline_points: int,
    context_minutes: int,
    client_limit: int,
) -> list[dict]:
    ordered = sorted(speed_rows, key=_timestamp_sort_key, reverse=True)
    anchors = ordered[: max(1, timeline_points)]
    timeline: list[dict] = []

    for row in anchors:
        anchor_ts = _parse_iso(row.get("timestamp"))
        if not anchor_ts:
            continue
        window_start = anchor_ts - timedelta(minutes=max(1, context_minutes))
        window_end = anchor_ts + timedelta(minutes=max(1, context_minutes))
        client_rows = await store.get_client_load_rollups_between(
            start_ts=window_start.isoformat(),
            end_ts=window_end.isoformat(),
            limit=max(1, client_limit),
        )
        normalized_clients = [_normalize_client_row(client) for client in client_rows]
        top_client = normalized_clients[0] if normalized_clients else None
        timeline.append(
            {
                "speed_test_timestamp": row.get("timestamp"),
                "source": row.get("source"),
                "quality": row.get("quality"),
                "download_mbps": _mbps_optional(row.get("download_bps")),
                "upload_mbps": _mbps_optional(row.get("upload_bps")),
                "ping_ms": _round_optional(row.get("ping_ms"), digits=2),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "clients_seen": len(normalized_clients),
                "clients_with_signal": sum(
                    1 for client in normalized_clients if client["has_signal"]
                ),
                "top_client": top_client,
            }
        )
    return timeline


async def build_isp_client_dashboard(
    store: DataStore,
    *,
    hours: int = 24,
    clients: int = 10,
    timeline_points: int = 6,
    context_minutes: int = 15,
) -> dict:
    """Return ISP + client dashboard payload over a shared lookback window."""
    bounded_hours = max(1, hours)
    now = datetime.utcnow()
    window_start = now - timedelta(hours=bounded_hours)
    query_days = max(1, math.ceil(bounded_hours / 24))

    speed_rows = await store.get_speed_tests(days=query_days)
    speed_rows = [
        row
        for row in speed_rows
        if _in_window(row.get("timestamp"), start=window_start, end=now)
    ]

    client_rows = await store.get_client_load_rollups_between(
        start_ts=window_start.isoformat(),
        end_ts=now.isoformat(),
        limit=max(1, clients),
    )
    normalized_client_rows = [_normalize_client_row(row) for row in client_rows]
    client_panel = {
        "clients_total": len(normalized_client_rows),
        "clients_with_signal": sum(
            1 for row in normalized_client_rows if row["has_signal"]
        ),
        "clients_placeholder_only": sum(
            1 for row in normalized_client_rows if not row["has_signal"]
        ),
        "top_clients": normalized_client_rows,
    }

    timeline = await _build_context_timeline(
        store,
        speed_rows=speed_rows,
        timeline_points=timeline_points,
        context_minutes=context_minutes,
        client_limit=clients,
    )
    return {
        "generated_at": now.isoformat(),
        "window": {
            "hours": bounded_hours,
            "start": window_start.isoformat(),
            "end": now.isoformat(),
        },
        "isp_performance": _build_isp_panel(speed_rows),
        "client_speed_load": client_panel,
        "isp_client_timeline": timeline,
    }
