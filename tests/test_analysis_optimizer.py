from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from asusroutercontrol.analyzer import analyze_patterns, analyze_trends
from asusroutercontrol.optimizer import (
    correlate_config_performance,
    generate_recommendations,
    suggest_settings,
)


class _TrendStore:
    def __init__(
        self,
        *,
        speed_rows: list[dict] | None = None,
        latency_rows: list[dict] | None = None,
        wifi_rows: list[dict] | None = None,
        system_rows: list[dict] | None = None,
    ) -> None:
        self._speed_rows = speed_rows or []
        self._latency_rows = latency_rows or []
        self._wifi_rows = wifi_rows or []
        self._system_rows = system_rows or []

    async def get_speed_tests(self, *, days: int = 30, source: str | None = None) -> list[dict]:
        return self._speed_rows

    async def get_latency_probes(
        self, *, days: int = 30, target: str | None = None
    ) -> list[dict]:
        if target is None:
            return self._latency_rows
        return [r for r in self._latency_rows if r.get("target") == target]

    async def get_wifi_snapshots(self, *, days: int = 30, band: str | None = None) -> list[dict]:
        if band is None:
            return self._wifi_rows
        return [r for r in self._wifi_rows if r.get("band") == band]

    async def get_system_snapshots(self, *, days: int = 30) -> list[dict]:
        return self._system_rows


class _SuggestStore:
    def __init__(self, nvram: dict[str, str]) -> None:
        self._nvram = nvram

    async def get_latest_config_snapshot(self) -> dict:
        return {"nvram_json": json.dumps(self._nvram)}

    async def get_speed_tests(self, *, days: int = 30, source: str | None = None) -> list[dict]:
        return []

    async def get_latency_probes(
        self, *, days: int = 7, target: str | None = None
    ) -> list[dict]:
        if target == "cloudflare":
            return [{"avg_ms": 12.5}]
        return []


class _CorrelationStore:
    def __init__(self) -> None:
        self.event_ts = (datetime.utcnow() - timedelta(hours=12)).isoformat()

    async def get_config_events(self, *, days: int = 90) -> list[dict]:
        return [{"timestamp": self.event_ts, "event_type": "config_change", "description": "qos update"}]

    async def get_avg_download_between(self, *, start_ts: str, end_ts: str) -> tuple[float | None, int]:
        if end_ts <= self.event_ts:
            return 250_000_000.0, 4
        return 300_000_000.0, 5

    async def get_avg_latency_between(
        self, *, start_ts: str, end_ts: str, target: str = "gateway"
    ) -> tuple[float | None, int]:
        if end_ts <= self.event_ts:
            return 12.0, 6
        return 9.5, 7

    async def get_avg_ram_between(self, *, start_ts: str, end_ts: str) -> tuple[float | None, int]:
        if end_ts <= self.event_ts:
            return 58.0, 8
        return 52.0, 9


@pytest.mark.asyncio
async def test_analyze_trends_filters_outlier_points() -> None:
    base = datetime.utcnow() - timedelta(days=20)
    speed_rows = []
    for i in range(12):
        dl = 280_000_000 + (i * 2_000_000)
        if i == 6:
            dl = 5_000_000
        speed_rows.append({
            "timestamp": (base + timedelta(days=i)).isoformat(),
            "download_bps": dl,
            "upload_bps": 35_000_000,
        })

    store = _TrendStore(speed_rows=speed_rows)
    trends = await analyze_trends(store, days=30)
    dl = trends["download"]
    assert dl["outliers_removed"] >= 1
    assert dl["samples"] < dl["raw_samples"]
    assert dl["slope_mbps_per_week"] > 0


@pytest.mark.asyncio
async def test_analyze_trends_detects_abrupt_download_change_point() -> None:
    now = datetime.utcnow()
    speed_rows = []
    for i in range(20):
        ts = (now - timedelta(hours=10 * i)).isoformat()
        dl = 300_000_000
        if i < 3:
            dl = 220_000_000
        speed_rows.append({
            "timestamp": ts,
            "download_bps": dl,
            "upload_bps": 35_000_000,
        })

    store = _TrendStore(speed_rows=speed_rows)
    trends = await analyze_trends(store, days=30)
    cp = trends.get("change_points", {}).get("download", {})
    assert cp
    assert cp["direction"] == "down"
    assert cp["delta_mbps"] < 0


@pytest.mark.asyncio
async def test_analyze_patterns_uses_dynamic_peak_hours() -> None:
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    speed_rows: list[dict] = []
    for hour in range(24):
        for day in range(3):
            ts = (now - timedelta(days=day)).replace(hour=hour).isoformat()
            dl = 220_000_000
            if hour in (14, 15, 16):
                dl = 110_000_000
            speed_rows.append({"timestamp": ts, "download_bps": dl, "upload_bps": 35_000_000})

    store = _TrendStore(speed_rows=speed_rows, latency_rows=[])
    patterns = await analyze_patterns(store, days=30)
    peak_hours = patterns.get("peak_hours", [])
    assert 14 in peak_hours
    assert 15 in peak_hours
    assert 16 in peak_hours


