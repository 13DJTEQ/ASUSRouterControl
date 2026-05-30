"""Trend analysis, pattern detection, and ISP SLA tracking.

All analysis operates on existing DB data — no new collection needed.
Uses only stdlib (statistics module + basic linear regression).
"""

from __future__ import annotations

import logging
from datetime import datetime
from statistics import mean, stdev

from asusroutercontrol.datastore import DataStore

log = logging.getLogger(__name__)

PLAN_SPEED_DOWN = 300_000_000  # 300 Mbps
PLAN_SPEED_UP = 35_000_000     # 35 Mbps


# ---------------------------------------------------------------------------
# Linear regression helper (no external deps)
# ---------------------------------------------------------------------------

def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Simple linear regression.  Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 3:
        return 0.0, 0.0, 0.0
    x_bar = mean(xs)
    y_bar = mean(ys)
    ss_xx = sum((x - x_bar) ** 2 for x in xs)
    ss_yy = sum((y - y_bar) ** 2 for y in ys)
    ss_xy = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    if ss_xx == 0:
        return 0.0, y_bar, 0.0
    slope = ss_xy / ss_xx
    intercept = y_bar - slope * x_bar
    r_sq = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy else 0.0
    return slope, intercept, r_sq


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (0-100)."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (len(s) - 1) * (p / 100)
    lower = int(rank)
    upper = min(lower + 1, len(s) - 1)
    weight = rank - lower
    return s[lower] * (1 - weight) + s[upper] * weight


