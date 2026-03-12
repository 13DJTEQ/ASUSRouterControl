"""Router optimization engine — correlates config changes with performance.

Analyzes historical data to generate evidence-based recommendations
and specific NVRAM setting suggestions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from statistics import mean

from asusroutercontrol.analyzer import analyze_isp_sla, analyze_patterns, analyze_trends
from asusroutercontrol.datastore import DataStore

log = logging.getLogger(__name__)

PLAN_SPEED_DOWN = 300_000_000
PLAN_SPEED_UP = 35_000_000
MIN_TREND_SAMPLES = 10
R2_MEDIUM = 0.5
R2_HIGH = 0.7
DOWNLOAD_DELTA_VERDICT_THRESHOLD_PCT = 5.0
LATENCY_DELTA_VERDICT_THRESHOLD_MS = 1.0
DOWNLOAD_TREND_DOWN_MBPS_PER_WEEK = 3.0
LATENCY_TREND_UP_MS_PER_WEEK = 1.0
RAM_TREND_UP_PCT_PER_WEEK = 1.0
RAM_STABLE_EPSILON_PCT_PER_WEEK = 0.3
PEAK_DEGRADATION_PCT = -10.0
PEAK_DEGRADATION_MIN_SAMPLES = 3
SLA_MIN_COMPLIANCE_PCT = 80.0
PACKET_LOSS_EVENTS_PER_WEEK = 5.0
JITTER_TREND_UP_MS_PER_WEEK = 0.8
TEMP_P95_RISK_C = 82.0
TEMP_TREND_UP_C_PER_WEEK = 1.0
CONNTRACK_PEAK_RISK_PCT = 80.0
CONNTRACK_PEAK_HIGH_CONFIDENCE_PCT = 85.0
CONNTRACK_TREND_UP_PCT_PER_WEEK = 2.0
WIFI_SIGNAL_DOWN_DB_PER_WEEK = 1.0
NOISE_FLOOR_UP_DB_PER_WEEK = 1.0
WIFI_SUPPORTING_SLOPE_DB_PER_WEEK = -0.5
WIFI_WEAK_RSSI_DBM = -67.0


# ---------------------------------------------------------------------------
# correlate_config_performance
# ---------------------------------------------------------------------------

async def correlate_config_performance(
    store: DataStore, *, days: int = 90
) -> list[dict]:
    """For each config event, compute before/after performance deltas.

    Uses 24-hour windows before and after each event to compare
    avg download, avg latency, avg packet loss, and avg RAM.
    """
    events = await store.get_config_events(days=days)
    if not events:
        return []


    results: list[dict] = []
    for ev in events:
        try:
            ev_ts = datetime.fromisoformat(ev["timestamp"])
        except (ValueError, KeyError):
            continue

        before_start = (ev_ts - timedelta(hours=24)).isoformat()
        before_end = ev_ts.isoformat()
        after_start = ev_ts.isoformat()
        after_end = (ev_ts + timedelta(hours=24)).isoformat()
        dl_before, dl_before_n = await store.get_avg_download_between(
            start_ts=before_start,
            end_ts=before_end,
        )
        dl_after, dl_after_n = await store.get_avg_download_between(
            start_ts=after_start,
            end_ts=after_end,
        )
        lat_before, lat_before_n = await store.get_avg_latency_between(
            start_ts=before_start,
            end_ts=before_end,
            target="gateway",
        )
        lat_after, lat_after_n = await store.get_avg_latency_between(
            start_ts=after_start,
            end_ts=after_end,
            target="gateway",
        )
        ram_before, ram_before_n = await store.get_avg_ram_between(
            start_ts=before_start,
            end_ts=before_end,
        )
        ram_after, ram_after_n = await store.get_avg_ram_between(
            start_ts=after_start,
            end_ts=after_end,
        )

        entry: dict = {
            "timestamp": ev["timestamp"],
            "event_type": ev["event_type"],
            "description": ev["description"],
        }

        if dl_before is not None and dl_after is not None and dl_before_n > 0 and dl_after_n > 0:
            delta_pct = ((dl_after - dl_before) / dl_before * 100) if dl_before else 0
            entry["download_delta_pct"] = round(delta_pct, 1)
            entry["download_samples_before"] = dl_before_n
            entry["download_samples_after"] = dl_after_n
            entry["download_verdict"] = (
                "improved"
                if delta_pct > DOWNLOAD_DELTA_VERDICT_THRESHOLD_PCT
                else "degraded"
                if delta_pct < -DOWNLOAD_DELTA_VERDICT_THRESHOLD_PCT
                else "neutral"
            )
        if (
            lat_before is not None
            and lat_after is not None
            and lat_before_n > 0
            and lat_after_n > 0
        ):
            delta_ms = lat_after - lat_before
            entry["latency_delta_ms"] = round(delta_ms, 2)
            entry["latency_samples_before"] = lat_before_n
            entry["latency_samples_after"] = lat_after_n
            entry["latency_verdict"] = (
                "improved"
                if delta_ms < -LATENCY_DELTA_VERDICT_THRESHOLD_MS
                else "degraded"
                if delta_ms > LATENCY_DELTA_VERDICT_THRESHOLD_MS
                else "neutral"
            )
        if (
            ram_before is not None
            and ram_after is not None
            and ram_before_n > 0
            and ram_after_n > 0
        ):
            delta_pct = ram_after - ram_before
            entry["ram_delta_pct"] = round(delta_pct, 1)
            entry["ram_samples_before"] = ram_before_n
            entry["ram_samples_after"] = ram_after_n

        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# generate_recommendations
# ---------------------------------------------------------------------------

async def generate_recommendations(store: DataStore, *, days: int = 30) -> list[dict]:
    """Generate prioritized recommendations based on trend + pattern analysis."""
    recs: list[dict] = []

    trends = await analyze_trends(store, days=days)
    patterns = await analyze_patterns(store, days=days)
    sla = await analyze_isp_sla(store, days=days)

    # --- Speed trending down ---
    dl_trend = trends.get("download", {})
    slope = dl_trend.get("slope_mbps_per_week", 0)
    dl_r2 = dl_trend.get("r_squared", 0)
    dl_samples = dl_trend.get("samples", 0)
    if (
        dl_samples >= MIN_TREND_SAMPLES
        and slope < -DOWNLOAD_TREND_DOWN_MBPS_PER_WEEK
        and dl_r2 >= R2_MEDIUM
    ):
        recs.append({
            "priority": "high",
            "category": "speed",
            "description": (
                f"Download speed trending down {abs(slope):.1f} Mbps/week "
                f"(R²={dl_r2:.2f}, n={dl_samples}). "
                "Possible ISP degradation or local interference."
            ),
            "confidence": "high" if dl_r2 >= R2_HIGH else "medium",
            "action": "Monitor for 1 more week; if continues, contact ISP.",
        })

    # --- Latency trending up ---
    lat_trend = trends.get("latency", {})
    lat_slope = lat_trend.get("slope_ms_per_week", 0)
    lat_r2 = lat_trend.get("r_squared", 0)
    lat_samples = lat_trend.get("samples", 0)
    if (
        lat_samples >= MIN_TREND_SAMPLES
        and lat_slope > LATENCY_TREND_UP_MS_PER_WEEK
        and lat_r2 >= R2_MEDIUM
    ):
        recs.append({
            "priority": "high",
            "category": "latency",
            "description": (
                f"Gateway latency trending up {lat_slope:.1f} ms/week "
                f"(R²={lat_r2:.2f}, n={lat_samples}). May indicate ISP routing change."
            ),
            "confidence": "high" if lat_r2 >= R2_HIGH else "medium",
            "action": "Check modem signal levels; contact ISP if sustained.",
        })

    # --- RAM creep (memory leak) ---
    ram_trend = trends.get("ram", {})
    ram_slope = ram_trend.get("slope_pct_per_week", 0)
    ram_r2 = ram_trend.get("r_squared", 0)
    ram_samples = ram_trend.get("samples", 0)
    if (
        ram_samples >= MIN_TREND_SAMPLES
        and ram_slope > RAM_TREND_UP_PCT_PER_WEEK
        and ram_r2 >= R2_MEDIUM
    ):
        recs.append({
            "priority": "medium",
            "category": "system",
            "description": (
                f"RAM usage increasing {ram_slope:.1f}%/week "
                f"(R²={ram_r2:.2f}, n={ram_samples}). Possible memory leak."
            ),
            "confidence": "high" if ram_r2 >= R2_HIGH else "medium",
            "action": "Schedule a router reboot to reclaim memory.",
        })
    elif (
        ram_samples >= MIN_TREND_SAMPLES
        and ram_trend
        and abs(ram_slope) < RAM_STABLE_EPSILON_PCT_PER_WEEK
    ):
        recs.append({
            "priority": "info",
            "category": "system",
            "description": (
                f"RAM stable at {ram_trend.get('avg_pct', 0):.0f}% — no memory leak."
            ),
            "confidence": "high",
            "action": "No action needed.",
        })

    # --- Peak degradation ---
    peak = patterns.get("peak_degradation", {})
    delta = peak.get("delta_pct", 0)
    peak_label = patterns.get("peak_window_label", "18:00-23:00")
    if delta < PEAK_DEGRADATION_PCT and peak.get("peak_samples", 0) >= PEAK_DEGRADATION_MIN_SAMPLES:
        recs.append({
            "priority": "medium",
            "category": "isp",
            "description": (
                f"Peak window ({peak_label}) averages {abs(delta):.0f}% slower than off-peak "
                f"({peak.get('peak_avg_mbps', 0):.0f} vs "
                f"{peak.get('offpeak_avg_mbps', 0):.0f} Mbps)."
            ),
            "confidence": "high" if peak.get("peak_samples", 0) >= 7 else "medium",
            "action": "ISP congestion during evenings. Consider adjusting QoS priorities.",
        })

    # --- SLA compliance ---
    if sla.get("tests", 0) >= 3:
        dl_sla = sla.get("download", {})
        pct_meeting = dl_sla.get("pct_meeting_plan", 100)
        if pct_meeting < SLA_MIN_COMPLIANCE_PCT:
            min_plan_mbps = PLAN_SPEED_DOWN * 0.90 / 1_000_000
            recs.append({
                "priority": "high",
                "category": "isp",
                "description": (
                    f"Only {pct_meeting:.0f}% of speed tests meet plan speed "
                    f"(≥{min_plan_mbps:.0f} Mbps of {PLAN_SPEED_DOWN / 1_000_000:.0f} Mbps "
                    f"plan). SLA score: {sla.get('sla_score', 0):.0f}/100."
                ),
                "confidence": "high",
                "action": "Document results and contact ISP for service review.",
            })

    # --- Abrupt change points ---
    cp = trends.get("change_points", {})
    dl_cp = cp.get("download", {})
    if dl_cp and dl_cp.get("direction") == "down":
        recs.append({
            "priority": "high",
            "category": "anomaly",
            "description": (
                f"Abrupt download shift detected: {dl_cp.get('delta_mbps', 0):.1f} Mbps "
                f"({dl_cp.get('delta_pct', 0):.1f}%) vs 7-day baseline."
            ),
            "confidence": "high",
            "action": "Check ISP status/events and recent router changes within last 24h.",
        })
    lat_cp = cp.get("latency", {})
    if lat_cp and lat_cp.get("direction") == "up":
        recs.append({
            "priority": "high",
            "category": "anomaly",
            "description": (
                f"Abrupt latency increase detected: +{lat_cp.get('delta_ms', 0):.2f} ms "
                f"({lat_cp.get('delta_pct', 0):.1f}%) vs 7-day baseline."
            ),
            "confidence": "high",
            "action": "Validate gateway/modem path health and investigate routing changes.",
        })

    # --- Packet loss ---
    loss = trends.get("packet_loss", {})
    if loss.get("per_week", 0) > PACKET_LOSS_EVENTS_PER_WEEK:
        recs.append({
            "priority": "medium",
            "category": "reliability",
            "description": (
                f"{loss['per_week']:.0f} packet loss events/week "
                f"({loss['events']} total in {days} days)."
            ),
            "confidence": "high",
            "action": "Check modem/cable signal levels; may need ISP tech visit.",
        })

    # --- Jitter degradation ---
    jitter = trends.get("jitter", {})
    jitter_slope = jitter.get("slope_ms_per_week", 0)
    jitter_r2 = jitter.get("r_squared", 0)
    jitter_samples = jitter.get("samples", 0)
    if (
        jitter_samples >= MIN_TREND_SAMPLES
        and jitter_slope > JITTER_TREND_UP_MS_PER_WEEK
        and jitter_r2 >= R2_MEDIUM
    ):
        recs.append({
            "priority": "medium",
            "category": "reliability",
            "description": (
                f"Jitter rising {jitter_slope:.1f} ms/week "
                f"(R²={jitter_r2:.2f}, n={jitter_samples})."
            ),
            "confidence": "high" if jitter_r2 >= R2_HIGH else "medium",
            "action": "Check congestion/QoS behavior and upstream modem signal quality.",
        })

    # --- Temperature risk ---
    temp = trends.get("temperature", {})
    temp_slope = temp.get("slope_c_per_week", 0)
    temp_r2 = temp.get("r_squared", 0)
    temp_samples = temp.get("samples", 0)
    temp_p95 = temp.get("p95_c", 0)
    if temp_samples >= MIN_TREND_SAMPLES and (
        temp_p95 >= TEMP_P95_RISK_C
        or (temp_slope > TEMP_TREND_UP_C_PER_WEEK and temp_r2 >= R2_MEDIUM)
    ):
        recs.append({
            "priority": "medium",
            "category": "system",
            "description": (
                f"Router temperature pressure detected (P95={temp_p95:.1f}°C, "
                f"trend={temp_slope:+.1f}°C/week)."
            ),
            "confidence": "high" if temp_r2 >= R2_HIGH else "medium",
            "action": (
                "Improve ventilation; sustained high thermals can reduce "
                "throughput stability."
            ),
        })

    # --- Conntrack saturation risk ---
    conn = trends.get("conntrack_utilization", {})
    conn_peak = conn.get("peak_pct", 0)
    conn_slope = conn.get("slope_pct_per_week", 0)
    conn_r2 = conn.get("r_squared", 0)
    conn_samples = conn.get("samples", 0)
    if conn_samples >= MIN_TREND_SAMPLES and (
        conn_peak >= CONNTRACK_PEAK_RISK_PCT
        or (conn_slope > CONNTRACK_TREND_UP_PCT_PER_WEEK and conn_r2 >= R2_MEDIUM)
    ):
        recs.append({
            "priority": "medium",
            "category": "reliability",
            "description": (
                f"Conntrack utilization is elevated (avg {conn.get('avg_pct', 0):.0f}%, "
                f"peak {conn_peak:.0f}%)."
            ),
            "confidence": (
                "high" if conn_peak >= CONNTRACK_PEAK_HIGH_CONFIDENCE_PCT else "medium"
            ),
            "action": (
                "Review connection churn (P2P/IoT bursts); tune conntrack "
                "limit/timeouts if needed."
            ),
        })

    # --- WiFi signal degradation ---
    for band_key in ("wifi_2.4", "wifi_5"):
        wifi = trends.get(band_key, {})
        wifi_slope = wifi.get("slope_db_per_week", 0)
        wifi_r2 = wifi.get("r_squared", 0)
        wifi_samples = wifi.get("samples", 0)
        if (
            wifi_samples >= MIN_TREND_SAMPLES
            and wifi_slope < -WIFI_SIGNAL_DOWN_DB_PER_WEEK
            and wifi_r2 >= R2_MEDIUM
        ):
            band = band_key.replace("wifi_", "")
            recs.append({
                "priority": "medium",
                "category": "wifi",
                "description": (
                    f"{band}GHz signal degrading {abs(wifi_slope):.1f} dB/week. "
                    "Check for new interference sources."
                ),
                "confidence": "high" if wifi_r2 >= R2_HIGH else "medium",
                "action": "Run WiFi channel survey; consider channel change.",
            })

    # --- WiFi noise floor degradation ---
    for band_key in ("2.4", "5"):
        noise = trends.get(f"noise_{band_key}", {})
        noise_slope = noise.get("slope_db_per_week", 0)
        noise_r2 = noise.get("r_squared", 0)
        noise_samples = noise.get("samples", 0)
        wifi = trends.get(f"wifi_{band_key}", {})
        wifi_slope = wifi.get("slope_db_per_week", 0)
        if (
            noise_samples >= MIN_TREND_SAMPLES
            and noise_slope > NOISE_FLOOR_UP_DB_PER_WEEK
            and noise_r2 >= R2_MEDIUM
            and (
                wifi_slope < WIFI_SUPPORTING_SLOPE_DB_PER_WEEK
                or wifi.get("avg_rssi", 0) < WIFI_WEAK_RSSI_DBM
            )
        ):
            recs.append({
                "priority": "medium",
                "category": "wifi",
                "description": (
                    f"{band_key}GHz noise floor worsening {noise_slope:.1f} dB/week "
                    f"(R²={noise_r2:.2f})."
                ),
                "confidence": "high" if noise_r2 >= R2_HIGH else "medium",
                "action": "Run channel survey and move to cleaner channel/bandwidth combination.",
            })

    if not recs:
        recs.append({
            "priority": "info",
            "category": "general",
            "description": "All metrics within normal ranges. Network is healthy.",
            "confidence": "high",
            "action": "No action needed.",
        })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: priority_order.get(r.get("priority", "info"), 4))
    return recs


# ---------------------------------------------------------------------------
# suggest_settings — specific NVRAM change suggestions
# ---------------------------------------------------------------------------

async def suggest_settings(store: DataStore) -> list[dict]:
    """Suggest specific NVRAM changes backed by historical data.

    Only suggests changes with high confidence of improvement.
    Each suggestion includes current value, proposed value, rationale.
    """
    suggestions: list[dict] = []

    # Get current config
    latest = await store.get_latest_config_snapshot()
    if not latest:
        return [{"message": "No config snapshots yet. Take a baseline snapshot first."}]

    config = json.loads(latest["nvram_json"])

    def _cfg(key: str, default: str = "0") -> str:
        return str(config.get(key, default))

    # --- QoS bandwidth alignment ---
    speed_rows = await store.get_speed_tests(days=30)
    valid_dl = [s["download_bps"] for s in speed_rows if s.get("download_bps")]
    valid_ul = [s["upload_bps"] for s in speed_rows if s.get("upload_bps")]

    if valid_dl and _cfg("qos_enable") == "1":
        measured_dl = mean(valid_dl) / 1000  # to Kbps
        current_ibw = int(config.get("qos_ibw") or 0)
        optimal_ibw = int(measured_dl * 0.96)  # 96% of measured
        if current_ibw and abs(current_ibw - optimal_ibw) / optimal_ibw > 0.10:
            suggestions.append({
                "key": "qos_ibw",
                "current": str(current_ibw),
                "proposed": str(optimal_ibw),
                "rationale": (
                    f"Measured avg download: {measured_dl / 1000:.0f} Mbps. "
                    f"Current QoS ibw ({current_ibw} Kbps) is "
                    f"{'too low' if current_ibw < optimal_ibw else 'too high'}. "
                    f"96% of measured = {optimal_ibw} Kbps."
                ),
                "risk": "low",
                "reversible": True,
            })

    if valid_ul and _cfg("qos_enable") == "1":
        measured_ul = mean(valid_ul) / 1000
        current_obw = int(config.get("qos_obw") or 0)
        optimal_obw = int(measured_ul * 0.90)
        if current_obw and abs(current_obw - optimal_obw) / optimal_obw > 0.10:
            suggestions.append({
                "key": "qos_obw",
                "current": str(current_obw),
                "proposed": str(optimal_obw),
                "rationale": (
                    f"Measured avg upload: {measured_ul / 1000:.0f} Mbps. "
                    f"Current QoS obw ({current_obw} Kbps) should be ~{optimal_obw} Kbps."
                ),
                "risk": "low",
                "reversible": True,
            })

    # --- DNS optimization ---
    if _cfg("wan0_dnsenable_x") == "1":
        lat_rows = await store.get_latency_probes(days=7, target="cloudflare")
        if lat_rows:
            cf_avg = mean([p["avg_ms"] for p in lat_rows if p.get("avg_ms")])
            if cf_avg < 20:
                suggestions.append({
                    "key": "wan0_dnsenable_x",
                    "current": "1 (ISP DNS)",
                    "proposed": "0 (custom: 1.1.1.1 / 1.0.0.1)",
                    "rationale": (
                        f"Cloudflare latency avg {cf_avg:.1f}ms suggests custom DNS "
                        "would likely improve resolution times over ISP DNS."
                    ),
                    "risk": "low",
                    "reversible": True,
                })

    # --- WAN admin surface reduction ---
    if _cfg("misc_http_x") == "1":
        suggestions.append({
            "key": "misc_http_x",
            "current": "1 (WAN HTTPS admin enabled)",
            "proposed": "0 (disable WAN admin access)",
            "rationale": (
                "WAN-exposed admin UI increases attack surface. Disable unless "
                "remote administration is explicitly required."
            ),
            "risk": "medium",
            "reversible": True,
        })

    # --- 5GHz TurboQAM ---
    if _cfg("wl1_turbo_qam") == "0":
        suggestions.append({
            "key": "wl1_turbo_qam",
            "current": "0",
            "proposed": "1",
            "rationale": (
                "TurboQAM is disabled on 5GHz; enabling can improve close-range "
                "throughput on supported clients."
            ),
            "risk": "low",
            "reversible": True,
        })

    # --- AiMesh services on standalone setups ---
    if _cfg("amas_enable") == "1":
        suggestions.append({
            "key": "amas_enable",
            "current": "1",
            "proposed": "0 (if no AiMesh nodes are connected)",
            "rationale": (
                "AiMesh control-plane services consume memory/CPU on standalone "
                "routers."
            ),
            "risk": "medium",
            "reversible": True,
        })

    # --- bwdpi / Adaptive QoS trade-off ---
    if _cfg("qos_type") == "1" and _cfg("apps_analysis") == "1" and _cfg("wrs_enable") == "0":
        suggestions.append({
            "key": "apps_analysis",
            "current": "1 (required for Adaptive QoS)",
            "proposed": "0 only if switching away from Adaptive QoS",
            "rationale": (
                "apps_analysis keeps bwdpi active, which increases memory use. "
                "Keep it for Adaptive QoS classification accuracy, or switch QoS "
                "mode to reclaim RAM."
            ),
            "risk": "medium",
            "reversible": True,
        })

    return suggestions
