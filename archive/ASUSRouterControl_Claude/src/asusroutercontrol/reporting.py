"""Weekly report generator — aggregates monitoring data into actionable insights."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from statistics import mean

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asusroutercontrol._time import utcnow
from asusroutercontrol.config import load_config
from asusroutercontrol.datastore import DataStore

log = logging.getLogger(__name__)

PLAN_SPEED_DOWN = 300_000_000  # 300 Mbps Spectrum plan
PLAN_SPEED_UP = 35_000_000     # 35 Mbps


def _percentile(data: list[float], p: float) -> float:
    """Simple percentile calc (0-100 scale)."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = min(int(len(s) * p / 100 + 0.5), len(s) - 1)
    return s[idx]


def _fmt_mbps(bps: float | None) -> str:
    if bps is None:
        return "N/A"
    return f"{bps / 1_000_000:.1f} Mbps"


def _fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "N/A"
    return f"{ms:.1f} ms"


async def generate_report(store: DataStore, *, days: int = 7) -> dict:
    """Generate a structured report dict from `days` of data."""
    cfg = load_config()
    report: dict = {"period_days": days, "generated_at": utcnow().isoformat()}

    # Fetch all data
    speed_tests = await store.get_speed_tests(days=days)
    latency = await store.get_latency_probes(days=days)
    sys_snaps = await store.get_system_snapshots(days=days)
    wifi_snaps = await store.get_wifi_snapshots(days=days)
    devices = await store.get_all_devices()

    # --- 1. Summary + Health Score ---
    report["summary"] = _build_summary(speed_tests, latency, sys_snaps, wifi_snaps, devices)

    # --- 2. Bandwidth Trends ---
    report["bandwidth"] = _build_bandwidth(speed_tests, cfg)

    # --- 3. Peak vs Off-Peak ---
    report["peak_analysis"] = _build_peak_analysis(speed_tests, latency, cfg)

    # --- 4. Latency Health ---
    report["latency"] = _build_latency(latency)

    # --- 5. Device Inventory ---
    report["devices"] = _build_devices(devices, days)

    # --- 6. WiFi Quality ---
    report["wifi"] = _build_wifi(wifi_snaps)

    # --- 7. System Health ---
    report["system"] = _build_system(sys_snaps)

    # --- 8. Anomalies ---
    report["anomalies"] = _build_anomalies(speed_tests, latency, sys_snaps)

    # --- 9. Provider Comparison ---
    report["provider_comparison"] = _build_provider_comparison(speed_tests)

    # --- 10. Trends + SLA + Config Impact ---
    try:
        from asusroutercontrol.analyzer import analyze_isp_sla, analyze_trends
        from asusroutercontrol.optimizer import correlate_config_performance
        report["trends"] = await analyze_trends(store, days=days)
        report["sla"] = await analyze_isp_sla(store, days=days)
        report["config_impact"] = await correlate_config_performance(store, days=days)
    except Exception:
        log.warning("Failed to generate trends/SLA/config-impact sections")
        report["trends"] = {}
        report["sla"] = {}
        report["config_impact"] = []

    # --- 11. Recommendations ---
    report["recommendations"] = _build_recommendations(report)

    return report


# --- Section Builders ---


def _build_summary(speed_tests, latency, sys_snaps, wifi_snaps, devices) -> dict:
    dl_speeds = [s["download_bps"] for s in speed_tests if s.get("download_bps")]
    gw_latency = [p["avg_ms"] for p in latency if p.get("target") == "gateway" and p.get("avg_ms")]
    temps = [s["temp_c"] for s in sys_snaps if s.get("temp_c")]
    loss_vals = [p["loss_pct"] for p in latency if p.get("loss_pct") is not None]
    rssi_vals = [w["avg_rssi"] for w in wifi_snaps if w.get("avg_rssi")]

    # Uptime: check for gaps in system snapshots (30min intervals expected)
    uptime_pct = 100.0
    if len(sys_snaps) >= 2:
        expected = (7 * 24 * 60) / 30  # expected samples in 7 days at 30min intervals
        uptime_pct = min(100.0, len(sys_snaps) / expected * 100)

    health = _calculate_health_score(
        uptime_pct=uptime_pct,
        avg_download=mean(dl_speeds) if dl_speeds else None,
        gw_p95=_percentile(gw_latency, 95) if gw_latency else None,
        avg_loss=mean(loss_vals) if loss_vals else None,
        p95_temp=_percentile(temps, 95) if temps else None,
        avg_rssi=mean(rssi_vals) if rssi_vals else None,
    )

    return {
        "health_score": round(health, 1),
        "speed_tests_count": len(speed_tests),
        "avg_download": _fmt_mbps(mean(dl_speeds)) if dl_speeds else "N/A",
        "avg_upload": _fmt_mbps(
            mean([s["upload_bps"] for s in speed_tests if s.get("upload_bps")])
        ) if any(s.get("upload_bps") for s in speed_tests) else "N/A",
        "device_count": len(devices),
        "uptime_pct": round(uptime_pct, 1),
    }


