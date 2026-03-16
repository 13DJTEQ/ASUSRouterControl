from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from asusroutercontrol.config import Config
from asusroutercontrol.models import SpeedTestResult
from asusroutercontrol.scheduler import MonitorScheduler


class _Store:
    def __init__(self) -> None:
        self.sent: dict[str, datetime] = {}

    async def get_notification_last_sent(self, rec_key: str):
        return self.sent.get(rec_key)

    async def set_notification_last_sent(self, rec_key: str, *, sent_at=None) -> None:
        self.sent[rec_key] = sent_at or datetime.utcnow()


class _SpeedtestStore:
    """Minimal store stub for speedtest loop tests."""

    def __init__(self) -> None:
        self.inserted: list[SpeedTestResult] = []

    async def insert_speed_test(self, result: SpeedTestResult) -> None:
        self.inserted.append(result)


class _ProbeBatchStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.commits = 0
        self.rollbacks = 0

    async def insert_latency_probe(self, _probe, *, commit: bool = True) -> None:
        self.calls.append(("latency", commit))

    async def insert_system_snapshot(self, _snap, *, commit: bool = True) -> None:
        self.calls.append(("system", commit))

    async def insert_wifi_snapshot(self, _snap, *, commit: bool = True) -> None:
        self.calls.append(("wifi", commit))

    async def get_latest_wifi_snapshot(self, _band: str):
        return None

    async def get_all_devices(self):
        return []

    async def insert_client_load(self, _client_load, *, commit: bool = True) -> None:
        self.calls.append(("client_load", commit))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_scheduler_uses_persisted_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_generate_recommendations(_store, *, days: int = 30):
        return [{
            "priority": "high",
            "category": "speed",
            "description": "Speed issue",
            "action": "Investigate",
        }]

    notifications: list[tuple[str, str, str]] = []

    def _fake_notify(title: str, subtitle: str, body: str) -> None:
        notifications.append((title, subtitle, body))

    monkeypatch.setattr(
        "asusroutercontrol.optimizer.generate_recommendations",
        _fake_generate_recommendations,
    )
    monkeypatch.setattr("asusroutercontrol.notifications.notify", _fake_notify)

    store = _Store()
    scheduler = MonitorScheduler(store=store, cfg=Config())
    await scheduler._evaluate_recommendations()
    await scheduler._evaluate_recommendations()

    assert len(notifications) == 1
    assert "speed:high" in store.sent


@pytest.mark.asyncio
async def test_probe_loop_batches_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeSSH:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def run(self, _command: str):
            return SimpleNamespace(ok=True, stdout="")

    async def _fake_probe_latency(_ssh):
        return [object()]

    async def _fake_probe_system(_ssh):
        return object()

    async def _fake_probe_wifi(_ssh):
        return [SimpleNamespace(band="2.4")]

    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FakeSSH)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_latency", _fake_probe_latency)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_system", _fake_probe_system)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_wifi", _fake_probe_wifi)

    store = _ProbeBatchStore()
    scheduler = MonitorScheduler(store=store, cfg=Config())
    scheduler._running = True

    # Test the extracted cycle method directly (avoids needing to mock sleep)
    await scheduler._run_probes_cycle()
    assert ("latency", False) in store.calls
    assert ("system", False) in store.calls
    assert ("wifi", False) in store.calls
    assert store.commits == 1
    assert store.rollbacks == 0


@pytest.mark.asyncio
async def test_speedtest_callback_fires_on_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_speedtest_complete is called after a scheduled speed test."""
    fake_result = SpeedTestResult(
        download_bps=250_000_000,
        upload_bps=25_000_000,
        ping_ms=12.5,
        source="composite",
    )

    async def _fake_run_speed_test(**_kw):
        return fake_result

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.run_speed_test", _fake_run_speed_test
    )

    callback_results: list[SpeedTestResult] = []

    def _cb(result: SpeedTestResult) -> None:
        callback_results.append(result)

    store = _SpeedtestStore()
    # Use speedtest_times that match "now" so the loop fires immediately
    hour_now = datetime.now().hour
    cfg = Config(speedtest_times=(hour_now,))
    scheduler = MonitorScheduler(
        store=store, cfg=cfg, on_speedtest_complete=_cb
    )
    scheduler._running = True

    sleep_call_count = 0
    _real_sleep = asyncio.sleep

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False
        # Don't actually sleep in tests

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert len(callback_results) == 1
    assert callback_results[0].download_bps == 250_000_000
    assert len(store.inserted) == 1


@pytest.mark.asyncio
async def test_speedtest_callback_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler works fine when no callback is set."""
    fake_result = SpeedTestResult(
        download_bps=100_000_000, source="composite"
    )

    async def _fake_run_speed_test(**_kw):
        return fake_result

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.run_speed_test", _fake_run_speed_test
    )

    store = _SpeedtestStore()
    hour_now = datetime.now().hour
    cfg = Config(speedtest_times=(hour_now,))
    scheduler = MonitorScheduler(store=store, cfg=cfg)  # no callback
    scheduler._running = True

    sleep_call_count = 0

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert len(store.inserted) == 1  # stored, no crash


@pytest.mark.asyncio
async def test_scheduled_speedtest_uses_unfiltered_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduled speed tests should keep all providers enabled."""
    fake_result = SpeedTestResult(
        download_bps=125_000_000,
        upload_bps=15_000_000,
        source="composite",
    )
    seen_sources: list[str | None] = []

    async def _fake_run_speed_test(*, source: str | None = None):
        seen_sources.append(source)
        return fake_result

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.run_speed_test", _fake_run_speed_test
    )

    store = _SpeedtestStore()
    cfg = Config(speedtest_times=(datetime.now().hour,))
    scheduler = MonitorScheduler(store=store, cfg=cfg)
    scheduler._running = True

    sleep_call_count = 0

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert seen_sources == [None]
    assert len(store.inserted) == 1


def test_notify_on_speedtest_config_toggle() -> None:
    """Config toggle defaults to True and can be disabled."""
    assert Config().notify_on_speedtest is True
    assert Config(notify_on_speedtest=False).notify_on_speedtest is False


def test_scheduler_perf_sample_emits_periodic_baseline_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    for _ in range(scheduler._perf_log_every):
        scheduler._record_perf_sample(
            "unit.test.metric",
            0.010,
            context="loop=test",
        )
    assert any("baseline[unit.test.metric]" in rec.getMessage() for rec in caplog.records)
