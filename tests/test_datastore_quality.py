from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import LatencyProbe, SpeedTestResult


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