def _calculate_health_score(
    *,
    uptime_pct: float | None,
    avg_download: float | None,
    gw_p95: float | None,
    avg_loss: float | None,
    p95_temp: float | None,
    avg_rssi: float | None,
) -> float:
    """Weighted composite health score (0-100)."""
    score = 0.0

    # Uptime: 20%
    if uptime_pct is not None:
        score += 20 * min(1.0, uptime_pct / 99.9)

    # Speed consistency: 25%
    if avg_download is not None:
        ratio = min(1.0, avg_download / PLAN_SPEED_DOWN)
        score += 25 * ratio

    # Latency: 20%
    if gw_p95 is not None:
        if gw_p95 <= 10:
            score += 20
        elif gw_p95 <= 15:
            score += 20 * (1.0 - (gw_p95 - 10) / 10)
        elif gw_p95 <= 30:
            score += 20 * 0.3 * (1.0 - (gw_p95 - 15) / 15)

    # Packet loss: 15%
    if avg_loss is not None:
        if avg_loss == 0:
            score += 15
        elif avg_loss <= 1:
            score += 15 * (1.0 - avg_loss)

    # Temperature: 10%
    if p95_temp is not None:
        if p95_temp <= 80:
            score += 10
        elif p95_temp <= 90:
            score += 10 * (1.0 - (p95_temp - 80) / 10)

    # WiFi signal: 10%
    if avg_rssi is not None:
        if avg_rssi >= -50:
            score += 10
        elif avg_rssi >= -70:
            score += 10 * (1.0 - (abs(avg_rssi) - 50) / 20)

    return score


def _build_provider_comparison(speed_tests) -> dict:
    """Aggregate speed test results by provider source."""
    by_source: dict[str, list[dict]] = {}
    for st in speed_tests:
        src = st.get("source", "ookla")
        by_source.setdefault(src, []).append(st)

    result: dict = {}
    for src, tests in sorted(by_source.items()):
        dl = [t["download_bps"] for t in tests if t.get("download_bps")]
        ul = [t["upload_bps"] for t in tests if t.get("upload_bps")]
        pings = [t["ping_ms"] for t in tests if t.get("ping_ms")]
        result[src] = {
            "tests": len(tests),
            "avg_download": _fmt_mbps(mean(dl)) if dl else "N/A",
            "avg_upload": _fmt_mbps(mean(ul)) if ul else "N/A",
            "avg_ping": _fmt_ms(mean(pings)) if pings else "N/A",
        }
    return result


def _build_bandwidth(speed_tests, cfg) -> dict:
    if not speed_tests:
        return {"tests": 0}

    by_slot: dict[int, list[dict]] = {}
    for st in speed_tests:
        if not st.get("download_bps"):
            continue
        try:
            ts = datetime.fromisoformat(st["timestamp"])
            hour = ts.hour
        except (ValueError, KeyError):
            continue
        # Bucket to nearest configured slot
        closest = min(cfg.speedtest_times, key=lambda h: abs(h - hour))
        by_slot.setdefault(closest, []).append(st)

    slots = {}
    for hour, tests in sorted(by_slot.items()):
        dl = [t["download_bps"] for t in tests if t.get("download_bps")]
        ul = [t["upload_bps"] for t in tests if t.get("upload_bps")]
        slots[f"{hour:02d}:00"] = {
            "count": len(tests),
            "avg_down": _fmt_mbps(mean(dl)) if dl else "N/A",
            "min_down": _fmt_mbps(min(dl)) if dl else "N/A",
            "max_down": _fmt_mbps(max(dl)) if dl else "N/A",
            "avg_up": _fmt_mbps(mean(ul)) if ul else "N/A",
        }

    all_dl = [s["download_bps"] for s in speed_tests if s.get("download_bps")]
    return {
        "tests": len(speed_tests),
        "overall_avg_down": _fmt_mbps(mean(all_dl)) if all_dl else "N/A",
        "by_time_slot": slots,
    }