@pytest.mark.asyncio
async def test_generate_recommendations_requires_min_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_trends(_store, *, days: int = 30):
        return {
            "download": {"slope_mbps_per_week": -5.0, "r_squared": 0.9, "samples": 9},
            "packet_loss": {"events": 0, "per_week": 0, "total_probes": 0},
        }

    async def _fake_patterns(_store, *, days: int = 30):
        return {}

    async def _fake_sla(_store, *, days: int = 30):
        return {"tests": 0}

    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_trends", _fake_trends)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_patterns", _fake_patterns)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_isp_sla", _fake_sla)

    recs = await generate_recommendations(object(), days=30)
    assert all(r.get("category") != "speed" for r in recs)


@pytest.mark.asyncio
async def test_generate_recommendations_sets_high_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_trends(_store, *, days: int = 30):
        return {
            "download": {"slope_mbps_per_week": -6.0, "r_squared": 0.75, "samples": 12},
            "packet_loss": {"events": 0, "per_week": 0, "total_probes": 0},
        }

    async def _fake_patterns(_store, *, days: int = 30):
        return {}

    async def _fake_sla(_store, *, days: int = 30):
        return {"tests": 0}

    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_trends", _fake_trends)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_patterns", _fake_patterns)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_isp_sla", _fake_sla)

    recs = await generate_recommendations(object(), days=30)
    speed_rec = next(r for r in recs if r.get("category") == "speed")
    assert speed_rec["confidence"] == "high"


@pytest.mark.asyncio
async def test_correlate_config_performance_uses_window_aggregates() -> None:
    store = _CorrelationStore()
    rows = await correlate_config_performance(store, days=30)
    assert len(rows) == 1
    entry = rows[0]
    assert entry["download_delta_pct"] > 0
    assert entry["latency_delta_ms"] < 0
    assert entry["ram_delta_pct"] < 0
    assert entry["download_samples_before"] == 4
    assert entry["download_samples_after"] == 5


@pytest.mark.asyncio
async def test_suggest_settings_includes_extended_findings() -> None:
    store = _SuggestStore({
        "qos_enable": "1",
        "wan0_dnsenable_x": "1",
        "misc_http_x": "1",
        "wl1_turbo_qam": "0",
        "amas_enable": "1",
        "qos_type": "1",
        "apps_analysis": "1",
        "wrs_enable": "0",
    })

    suggestions = await suggest_settings(store)
    keys = {s["key"] for s in suggestions}
    assert "wan0_dnsenable_x" in keys
    assert "misc_http_x" in keys
    assert "wl1_turbo_qam" in keys
    assert "amas_enable" in keys
    assert "apps_analysis" in keys


@pytest.mark.asyncio
async def test_analyze_trends_reports_jitter_temperature_and_conntrack() -> None:
    now = datetime.utcnow()
    speed_rows = []
    system_rows = []
    for i in range(15):
        ts = (now - timedelta(hours=6 * i)).isoformat()
        speed_rows.append({
            "timestamp": ts,
            "download_bps": 280_000_000,
            "upload_bps": 35_000_000,
            "jitter_ms": 3.0 + (i * 0.1),
        })
        system_rows.append({
            "timestamp": ts,
            "ram_pct": 55.0 + (i * 0.1),
            "temp_c": 70.0 + (i * 0.3),
            "conntrack_count": 30_000 + (i * 200),
            "conntrack_max": 100_000,
        })

    store = _TrendStore(speed_rows=speed_rows, system_rows=system_rows)
    trends = await analyze_trends(store, days=30)
    assert "jitter" in trends
    assert "temperature" in trends
    assert "conntrack_utilization" in trends


@pytest.mark.asyncio
async def test_generate_recommendations_flags_noise_floor_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_trends(_store, *, days: int = 30):
        return {
            "noise_2.4": {"slope_db_per_week": 1.5, "r_squared": 0.75, "samples": 12},
            "wifi_2.4": {"slope_db_per_week": -1.0, "avg_rssi": -68.0, "samples": 12},
            "packet_loss": {"events": 0, "per_week": 0, "total_probes": 0},
        }

    async def _fake_patterns(_store, *, days: int = 30):
        return {}

    async def _fake_sla(_store, *, days: int = 30):
        return {"tests": 0}

    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_trends", _fake_trends)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_patterns", _fake_patterns)
    monkeypatch.setattr("asusroutercontrol.optimizer.analyze_isp_sla", _fake_sla)

    recs = await generate_recommendations(object(), days=30)
    assert any("noise floor" in r.get("description", "").lower() for r in recs)
