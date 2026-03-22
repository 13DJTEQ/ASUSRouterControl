from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import (
    ClientLoad,
    ConnectionType,
    Device,
    LatencyProbe,
    SpeedTestResult,
)


@pytest.mark.asyncio
async def test_speed_test_quality_drops_invalid_and_flags_suspect(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        await store.insert_speed_test(SpeedTestResult(download_bps=-1, upload_bps=10_000_000))
        rows = await store.get_speed_tests(days=1)
        assert rows == []

        await store.insert_speed_test(
            SpeedTestResult(
                download_bps=700_000_000,
                upload_bps=40_000_000,
                ping_ms=12.0,
                jitter_ms=2.0,
            )
        )
        rows = await store.get_speed_tests(days=1)
        assert len(rows) == 1
        assert rows[0]["quality"] == "suspect"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_client_loads_prioritizes_signal_rows(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        await store.insert_client_load(
            ClientLoad(
                timestamp=now - timedelta(seconds=30),
                mac="AA:AA:AA:AA:AA:01",
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            )
        )
        await store.insert_client_load(
            ClientLoad(
                timestamp=now,
                mac="AA:AA:AA:AA:AA:01",
                tx_rate_mbps=1.5,
                rx_rate_mbps=0.8,
                rssi=-58,
                load_pct=1.2,
            )
        )
        rows = await store.get_client_loads(hours=1, limit=10)
        assert rows
        assert rows[0]["has_signal"] == 1
        assert rows[0]["tx_rate_mbps"] == 1.5
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_client_loads_prefers_device_perf_band_history(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        mac = "AA:AA:AA:AA:AA:10"

        await store.upsert_device(
            Device(
                mac=mac,
                hostname="test-phone",
                connection=ConnectionType.UNKNOWN,
                band="unknown",
            )
        )
        await store.insert_client_load(
            ClientLoad(
                timestamp=now,
                mac=mac,
                tx_rate_mbps=1.0,
                rx_rate_mbps=0.5,
                rssi=-55,
                load_pct=1.0,
            )
        )
        await store.insert_device_perf(
            ClientLoad(
                timestamp=now,
                mac=mac,
                band="5GHz",
                tx_rate_mbps=1.0,
                rx_rate_mbps=0.5,
                rssi=-55,
                load_pct=1.0,
            )
        )

        rows = await store.get_client_loads(hours=1, limit=10)
        assert rows
        assert rows[0]["band"] == "5GHz"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_client_loads_falls_back_to_client_traffic_for_legacy_rows(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        mac = "AA:AA:AA:AA:AA:11"
        await store.upsert_device(
            Device(
                mac=mac,
                hostname="wired-host",
                connection=ConnectionType.WIRED,
                band=None,
            )
        )
        await store.insert_client_load(
            ClientLoad(
                timestamp=now,
                mac=mac,
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            )
        )

        rows = await store.get_client_loads(hours=1, limit=10)
        assert rows
        assert rows[0]["band"] == "wired"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_upsert_device_preserves_known_connection_when_snapshot_is_unknown(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        mac = "AA:AA:AA:AA:AA:12"
        await store.upsert_device(
            Device(
                mac=mac,
                connection=ConnectionType.WIFI_5G,
                band="5GHz",
            )
        )
        await store.upsert_device(
            Device(
                mac=mac,
                connection=ConnectionType.UNKNOWN,
                band="unknown",
            )
        )

        devices = await store.get_all_devices()
        row = next(d for d in devices if d["mac"] == mac)
        assert row["connection"] == ConnectionType.WIFI_5G.value
        assert row["band"] == "5GHz"

        sessions = await store.get_device_sessions(mac, limit=1)
        assert sessions
        assert sessions[0]["connection"] == ConnectionType.WIFI_5G.value
        assert sessions[0]["band"] == "5GHz"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_client_load_window_stats_counts_placeholders(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        now = datetime.utcnow()
        await store.insert_client_load(
            ClientLoad(
                timestamp=now - timedelta(seconds=45),
                mac="AA:AA:AA:AA:AA:01",
                tx_rate_mbps=2.0,
                rx_rate_mbps=1.0,
                rssi=-60,
                load_pct=1.5,
            )
        )
        await store.insert_client_load(
            ClientLoad(
                timestamp=now - timedelta(seconds=30),
                mac="AA:AA:AA:AA:AA:02",
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            )
        )
        await store.insert_client_load(
            ClientLoad(
                timestamp=now - timedelta(seconds=15),
                mac="AA:AA:AA:AA:AA:03",
                tx_rate_mbps=None,
                rx_rate_mbps=None,
                rssi=None,
                load_pct=0.0,
            )
        )
        stats = await store.get_client_load_window_stats(hours=1)
        assert stats["samples"] == 3
        assert stats["signal_rows"] == 1
        assert stats["placeholder_rows"] == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_latency_quality_drops_invalid_and_flags_suspect(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        await store.insert_latency_probe(
            LatencyProbe(target="gateway", avg_ms=5.0, loss_pct=120.0, samples=10)
        )
        rows = await store.get_latency_probes(days=1, target="gateway")
        assert rows == []

        await store.insert_latency_probe(
            LatencyProbe(target="gateway", avg_ms=0.0, loss_pct=0.0, samples=10)
        )
        rows = await store.get_latency_probes(days=1, target="gateway")
        assert len(rows) == 1
        assert rows[0]["quality"] == "suspect"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_prune_old_data_includes_speed_tests(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        old_ts = datetime.utcnow() - timedelta(days=120)
        await store.insert_speed_test(
            SpeedTestResult(
                timestamp=old_ts,
                download_bps=250_000_000,
                upload_bps=35_000_000,
                ping_ms=10.0,
            )
        )
        pruned = await store.prune_old_data(retention_days=90)
        assert "speed_tests" in pruned
        assert pruned["speed_tests"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_cooldown_persistence(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        key = "speed:high"
        assert await store.get_notification_last_sent(key) is None
        sent_at = datetime.utcnow() - timedelta(minutes=30)
        await store.set_notification_last_sent(key, sent_at=sent_at)
        loaded = await store.get_notification_last_sent(key)
        assert loaded is not None
        assert abs((loaded - sent_at).total_seconds()) < 1.0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_speed_metric_series_projection_query(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        await store.insert_speed_test(
            SpeedTestResult(
                download_bps=275_000_000,
                upload_bps=34_000_000,
                ping_ms=11.0,
                jitter_ms=2.0,
            ),
            commit=False,
        )
        await store.commit()
        rows = await store.get_speed_metric_series(days=1, metric="download_bps")
        assert rows
        row = rows[0]
        assert "download_bps" in row
        assert "quality" in row
        assert row["download_bps"] == 275_000_000
    finally:
        await store.close()