def _build_peak_analysis(speed_tests, latency, cfg) -> dict:
    peak_dl = [s["download_bps"] for s in speed_tests if s.get("is_peak") and s.get("download_bps")]
    offpeak_dl = [
        s["download_bps"] for s in speed_tests if not s.get("is_peak") and s.get("download_bps")
    ]
    peak_lat = [
        p["avg_ms"] for p in latency
        if p.get("target") == "gateway" and p.get("avg_ms") and _is_peak_ts(p, cfg)
    ]
    offpeak_lat = [
        p["avg_ms"] for p in latency
        if p.get("target") == "gateway" and p.get("avg_ms") and not _is_peak_ts(p, cfg)
    ]

    result: dict = {}
    if peak_dl and offpeak_dl:
        delta_pct = ((mean(peak_dl) - mean(offpeak_dl)) / mean(offpeak_dl)) * 100
        result["speed_delta_pct"] = round(delta_pct, 1)
    result["peak_avg_download"] = _fmt_mbps(mean(peak_dl)) if peak_dl else "N/A"
    result["offpeak_avg_download"] = _fmt_mbps(mean(offpeak_dl)) if offpeak_dl else "N/A"
    result["peak_avg_latency"] = _fmt_ms(mean(peak_lat)) if peak_lat else "N/A"
    result["offpeak_avg_latency"] = _fmt_ms(mean(offpeak_lat)) if offpeak_lat else "N/A"
    return result


def _is_peak_ts(row: dict, cfg) -> bool:
    try:
        hour = datetime.fromisoformat(row["timestamp"]).hour
    except (ValueError, KeyError):
        return False
    if cfg.peak_start <= cfg.peak_end:
        return cfg.peak_start <= hour < cfg.peak_end
    return hour >= cfg.peak_start or hour < cfg.peak_end


def _build_latency(latency) -> dict:
    result: dict = {}
    for target in ("gateway", "cloudflare", "google"):
        avgs = [p["avg_ms"] for p in latency if p.get("target") == target and p.get("avg_ms")]
        losses = [p["loss_pct"] for p in latency if p.get("target") == target and p.get("loss_pct")]
        if avgs:
            result[target] = {
                "p50": _fmt_ms(_percentile(avgs, 50)),
                "p95": _fmt_ms(_percentile(avgs, 95)),
                "p99": _fmt_ms(_percentile(avgs, 99)),
                "loss_events": sum(1 for v in losses if v > 0),
                "samples": len(avgs),
            }
    return result


def _build_devices(devices, days) -> dict:
    known = [d for d in devices if d.get("is_known")]
    unknown = [d for d in devices if not d.get("is_known")]
    return {
        "total_unique": len(devices),
        "known": len(known),
        "unknown_phantoms": len(unknown),
        "real_ratio": f"{len(known)}/{len(devices)}" if devices else "0/0",
    }


def _build_wifi(wifi_snaps) -> dict:
    result: dict = {}
    for band in ("2.4", "5"):
        snaps = [w for w in wifi_snaps if w.get("band") == band]
        clients = [w["client_count"] for w in snaps if w.get("client_count") is not None]
        rssi = [w["avg_rssi"] for w in snaps if w.get("avg_rssi") is not None]
        noise = [w["noise_floor"] for w in snaps if w.get("noise_floor") is not None]
        if snaps:
            result[band] = {
                "samples": len(snaps),
                "avg_clients": round(mean(clients), 1) if clients else 0,
                "avg_rssi": round(mean(rssi), 1) if rssi else None,
                "min_rssi": round(min(rssi), 1) if rssi else None,
                "avg_noise": round(mean(noise), 1) if noise else None,
            }
    return result


