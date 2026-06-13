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


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_metric(row: dict, aliases: tuple[str, ...]) -> float | None:
    for alias in aliases:
        parsed = _to_float(row.get(alias))
        if parsed is not None:
            return parsed
    return None


def _build_latency_panel(latency_rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for row in latency_rows:
        target = str(row.get("target") or "unknown")
        grouped.setdefault(target, []).append(row)
    targets: dict[str, dict] = {}
    for target, rows in grouped.items():
        avg_ms = [_to_float(r.get("avg_ms")) for r in rows]
        jitter = [_to_float(r.get("jitter_ms")) for r in rows]
        loss = [_to_float(r.get("loss_pct")) for r in rows]
        avg_ms_clean = [v for v in avg_ms if v is not None]
        jitter_clean = [v for v in jitter if v is not None]
        loss_clean = [v for v in loss if v is not None]
        targets[target] = {
            "samples": len(rows),
            "avg_ms": _safe_mean(avg_ms_clean),
            "avg_jitter_ms": _safe_mean(jitter_clean),
            "max_loss_pct": round(max(loss_clean), 2) if loss_clean else None,
            "loss_events": sum(1 for value in loss_clean if value > 0),
        }
    return {"samples": len(latency_rows), "targets": targets}


def _build_wifi_panel(wifi_rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for row in wifi_rows:
        grouped.setdefault(str(row.get("band") or "unknown"), []).append(row)
    bands: dict[str, dict] = {}
    for band, rows in grouped.items():
        client_count = [_to_float(r.get("client_count")) for r in rows]
        avg_rssi = [_to_float(r.get("avg_rssi")) for r in rows]
        noise_floor = [_to_float(r.get("noise_floor")) for r in rows]
        client_count_clean = [v for v in client_count if v is not None]
        avg_rssi_clean = [v for v in avg_rssi if v is not None]
        noise_floor_clean = [v for v in noise_floor if v is not None]
        bands[band] = {
            "samples": len(rows),
            "avg_clients": _safe_mean(client_count_clean),
            "avg_rssi": _safe_mean(avg_rssi_clean),
            "avg_noise_floor": _safe_mean(noise_floor_clean),
        }
    return {"samples": len(wifi_rows), "bands": bands}


def _build_system_panel(system_rows: list[dict]) -> dict:
    cpu = [_to_float(r.get("cpu_pct")) for r in system_rows]
    ram = [_to_float(r.get("ram_pct")) for r in system_rows]
    temp = [_to_float(r.get("temp_c")) for r in system_rows]
    cpu_clean = [v for v in cpu if v is not None]
    ram_clean = [v for v in ram if v is not None]
    temp_clean = [v for v in temp if v is not None]
    return {
        "samples": len(system_rows),
        "cpu_avg_pct": _safe_mean(cpu_clean),
        "ram_avg_pct": _safe_mean(ram_clean),
        "temp_avg_c": _safe_mean(temp_clean),
        "temp_peak_c": round(max(temp_clean), 2) if temp_clean else None,
    }


def _build_router_direct_panel(router_rows: list[dict]) -> dict:
    if not router_rows:
        return {"samples": 0}
    normalized = sorted(router_rows, key=_timestamp_sort_key)
    latest = normalized[-1]
    load_1m = [_pick_metric(r, ("load_1m", "load1", "loadavg_1m")) for r in normalized]
    load_1m_clean = [v for v in load_1m if v is not None]

    def _counter_delta(aliases: tuple[str, ...]) -> float:
        values = [_pick_metric(r, aliases) for r in normalized]
        values_clean = [v for v in values if v is not None]
        if len(values_clean) < 2:
            return 0.0
        return round(max(0.0, values_clean[-1] - values_clean[0]), 2)

    return {
        "samples": len(router_rows),
        "latest_timestamp": latest.get("timestamp"),
        "interfaces": {
            "wan": latest.get("wan_interface") or latest.get("wan_ifname"),
            "lan": latest.get("lan_interface") or latest.get("lan_ifname"),
        },
        "load_1m_avg": _safe_mean(load_1m_clean),
        "load_1m_peak": round(max(load_1m_clean), 3) if load_1m_clean else None,
        "drop_delta_total": round(
            _counter_delta(("wan_rx_drops", "wan_drops_rx"))
            + _counter_delta(("wan_tx_drops", "wan_drops_tx"))
            + _counter_delta(("lan_rx_drops", "lan_drops_rx"))
            + _counter_delta(("lan_tx_drops", "lan_drops_tx")),
            2,
        ),
        "error_delta_total": round(
            _counter_delta(("wan_rx_errors", "wan_errors_rx"))
            + _counter_delta(("wan_tx_errors", "wan_errors_tx"))
            + _counter_delta(("lan_rx_errors", "lan_errors_rx"))
            + _counter_delta(("lan_tx_errors", "lan_errors_tx")),
            2,
        ),
        "available_fields": sorted({key for row in router_rows for key in row.keys()}),
    }


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
    latency_rows = await store.get_latency_probes(days=query_days)
    latency_rows = [
        row
        for row in latency_rows
        if _in_window(row.get("timestamp"), start=window_start, end=now)
    ]
    wifi_rows = await store.get_wifi_snapshots(days=query_days)
    wifi_rows = [
        row
        for row in wifi_rows
        if _in_window(row.get("timestamp"), start=window_start, end=now)
    ]
    system_rows = await store.get_system_snapshots(days=query_days)
    system_rows = [
        row
        for row in system_rows
        if _in_window(row.get("timestamp"), start=window_start, end=now)
    ]
    router_rows: list[dict] = []
    if hasattr(store, "get_router_perf_snapshots"):
        router_rows = await store.get_router_perf_snapshots(days=query_days)  # type: ignore[attr-defined]
    elif hasattr(store, "get_router_perf_series"):
        router_rows = await store.get_router_perf_series(days=query_days)  # type: ignore[attr-defined]
    router_rows = [
        row
        for row in router_rows
        if _in_window(row.get("timestamp"), start=window_start, end=now)
    ]
    return {
        "generated_at": now.isoformat(),
        "window": {
            "hours": bounded_hours,
            "start": window_start.isoformat(),
            "end": now.isoformat(),
        },
        "isp_performance": _build_isp_panel(speed_rows),
        "latency_health": _build_latency_panel(latency_rows),
        "wifi_health": _build_wifi_panel(wifi_rows),
        "system_health": _build_system_panel(system_rows),
        "router_direct": _build_router_direct_panel(router_rows),
        "client_speed_load": client_panel,
        "isp_client_timeline": timeline,
    }
