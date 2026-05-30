from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from asusroutercontrol.analysis.dashboard import build_isp_client_dashboard
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import ClientLoad, SpeedTestResult


@pytest.mark.asyncio
async def test_dashboard_empty_window_returns_empty_sections(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        data = await build_isp_client_dashboard(store, hours=2, clients=5, timeline_points=3)
        assert data["isp_performance"]["tests_total"] == 0
        assert data["client_speed_load"]["top_clients"] == []
        assert data["isp_client_timeline"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dashboard_client_panel_handles_placeholder_only_rows(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        await store.insert_device_perf(
            ClientLoad(
                timestamp=now - timedelta(minutes=5),
                mac="AA:AA:AA:AA:AA:31",
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            ),
            commit=False,
        )
        await store.insert_device_perf(
            ClientLoad(
                timestamp=now - timedelta(minutes=2),
                mac="AA:AA:AA:AA:AA:31",
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            ),
            commit=False,
        )
        await store.commit()

        data = await build_isp_client_dashboard(store, hours=2, clients=5, timeline_points=2)
        rows = data["client_speed_load"]["top_clients"]
        assert rows
        row = rows[0]
        assert row["has_signal"] is False
        assert row["signal_samples"] == 0
        assert row["placeholder_samples"] == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dashboard_isp_panel_tracks_mixed_quality_and_timeline_context(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        await store.insert_speed_test(
            SpeedTestResult(
                timestamp=now - timedelta(minutes=25),
                download_bps=260_000_000,
                upload_bps=34_000_000,
                ping_ms=11.0,
                jitter_ms=2.0,
                source="composite",
                provider_details_json='{"confidence": 90}',
            ),
            commit=False,
        )
        await store.insert_speed_test(
            SpeedTestResult(
                timestamp=now - timedelta(minutes=15),
                download_bps=720_000_000,
                upload_bps=40_000_000,
                ping_ms=13.0,
                jitter_ms=3.0,
                source="composite",
                provider_details_json='{"confidence": 40}',
            ),
            commit=False,
        )
        await store.insert_speed_test(
            SpeedTestResult(
                timestamp=now - timedelta(minutes=5),
                error="timeout",
                source="cloudflare",
            ),
            commit=False,
        )
        await store.insert_device_perf(
            ClientLoad(
                timestamp=now - timedelta(minutes=5),
                mac="AA:AA:AA:AA:AA:32",
                tx_rate_mbps=28.0,
                rx_rate_mbps=14.0,
                rssi=-49,
                load_pct=4.7,
            ),
            commit=False,
        )
        await store.commit()

        data = await build_isp_client_dashboard(store, hours=4, clients=5, timeline_points=3)
        quality = data["isp_performance"]["quality_counts"]
        assert quality["ok"] == 1
        assert quality["suspect"] == 1
        assert quality["error"] == 1
        assert data["isp_performance"]["avg_confidence"] == 65.0

        timeline = data["isp_client_timeline"]
        assert timeline
        assert timeline[0]["clients_seen"] >= 1
    finally:
        await store.close()