def _build_system(sys_snaps) -> dict:
    cpu = [s["cpu_pct"] for s in sys_snaps if s.get("cpu_pct") is not None]
    ram = [s["ram_pct"] for s in sys_snaps if s.get("ram_pct") is not None]
    temp = [s["temp_c"] for s in sys_snaps if s.get("temp_c") is not None]
    ct = [s["conntrack_count"] for s in sys_snaps if s.get("conntrack_count") is not None]
    ct_max = [s["conntrack_max"] for s in sys_snaps if s.get("conntrack_max") is not None]

    result: dict = {"samples": len(sys_snaps)}
    if cpu:
        result["cpu"] = {
            "avg": round(mean(cpu), 1), "p95": round(_percentile(cpu, 95), 1),
            "max": round(max(cpu), 1),
        }
    if ram:
        result["ram"] = {"avg": round(mean(ram), 1), "max": round(max(ram), 1)}
    if temp:
        result["temp"] = {
            "avg": round(mean(temp), 1), "p95": round(_percentile(temp, 95), 1),
            "max": round(max(temp), 1),
            "warnings": sum(1 for t in temp if t > 85),
        }
    if ct and ct_max:
        result["conntrack"] = {
            "avg": round(mean(ct)),
            "peak": max(ct),
            "max_allowed": ct_max[0] if ct_max else None,
            "peak_pct": round(max(ct) / ct_max[0] * 100, 2) if ct_max and ct_max[0] else None,
        }
    return result


def _build_anomalies(speed_tests, latency, sys_snaps) -> list[str]:
    anomalies: list[str] = []

    # Speed test failures
    failures = [s for s in speed_tests if s.get("error")]
    if failures:
        anomalies.append(f"{len(failures)} speed test failure(s)")

    # Speed drops >15% below plan
    for st in speed_tests:
        dl = st.get("download_bps")
        if dl and dl < PLAN_SPEED_DOWN * 0.70:
            ts = st.get("timestamp", "?")
            anomalies.append(f"Speed drop to {_fmt_mbps(dl)} at {ts}")

    # Latency spikes
    gw = [p for p in latency if p.get("target") == "gateway" and p.get("avg_ms")]
    if gw:
        p95 = _percentile([p["avg_ms"] for p in gw], 95)
        spikes = [p for p in gw if (p.get("avg_ms") or 0) > p95 * 2]
        if spikes:
            anomalies.append(f"{len(spikes)} latency spike(s) >2x p95 ({_fmt_ms(p95)})")

    # Packet loss
    loss_events = [p for p in latency if (p.get("loss_pct") or 0) > 0]
    if loss_events:
        anomalies.append(f"{len(loss_events)} packet loss event(s)")

    # Temperature
    for s in sys_snaps:
        if (s.get("temp_c") or 0) > 90:
            anomalies.append(f"Temperature {s['temp_c']}°C at {s.get('timestamp', '?')}")
            break  # Report once

    return anomalies


def _build_recommendations(report: dict) -> list[str]:
    recs: list[str] = []

    # Peak degradation
    peak = report.get("peak_analysis", {})
    delta = peak.get("speed_delta_pct")
    if delta is not None and delta < -10:
        recs.append(
            f"Peak download {abs(delta):.0f}% lower than off-peak → possible ISP congestion"
        )

    # Temperature
    sys_data = report.get("system", {})
    temp = sys_data.get("temp", {})
    if temp.get("p95") and temp["p95"] > 85:
        recs.append(f"CPU temp p95 = {temp['p95']}°C → check ventilation")

    # WiFi signal
    wifi = report.get("wifi", {})
    for band, data in wifi.items():
        if data.get("min_rssi") and data["min_rssi"] < -75:
            recs.append(
                f"{band}GHz worst signal {data['min_rssi']} dBm → device may need band change"
            )

    # Device churn
    dev = report.get("devices", {})
    phantoms = dev.get("unknown_phantoms", 0)
    if phantoms > 20:
        recs.append(f"{phantoms} unknown MACs → MAC randomization churn (normal for iOS/Android)")

    # Packet loss
    lat = report.get("latency", {})
    gw = lat.get("gateway", {})
    if gw.get("loss_events", 0) > 5:
        recs.append(
            f"{gw['loss_events']} packet loss events → call Spectrum for signal level check"
        )

    if not recs:
        recs.append("No issues detected — network health is good")

    return recs