def _iqr_filter(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    """Filter y-outliers beyond 1.5×IQR while preserving x/y alignment."""
    if len(xs) != len(ys) or len(ys) < 6:
        return xs, ys
    q1 = _percentile(ys, 25)
    q3 = _percentile(ys, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return xs, ys
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    filtered = [(x, y) for x, y in zip(xs, ys) if lo <= y <= hi]
    if len(filtered) < max(3, int(len(ys) * 0.6)):
        return xs, ys
    fx, fy = zip(*filtered)
    return list(fx), list(fy)


def _detect_mean_shift(
    points: list[tuple[float, float]],
    *,
    recent_hours: int = 24,
    baseline_hours: int = 24 * 7,
    min_recent_samples: int = 3,
    min_baseline_samples: int = 12,
    sigma_threshold: float = 2.0,
    min_abs_delta: float = 0.0,
) -> dict | None:
    """Detect abrupt shift between recent window and prior baseline window."""
    if len(points) < (min_recent_samples + min_baseline_samples):
        return None
    pts = sorted(points, key=lambda p: p[0])
    latest_ts = pts[-1][0]
    recent_start = latest_ts - recent_hours * 3600
    baseline_start = latest_ts - baseline_hours * 3600

    recent_vals = [v for ts, v in pts if ts >= recent_start]
    baseline_vals = [v for ts, v in pts if baseline_start <= ts < recent_start]
    if len(recent_vals) < min_recent_samples or len(baseline_vals) < min_baseline_samples:
        return None

    recent_avg = mean(recent_vals)
    baseline_avg = mean(baseline_vals)
    baseline_std = stdev(baseline_vals) if len(baseline_vals) > 1 else 0.0
    delta = recent_avg - baseline_avg
    dynamic_threshold = max(min_abs_delta, sigma_threshold * baseline_std)
    if abs(delta) <= dynamic_threshold:
        return None

    return {
        "recent_avg": recent_avg,
        "baseline_avg": baseline_avg,
        "delta": delta,
        "delta_pct": (delta / baseline_avg * 100) if baseline_avg else 0.0,
        "recent_samples": len(recent_vals),
        "baseline_samples": len(baseline_vals),
        "sigma_threshold": sigma_threshold,
    }


def _trend_arrow(slope: float, threshold: float = 0.0) -> str:
    """Return a Unicode arrow representing trend direction."""
    if abs(slope) < threshold:
        return "→"
    if slope > threshold * 3:
        return "↑"
    if slope > 0:
        return "↗"
    if slope < -threshold * 3:
        return "↓"
    return "↘"


def _ts_to_epoch(iso: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    return datetime.fromisoformat(iso).timestamp()


def _derive_peak_hours(hourly_speed: dict[int, list[float]]) -> list[int]:
    """Infer likely congestion hours from worst throughput bins."""
    means = {h: mean(vals) for h, vals in hourly_speed.items() if vals}
    if len(means) < 6:
        return [18, 19, 20, 21, 22]

    cutoff = _percentile(list(means.values()), 25)
    overall_avg = mean(list(means.values()))
    threshold = min(cutoff, overall_avg * 0.95)
    peak_hours = sorted(h for h, v in means.items() if v <= threshold)

    if len(peak_hours) < 3:
        peak_hours = sorted(means, key=means.get)[:3]
    if len(peak_hours) > 6:
        peak_hours = sorted(peak_hours, key=lambda h: means[h])[:6]
        peak_hours.sort()
    return peak_hours


# ---------------------------------------------------------------------------
# analyze_trends — linear regressions on key metrics
# ---------------------------------------------------------------------------

async def analyze_trends(store: DataStore, *, days: int = 30) -> dict:
    """Compute trend direction + slope for speed, latency, loss, WiFi, RAM."""
    result: dict = {}
    async def _speed_metric_rows(metric: str) -> list[dict]:
        if hasattr(store, "get_speed_metric_series"):
            return await store.get_speed_metric_series(days=days, metric=metric)  # type: ignore[attr-defined]
        rows = await store.get_speed_tests(days=days)
        return [
            {"timestamp": r["timestamp"], metric: r.get(metric), "quality": r.get("quality", "ok")}
            for r in rows if r.get(metric) is not None
        ]

    async def _latency_metric_rows(metric: str, *, target: str | None = None) -> list[dict]:
        if hasattr(store, "get_latency_metric_series"):
            return await store.get_latency_metric_series(days=days, metric=metric, target=target)  # type: ignore[attr-defined]
        rows = await store.get_latency_probes(days=days, target=target)
        return [
            {"timestamp": r["timestamp"], "target": r.get("target"), metric: r.get(metric)}
            for r in rows if r.get(metric) is not None
        ]

    async def _wifi_metric_rows(metric: str, *, band: str | None = None) -> list[dict]:
        if hasattr(store, "get_wifi_metric_series"):
            return await store.get_wifi_metric_series(days=days, metric=metric, band=band)  # type: ignore[attr-defined]
        rows = await store.get_wifi_snapshots(days=days, band=band)
        return [
            {"timestamp": r["timestamp"], "band": r.get("band"), metric: r.get(metric)}
            for r in rows if r.get(metric) is not None
        ]

    async def _system_metric_rows(metric: str) -> list[dict]:
        if hasattr(store, "get_system_metric_series"):
            return await store.get_system_metric_series(days=days, metric=metric)  # type: ignore[attr-defined]
        rows = await store.get_system_snapshots(days=days)
        return [
            {"timestamp": r["timestamp"], metric: r.get(metric)}
            for r in rows if r.get(metric) is not None
        ]

    # --- Speed trend ---
    dl_rows = await _speed_metric_rows("download_bps")
    ul_rows = await _speed_metric_rows("upload_bps")
    dl_pts = [
        (_ts_to_epoch(s["timestamp"]), s["download_bps"])
        for s in dl_rows
        if s.get("download_bps") and s.get("quality", "ok") != "invalid"
    ]
    ul_pts = [
        (_ts_to_epoch(s["timestamp"]), s["upload_bps"])
        for s in ul_rows
        if s.get("upload_bps") and s.get("quality", "ok") != "invalid"
    ]
    if len(dl_pts) >= 3:
        raw_xs, raw_ys = zip(*dl_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        mbps_per_week = slope * 604800 / 1_000_000
        arrow = _trend_arrow(mbps_per_week, 1.0)
        result["download"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_mbps_per_week": round(mbps_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_mbps": round(mean(ys) / 1_000_000, 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }
    if len(ul_pts) >= 3:
        raw_xs, raw_ys = zip(*ul_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        mbps_per_week = slope * 604800 / 1_000_000
        arrow = _trend_arrow(mbps_per_week, 0.5)
        result["upload"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_mbps_per_week": round(mbps_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_mbps": round(mean(ys) / 1_000_000, 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    # --- Latency trend ---
    lat_rows = await _latency_metric_rows("avg_ms", target="gateway")
    lat_pts = [
        (_ts_to_epoch(p["timestamp"]), p["avg_ms"])
        for p in lat_rows if p.get("avg_ms")
    ]
    if len(lat_pts) >= 3:
        raw_xs, raw_ys = zip(*lat_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        ms_per_week = slope * 604800
        arrow = _trend_arrow(ms_per_week, 0.5)
        result["latency"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_ms_per_week": round(ms_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_ms": round(mean(ys), 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    # --- Abrupt change detection (mean-shift) ---
    change_points: dict[str, dict] = {}
    dl_shift = _detect_mean_shift(dl_pts, min_abs_delta=20_000_000)
    if dl_shift:
        change_points["download"] = {
            "direction": "up" if dl_shift["delta"] > 0 else "down",
            "delta_mbps": round(dl_shift["delta"] / 1_000_000, 1),
            "delta_pct": round(dl_shift["delta_pct"], 1),
            "recent_avg_mbps": round(dl_shift["recent_avg"] / 1_000_000, 1),
            "baseline_avg_mbps": round(dl_shift["baseline_avg"] / 1_000_000, 1),
            "recent_samples": dl_shift["recent_samples"],
            "baseline_samples": dl_shift["baseline_samples"],
        }
    lat_shift = _detect_mean_shift(lat_pts, min_abs_delta=2.0)
    if lat_shift:
        change_points["latency"] = {
            "direction": "up" if lat_shift["delta"] > 0 else "down",
            "delta_ms": round(lat_shift["delta"], 2),
            "delta_pct": round(lat_shift["delta_pct"], 1),
            "recent_avg_ms": round(lat_shift["recent_avg"], 2),
            "baseline_avg_ms": round(lat_shift["baseline_avg"], 2),
            "recent_samples": lat_shift["recent_samples"],
            "baseline_samples": lat_shift["baseline_samples"],
        }
    if change_points:
        result["change_points"] = change_points

    # --- Packet loss frequency ---
    all_lat = await _latency_metric_rows("loss_pct")
    loss_events = [p for p in all_lat if (p.get("loss_pct") or 0) > 0]
    weeks = max(days / 7, 1)
    result["packet_loss"] = {
        "events": len(loss_events),
        "per_week": round(len(loss_events) / weeks, 1),
        "total_probes": len(all_lat),
    }

    # --- WiFi RSSI trend (per band) ---
    for band in ("2.4", "5"):
        band_rows = await _wifi_metric_rows("avg_rssi", band=band)
        pts = [
            (_ts_to_epoch(w["timestamp"]), w["avg_rssi"])
            for w in band_rows if w.get("avg_rssi")
        ]
        if len(pts) >= 3:
            raw_xs, raw_ys = zip(*pts)
            xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
            slope, _, r2 = _linreg(xs, ys)
            db_per_week = slope * 604800
            arrow = _trend_arrow(-db_per_week, 0.3)
            avg_rssi = round(mean(ys), 1)
            result[f"wifi_{band}"] = {
                "direction": arrow,  # negative slope = worse
                "arrow": arrow,
                "slope_db_per_week": round(db_per_week, 2),
                "r_squared": round(r2, 3),
                "avg_rssi": avg_rssi,
                "avg_db": avg_rssi,
                "samples": len(ys),
                "raw_samples": len(raw_ys),
                "outliers_removed": len(raw_ys) - len(ys),
            }

    # --- WiFi noise floor trend (per band) ---
    for band in ("2.4", "5"):
        band_rows = await _wifi_metric_rows("noise_floor", band=band)
        pts = [
            (_ts_to_epoch(w["timestamp"]), w["noise_floor"])
            for w in band_rows if w.get("noise_floor") is not None
        ]
        if len(pts) >= 3:
            raw_xs, raw_ys = zip(*pts)
            xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
            slope, _, r2 = _linreg(xs, ys)
            db_per_week = slope * 604800
            arrow = _trend_arrow(-db_per_week, 0.3)  # higher noise floor is worse
            result[f"noise_{band}"] = {
                "direction": arrow,
                "arrow": arrow,
                "slope_db_per_week": round(db_per_week, 2),
                "r_squared": round(r2, 3),
                "avg_dbm": round(mean(ys), 1),
                "samples": len(ys),
                "raw_samples": len(raw_ys),
                "outliers_removed": len(raw_ys) - len(ys),
            }

    # --- RAM trend (detect memory leaks) ---
    sys_rows = await _system_metric_rows("ram_pct")
    ram_pts = [
        (_ts_to_epoch(s["timestamp"]), s["ram_pct"])
        for s in sys_rows if s.get("ram_pct")
    ]
    if len(ram_pts) >= 3:
        raw_xs, raw_ys = zip(*ram_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        pct_per_week = slope * 604800
        arrow = _trend_arrow(pct_per_week, 0.5)
        result["ram"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_pct_per_week": round(pct_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_pct": round(mean(ys), 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    # --- Jitter trend ---
    jitter_rows = await _speed_metric_rows("jitter_ms")
    jitter_pts = [
        (_ts_to_epoch(s["timestamp"]), s["jitter_ms"])
        for s in jitter_rows
        if s.get("jitter_ms") is not None and s.get("quality", "ok") != "invalid"
    ]
    if len(jitter_pts) >= 3:
        raw_xs, raw_ys = zip(*jitter_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        ms_per_week = slope * 604800
        arrow = _trend_arrow(-ms_per_week, 0.2)  # rising jitter is worse
        result["jitter"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_ms_per_week": round(ms_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_ms": round(mean(ys), 2),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    # --- Temperature trend ---
    temp_rows = await _system_metric_rows("temp_c")
    temp_pts = [
        (_ts_to_epoch(s["timestamp"]), s["temp_c"])
        for s in temp_rows if s.get("temp_c") is not None
    ]
    if len(temp_pts) >= 3:
        raw_xs, raw_ys = zip(*temp_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        c_per_week = slope * 604800
        arrow = _trend_arrow(-c_per_week, 0.5)  # hotter over time is worse
        result["temperature"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_c_per_week": round(c_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_c": round(mean(ys), 1),
            "p95_c": round(_percentile(list(ys), 95), 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    # --- Conntrack utilization trend ---
    conn_count_rows = await _system_metric_rows("conntrack_count")
    conn_max_rows = await _system_metric_rows("conntrack_max")
    max_by_ts = {
        r["timestamp"]: r["conntrack_max"]
        for r in conn_max_rows
        if r.get("conntrack_max") and r["conntrack_max"] > 0
    }
    util_pts = []
    for r in conn_count_rows:
        ts = r["timestamp"]
        cmax = max_by_ts.get(ts)
        ccount = r.get("conntrack_count")
        if cmax and ccount is not None:
            util_pts.append((_ts_to_epoch(ts), (ccount / cmax) * 100))
    if len(util_pts) >= 3:
        raw_xs, raw_ys = zip(*util_pts)
        xs, ys = _iqr_filter(list(raw_xs), list(raw_ys))
        slope, _, r2 = _linreg(xs, ys)
        pct_per_week = slope * 604800
        arrow = _trend_arrow(-pct_per_week, 0.5)  # rising utilization is worse
        result["conntrack_utilization"] = {
            "direction": arrow,
            "arrow": arrow,
            "slope_pct_per_week": round(pct_per_week, 2),
            "r_squared": round(r2, 3),
            "avg_pct": round(mean(ys), 1),
            "peak_pct": round(max(ys), 1),
            "samples": len(ys),
            "raw_samples": len(raw_ys),
            "outliers_removed": len(raw_ys) - len(ys),
        }

    return result


# ---------------------------------------------------------------------------
# analyze_patterns — time-of-day / day-of-week performance maps
# ---------------------------------------------------------------------------

async def analyze_patterns(store: DataStore, *, days: int = 30) -> dict:
    """Hourly/daily performance heatmaps and peak degradation analysis."""
    result: dict = {}

    # --- Hourly speed heatmap ---
    if hasattr(store, "get_speed_metric_series"):
        speed_rows = await store.get_speed_metric_series(days=days, metric="download_bps")  # type: ignore[attr-defined]
    else:
        speed_rows = await store.get_speed_tests(days=days)
    hourly_dl: dict[int, list[float]] = {h: [] for h in range(24)}
    for s in speed_rows:
        if not s.get("download_bps"):
            continue
        try:
            hour = datetime.fromisoformat(s["timestamp"]).hour
            hourly_dl[hour].append(s["download_bps"] / 1_000_000)
        except (ValueError, KeyError):
            continue

    heatmap = {}
    for h in range(24):
        vals = hourly_dl[h]
        if vals:
            heatmap[f"{h:02d}:00"] = {"avg_mbps": round(mean(vals), 1), "count": len(vals)}
    result["hourly_speed"] = heatmap

    # --- Hourly latency heatmap ---
    if hasattr(store, "get_latency_metric_series"):
        lat_rows = await store.get_latency_metric_series(  # type: ignore[attr-defined]
            days=days,
            metric="avg_ms",
            target="gateway",
        )
    else:
        lat_rows = await store.get_latency_probes(days=days, target="gateway")
    hourly_lat: dict[int, list[float]] = {h: [] for h in range(24)}
    for p in lat_rows:
        if not p.get("avg_ms"):
            continue
        try:
            hour = datetime.fromisoformat(p["timestamp"]).hour
            hourly_lat[hour].append(p["avg_ms"])
        except (ValueError, KeyError):
            continue

    lat_heatmap = {}
    for h in range(24):
        vals = hourly_lat[h]
        if vals:
            lat_heatmap[f"{h:02d}:00"] = {"avg_ms": round(mean(vals), 1), "count": len(vals)}
    result["hourly_latency"] = lat_heatmap

    # --- Day-of-week patterns ---
    dow_dl: dict[int, list[float]] = {d: [] for d in range(7)}
    for s in speed_rows:
        if not s.get("download_bps"):
            continue
        try:
            dow = datetime.fromisoformat(s["timestamp"]).weekday()
            dow_dl[dow].append(s["download_bps"] / 1_000_000)
        except (ValueError, KeyError):
            continue

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_map = {}
    for d in range(7):
        vals = dow_dl[d]
        if vals:
            dow_map[day_names[d]] = {"avg_mbps": round(mean(vals), 1), "count": len(vals)}
    result["day_of_week_speed"] = dow_map

    # --- Best/worst hours ---
    all_hourly_vals = {h: mean(v) for h, v in hourly_dl.items() if v}
    if all_hourly_vals:
        best_h = max(all_hourly_vals, key=all_hourly_vals.get)  # type: ignore[arg-type]
        worst_h = min(all_hourly_vals, key=all_hourly_vals.get)  # type: ignore[arg-type]
        result["best_hour"] = {
            "hour": f"{best_h:02d}:00",
            "avg_mbps": round(all_hourly_vals[best_h], 1),
        }
        result["worst_hour"] = {
            "hour": f"{worst_h:02d}:00",
            "avg_mbps": round(all_hourly_vals[worst_h], 1),
        }

    # --- Peak degradation (data-driven peak window) ---
    peak_hours = _derive_peak_hours(hourly_dl)
    result["peak_hours"] = peak_hours
    if peak_hours:
        result["peak_window_label"] = ",".join(f"{h:02d}:00" for h in peak_hours)
    peak_vals = []
    offpeak_vals = []
    for s in speed_rows:
        if not s.get("download_bps"):
            continue
        try:
            hour = datetime.fromisoformat(s["timestamp"]).hour
        except (ValueError, KeyError):
            continue
        if hour in peak_hours:
            peak_vals.append(s["download_bps"] / 1_000_000)
        else:
            offpeak_vals.append(s["download_bps"] / 1_000_000)

    if peak_vals and offpeak_vals:
        peak_avg = mean(peak_vals)
        offpeak_avg = mean(offpeak_vals)
        delta_pct = ((peak_avg - offpeak_avg) / offpeak_avg) * 100
        result["peak_degradation"] = {
            "peak_avg_mbps": round(peak_avg, 1),
            "offpeak_avg_mbps": round(offpeak_avg, 1),
            "delta_pct": round(delta_pct, 1),
            "peak_samples": len(peak_vals),
            "offpeak_samples": len(offpeak_vals),
        }

    return result


# ---------------------------------------------------------------------------
# analyze_isp_sla — plan compliance tracking
# ---------------------------------------------------------------------------

async def analyze_isp_sla(store: DataStore, *, days: int = 30) -> dict:
    """Track ISP delivery against plan speeds (300/35 Mbps)."""
    speed_rows = await store.get_speed_tests(days=days)
    valid = [s for s in speed_rows if s.get("download_bps") and s.get("upload_bps")]

    if not valid:
        return {"tests": 0, "message": "No valid speed tests in period"}

    dl_vals = [s["download_bps"] for s in valid]
    ul_vals = [s["upload_bps"] for s in valid]

    dl_meeting = sum(1 for v in dl_vals if v >= PLAN_SPEED_DOWN * 0.90)
    ul_meeting = sum(1 for v in ul_vals if v >= PLAN_SPEED_UP * 0.90)

    result = {
        "tests": len(valid),
        "download": {
            "plan_mbps": PLAN_SPEED_DOWN / 1_000_000,
            "avg_mbps": round(mean(dl_vals) / 1_000_000, 1),
            "min_mbps": round(min(dl_vals) / 1_000_000, 1),
            "max_mbps": round(max(dl_vals) / 1_000_000, 1),
            "pct_meeting_plan": round(dl_meeting / len(valid) * 100, 1),
            "stddev_mbps": round(stdev(dl_vals) / 1_000_000, 1) if len(dl_vals) > 1 else 0,
        },
        "upload": {
            "plan_mbps": PLAN_SPEED_UP / 1_000_000,
            "avg_mbps": round(mean(ul_vals) / 1_000_000, 1),
            "min_mbps": round(min(ul_vals) / 1_000_000, 1),
            "max_mbps": round(max(ul_vals) / 1_000_000, 1),
            "pct_meeting_plan": round(ul_meeting / len(valid) * 100, 1),
        },
    }

    # Congestion windows — hours where avg download < 90% plan
    hourly: dict[int, list[float]] = {}
    for s in valid:
        try:
            hour = datetime.fromisoformat(s["timestamp"]).hour
            hourly.setdefault(hour, []).append(s["download_bps"])
        except (ValueError, KeyError):
            continue

    congestion_hours = []
    for h, vals in sorted(hourly.items()):
        avg = mean(vals)
        if avg < PLAN_SPEED_DOWN * 0.90:
            congestion_hours.append({
                "hour": f"{h:02d}:00",
                "avg_mbps": round(avg / 1_000_000, 1),
                "pct_of_plan": round(avg / PLAN_SPEED_DOWN * 100, 1),
            })
    result["congestion_hours"] = congestion_hours

    # SLA compliance score (0-100)
    dl_ratio = min(1.0, mean(dl_vals) / PLAN_SPEED_DOWN)
    ul_ratio = min(1.0, mean(ul_vals) / PLAN_SPEED_UP)
    consistency = dl_meeting / len(valid) if valid else 0
    result["sla_score"] = round(dl_ratio * 40 + ul_ratio * 20 + consistency * 40, 1)

    return result
