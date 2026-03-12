from __future__ import annotations

import json
from pathlib import Path

import pytest

import asusroutercontrol.optimizer as optimizer
from asusroutercontrol.analyzer import analyze_patterns, analyze_trends
from asusroutercontrol.optimizer import generate_recommendations

FIXTURE_DIR = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURE_DIR / "calibration_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _fixture_payload(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


class FixtureStore:
    def __init__(self, payload: dict) -> None:
        self._speed = payload.get("speed_tests", [])
        self._latency = payload.get("latency_probes", [])
        self._system = payload.get("system_snapshots", [])
        self._wifi = payload.get("wifi_snapshots", [])

    async def get_speed_tests(
        self, *, days: int = 30, source: str | None = None
    ) -> list[dict]:
        if source is None:
            return list(self._speed)
        return [r for r in self._speed if r.get("source") == source]

    async def get_latency_probes(
        self, *, days: int = 30, target: str | None = None
    ) -> list[dict]:
        if target is None:
            return list(self._latency)
        return [r for r in self._latency if r.get("target") == target]

    async def get_system_snapshots(self, *, days: int = 30) -> list[dict]:
        return list(self._system)

    async def get_wifi_snapshots(
        self, *, days: int = 30, band: str | None = None
    ) -> list[dict]:
        if band is None:
            return list(self._wifi)
        return [r for r in self._wifi if r.get("band") == band]

    async def get_speed_metric_series(
        self, *, days: int = 30, metric: str = "download_bps", source: str | None = None
    ) -> list[dict]:
        rows = await self.get_speed_tests(days=days, source=source)
        return [
            {"timestamp": r["timestamp"], metric: r.get(metric), "quality": r.get("quality", "ok")}
            for r in rows
            if r.get(metric) is not None
        ]

    async def get_latency_metric_series(
        self, *, days: int = 30, metric: str = "avg_ms", target: str | None = None
    ) -> list[dict]:
        rows = await self.get_latency_probes(days=days, target=target)
        return [
            {
                "timestamp": r["timestamp"],
                "target": r.get("target"),
                metric: r.get(metric),
                "quality": r.get("quality", "ok"),
            }
            for r in rows
            if r.get(metric) is not None
        ]

    async def get_system_metric_series(
        self, *, days: int = 30, metric: str = "ram_pct"
    ) -> list[dict]:
        return [
            {"timestamp": r["timestamp"], metric: r.get(metric)}
            for r in self._system
            if r.get(metric) is not None
        ]

    async def get_wifi_metric_series(
        self, *, days: int = 30, metric: str = "avg_rssi", band: str | None = None
    ) -> list[dict]:
        rows = await self.get_wifi_snapshots(days=days, band=band)
        return [
            {"timestamp": r["timestamp"], "band": r.get("band"), metric: r.get(metric)}
            for r in rows
            if r.get(metric) is not None
        ]


@pytest.mark.parametrize("name,value", sorted(MANIFEST["thresholds"].items()))
def test_threshold_manifest_matches_optimizer_constants(name: str, value: float | int) -> None:
    assert getattr(optimizer, name) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_name", sorted(MANIFEST["profiles"].keys()))
async def test_fixture_replay_profiles(profile_name: str) -> None:
    profile = MANIFEST["profiles"][profile_name]
    payload = _fixture_payload(profile["fixture"])
    store = FixtureStore(payload)

    trends = await analyze_trends(store, days=30)
    patterns = await analyze_patterns(store, days=30)
    recs = await generate_recommendations(store, days=30)

    assert isinstance(trends, dict)
    assert isinstance(patterns, dict)
    assert recs

    rec_keys = {(r.get("category"), r.get("priority")) for r in recs}
    expected = {
        (e["category"], e["priority"])
        for e in profile.get("expected_recommendations", [])
    }
    assert expected.issubset(rec_keys)

    if profile.get("forbid_actionable", False):
        actionable = [r for r in recs if r.get("priority") in ("high", "medium")]
        assert actionable == []