# --- Rich Console Output ---


def print_report(report: dict, console: Console | None = None) -> None:
    """Render the report using Rich tables."""
    c = console or Console()

    # Header
    summary = report.get("summary", {})
    score = summary.get("health_score", 0)
    score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
    c.print(Panel(
        f"[bold {score_color}]Health Score: {score}/100[/bold {score_color}]  |  "
        f"Tests: {summary.get('speed_tests_count', 0)}  |  "
        f"Uptime: {summary.get('uptime_pct', 0)}%  |  "
        f"Devices: {summary.get('device_count', 0)}",
        title=f"[bold]Network Report — {report.get('period_days', 7)} Days[/bold]",
    ))

    # Provider Comparison
    prov = report.get("provider_comparison", {})
    if len(prov) > 1:
        t = Table(title="Speed Test Provider Comparison")
        t.add_column("Source", style="bold")
        t.add_column("Tests", justify="right")
        t.add_column("Avg Down")
        t.add_column("Avg Up")
        t.add_column("Avg Ping")
        for src, data in prov.items():
            t.add_row(
                src, str(data["tests"]),
                data["avg_download"], data["avg_upload"],
                data["avg_ping"],
            )
        c.print(t)

    # Bandwidth
    bw = report.get("bandwidth", {})
    if bw.get("by_time_slot"):
        t = Table(title="Bandwidth by Time Slot")
        t.add_column("Slot")
        t.add_column("Tests", justify="right")
        t.add_column("Avg Down")
        t.add_column("Min Down")
        t.add_column("Max Down")
        t.add_column("Avg Up")
        for slot, data in bw["by_time_slot"].items():
            t.add_row(slot, str(data["count"]), data["avg_down"],
                       data["min_down"], data["max_down"], data["avg_up"])
        c.print(t)

    # Peak vs Off-Peak
    peak = report.get("peak_analysis", {})
    if peak.get("peak_avg_download") != "N/A":
        t = Table(title="Peak vs Off-Peak")
        t.add_column("Metric")
        t.add_column("Peak (18-23)")
        t.add_column("Off-Peak")
        t.add_column("Delta")
        delta_str = f"{peak.get('speed_delta_pct', 0):+.1f}%" if "speed_delta_pct" in peak else "—"
        t.add_row("Download", peak["peak_avg_download"], peak["offpeak_avg_download"], delta_str)
        t.add_row(
            "Latency", peak.get("peak_avg_latency", "—"),
            peak.get("offpeak_avg_latency", "—"), "",
        )
        c.print(t)

    # Latency
    lat = report.get("latency", {})
    if lat:
        t = Table(title="Latency Health")
        t.add_column("Target")
        t.add_column("p50")
        t.add_column("p95")
        t.add_column("p99")
        t.add_column("Loss Events", justify="right")
        for target, data in lat.items():
            t.add_row(target, data["p50"], data["p95"], data["p99"], str(data["loss_events"]))
        c.print(t)

    # System Health
    sys_data = report.get("system", {})
    if sys_data.get("cpu"):
        t = Table(title="System Health")
        t.add_column("Metric")
        t.add_column("Avg")
        t.add_column("p95")
        t.add_column("Max")
        cpu = sys_data["cpu"]
        t.add_row("CPU %", f"{cpu['avg']}%", f"{cpu['p95']}%", f"{cpu['max']}%")
        if sys_data.get("ram"):
            ram = sys_data["ram"]
            t.add_row("RAM %", f"{ram['avg']}%", "—", f"{ram['max']}%")
        if sys_data.get("temp"):
            temp = sys_data["temp"]
            color = "red" if temp["p95"] > 85 else ""
            t.add_row(
                "Temp °C",
                f"[{color}]{temp['avg']}[/{color}]" if color else str(temp["avg"]),
                f"[{color}]{temp['p95']}[/{color}]" if color else str(temp["p95"]),
                f"[{color}]{temp['max']}[/{color}]" if color else str(temp["max"]),
            )
        c.print(t)

    # WiFi
    wifi = report.get("wifi", {})
    if wifi:
        t = Table(title="WiFi Quality")
        t.add_column("Band")
        t.add_column("Avg Clients")
        t.add_column("Avg RSSI")
        t.add_column("Worst RSSI")
        t.add_column("Noise Floor")
        for band, data in wifi.items():
            t.add_row(
                f"{band} GHz",
                str(data.get("avg_clients", "—")),
                f"{data['avg_rssi']} dBm" if data.get("avg_rssi") else "—",
                f"{data['min_rssi']} dBm" if data.get("min_rssi") else "—",
                f"{data['avg_noise']} dBm" if data.get("avg_noise") else "—",
            )
        c.print(t)

    # Trends
    trends = report.get("trends", {})
    if trends:
        t = Table(title="Performance Trends")
        t.add_column("Metric", style="bold cyan")
        t.add_column("Avg")
        t.add_column("Trend")
        t.add_column("Rate")
        dl = trends.get("download", {})
        if dl:
            t.add_row(
                "Download", f"{dl.get('avg_mbps', 0):.0f} Mbps",
                dl.get("arrow", ""), f"{dl.get('slope_mbps_per_week', 0):+.1f} Mbps/wk",
            )
        ul = trends.get("upload", {})
        if ul:
            t.add_row(
                "Upload", f"{ul.get('avg_mbps', 0):.0f} Mbps",
                ul.get("arrow", ""), f"{ul.get('slope_mbps_per_week', 0):+.1f} Mbps/wk",
            )
        lat_t = trends.get("latency", {})
        if lat_t:
            t.add_row(
                "Latency", f"{lat_t.get('avg_ms', 0):.1f} ms",
                lat_t.get("arrow", ""), f"{lat_t.get('slope_ms_per_week', 0):+.2f} ms/wk",
            )
        ram_t = trends.get("ram", {})
        if ram_t:
            t.add_row(
                "RAM", f"{ram_t.get('avg_pct', 0):.0f}%",
                ram_t.get("arrow", ""), f"{ram_t.get('slope_pct_per_week', 0):+.1f}%/wk",
            )
        c.print(t)

    # SLA
    sla = report.get("sla", {})
    if sla.get("tests", 0) >= 1:
        sla_score = sla.get("sla_score", 0)
        sla_color = "green" if sla_score >= 80 else "yellow" if sla_score >= 60 else "red"
        dl_sla = sla.get("download", {})
        sla_text = f"[bold {sla_color}]SLA Score: {sla_score:.0f}/100[/bold {sla_color}]"
        if dl_sla:
            sla_text += (
                f"  |  Avg: {dl_sla.get('avg_mbps', 0):.0f} Mbps"
                f"  |  Meeting plan: {dl_sla.get('pct_meeting_plan', 0):.0f}%"
            )
        c.print(Panel(sla_text, title="ISP SLA"))

    # Config Impact
    config_impact = report.get("config_impact", [])
    if config_impact:
        t = Table(title="Config Change Impact")
        t.add_column("Time")
        t.add_column("Description")
        t.add_column("DL Impact")
        t.add_column("Latency Impact")
        for ci in config_impact[:10]:
            dl_v = ci.get("download_delta_pct")
            lat_v = ci.get("latency_delta_ms")
            dl_str = f"{dl_v:+.1f}%" if dl_v is not None else "—"
            lat_str = f"{lat_v:+.1f}ms" if lat_v is not None else "—"
            t.add_row(
                ci.get("timestamp", "")[:16],
                ci.get("description", "")[:50],
                dl_str,
                lat_str,
            )
        c.print(t)

    # Anomalies
    anomalies = report.get("anomalies", [])
    if anomalies:
        c.print(Panel("\n".join(f"[yellow]⚠[/yellow] {a}" for a in anomalies), title="Anomalies"))

    # Recommendations
    recs = report.get("recommendations", [])
    if recs:
        c.print(Panel("\n".join(f"→ {r}" for r in recs), title="Recommendations"))


def export_report_json(report: dict, path: Path) -> None:
    """Write report dict as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Report exported to %s", path)
