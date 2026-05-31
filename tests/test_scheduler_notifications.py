from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from asusroutercontrol.config import Config
from asusroutercontrol.models import ConnectionType, Device, SpeedTestResult, TrafficSnapshot
from asusroutercontrol.scheduler import MonitorScheduler, RuntimeProfile


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


def _patch_speedtest_schedule(
    monkeypatch: pytest.MonkeyPatch,
    *,
    delay_seconds: int = 30,
) -> None:
    def _fake_next_speedtest_time(_cfg: Config) -> datetime:
        return datetime.now() + timedelta(seconds=delay_seconds)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler._next_speedtest_time",
        _fake_next_speedtest_time,
    )


@pytest.mark.asyncio
async def test_runtime_profile_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: (None, None),
    )
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    profile = await scheduler._determine_runtime_profile()
    assert profile.capability == "degraded-no-credentials"
    assert profile.operation_mode == "unknown"


@pytest.mark.asyncio
async def test_runtime_profile_full_with_router_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    class _FakeSSH:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def run(self, _command: str):
            return SimpleNamespace(ok=True, stdout="1", stderr="", exit_code=0)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )
    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FakeSSH)
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    profile = await scheduler._determine_runtime_profile()
    assert profile.capability == "full"
    assert profile.operation_mode == "router"
    assert captured["hostname"] == scheduler._cfg.router_host
    assert captured["port"] == scheduler._cfg.ssh_port


@pytest.mark.asyncio
async def test_runtime_profile_uses_scheduler_cfg_for_ssh_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeSSH:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def run(self, _command: str):
            return SimpleNamespace(ok=True, stdout="1", stderr="", exit_code=0)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )
    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FakeSSH)

    cfg = Config(router_host="10.0.0.9", ssh_port=2222)
    scheduler = MonitorScheduler(store=_Store(), cfg=cfg)
    profile = await scheduler._determine_runtime_profile()

    assert profile.capability == "full"
    assert profile.operation_mode == "router"
    assert captured["hostname"] == "10.0.0.9"
    assert captured["port"] == 2222


@pytest.mark.asyncio
async def test_runtime_profile_degraded_when_ssh_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailSSH:
        def __init__(self, *args, **kwargs):
            return None

        async def connect(self) -> None:
            raise RuntimeError("ssh down")

        async def disconnect(self) -> None:
            return None

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )
    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FailSSH)
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    profile = await scheduler._determine_runtime_profile()
    assert profile.capability == "degraded-no-ssh"
    assert profile.operation_mode == "unknown"


def test_select_task_specs_by_runtime_profile() -> None:
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())

    selected_full, skipped_full = scheduler._select_task_specs(
        RuntimeProfile(capability="full", operation_mode="router")
    )
    assert {spec.name for spec in selected_full} == {
        "speedtest",
        "probe",
        "client-traffic",
        "poll",
        "prune",
        "config",
        "recommend",
    }
    assert skipped_full == []

    selected_ssh_down, skipped_ssh_down = scheduler._select_task_specs(
        RuntimeProfile(capability="degraded-no-ssh", operation_mode="unknown")
    )
    assert {spec.name for spec in selected_ssh_down} == {
        "poll",
        "prune",
        "recommend",
    }
    assert {name for name, _reason in skipped_ssh_down} == {
        "speedtest",
        "probe",
        "client-traffic",
        "config",
    }

    selected_no_creds, skipped_no_creds = scheduler._select_task_specs(
        RuntimeProfile(capability="degraded-no-credentials", operation_mode="unknown")
    )
    assert {spec.name for spec in selected_no_creds} == {
        "prune",
        "recommend",
    }
    assert {name for name, _reason in skipped_no_creds} == {
        "speedtest",
        "probe",
        "client-traffic",
        "poll",
        "config",
    }


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
async def test_client_traffic_cycle_adds_wired_fallback_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ClientTrafficStore:
        def __init__(self) -> None:
            self.client_load_rows = []
            self.device_perf_rows = []
            self.upserted_devices = []

        async def get_all_devices(self):
            return [{
                "mac": "00:3E:E1:C9:2C:0B",
                "hostname": "MacPro12Core",
                "ip": "192.168.1.242",
            }]

        async def insert_client_load(self, row, *, commit: bool = True) -> None:
            self.client_load_rows.append((row, commit))

        async def insert_device_perf(self, row, *, commit: bool = True) -> None:
            self.device_perf_rows.append((row, commit))

        async def upsert_device(self, dev, *, commit: bool = True) -> bool:
            self.upserted_devices.append((dev, commit))
            return False

    async def _fake_probe_client_traffic(_ssh):
        return [{
            "mac": "aa:aa:aa:aa:aa:01",
            "band": "5GHz",
            "rssi": -45,
            "rx_bytes": 2_000_000,
            "tx_bytes": 1_000_000,
        }]

    async def _fake_probe_wired_clients(_ssh, *, wifi_macs=None):
        assert "aa:aa:aa:aa:aa:01" in (wifi_macs or set())
        return [{
            "mac": "00:3E:E1:C9:2C:0B",
            "ip": "192.168.1.242",
        }]

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.probe_client_traffic",
        _fake_probe_client_traffic,
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.probe_wired_clients",
        _fake_probe_wired_clients,
    )

    store = _ClientTrafficStore()
    scheduler = MonitorScheduler(store=store, cfg=Config())
    scheduler._client_prev["aa:aa:aa:aa:aa:01"] = (
        1_500_000,
        900_000,
        datetime.utcnow() - timedelta(seconds=60),
    )

    await scheduler._collect_client_traffic(SimpleNamespace())

    assert store.upserted_devices
    fallback_device = store.upserted_devices[0][0]
    assert fallback_device.mac == "00:3E:E1:C9:2C:0B"
    assert fallback_device.connection.value == "wired"

    wired_perf = [row for row, _commit in store.device_perf_rows if row.band == "wired"]
    assert wired_perf
    assert wired_perf[0].mac == "00:3E:E1:C9:2C:0B"

@pytest.mark.asyncio
async def test_poll_cycle_records_presence_rows_for_online_clients() -> None:
    class _PollPresenceStore:
        def __init__(self) -> None:
            self.upserted_devices = []
            self.device_perf_rows = []
            self.traffic_rows = []
            self.commits = 0

        async def upsert_device(self, dev, *, commit: bool = True) -> bool:
            self.upserted_devices.append((dev, commit))
            return False

        async def insert_device_perf(self, row, *, commit: bool = True) -> None:
            self.device_perf_rows.append((row, commit))

        async def insert_traffic(self, row, *, commit: bool = True) -> None:
            self.traffic_rows.append((row, commit))

        async def commit(self) -> None:
            self.commits += 1

    class _Backend:
        async def connect(self) -> None:
            return None

        async def get_connected_devices(self) -> list[Device]:
            return [
                Device(
                    mac="00:11:22:33:44:55",
                    hostname="wired-client",
                    connection=ConnectionType.WIRED,
                    is_online=True,
                ),
                Device(
                    mac="AA:BB:CC:DD:EE:01",
                    hostname="wifi-client",
                    connection=ConnectionType.WIFI_5G,
                    band="5GHz",
                    rssi=-48,
                    is_online=True,
                ),
                Device(
                    mac="AA:BB:CC:DD:EE:02",
                    hostname="offline-client",
                    connection=ConnectionType.WIFI_2G,
                    band="2.4GHz",
                    is_online=False,
                ),
            ]

        async def get_traffic_stats(self) -> TrafficSnapshot:
            return TrafficSnapshot(rx_rate_bps=10_000, tx_rate_bps=5_000)

    store = _PollPresenceStore()
    scheduler = MonitorScheduler(store=store, cfg=Config())
    await scheduler._run_poll_cycle(_Backend())

    assert len(store.upserted_devices) == 3
    assert len(store.device_perf_rows) == 2
    by_mac = {row.mac: row for row, _commit in store.device_perf_rows}
    assert by_mac["00:11:22:33:44:55"].band == "wired"
    assert by_mac["AA:BB:CC:DD:EE:01"].band == "5GHz"
    for row in by_mac.values():
        assert row.tx_rate_mbps is None
        assert row.rx_rate_mbps is None
        assert row.rssi is None
        assert row.load_pct == 0.0
    assert store.traffic_rows
    assert store.commits == 1


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
    _patch_speedtest_schedule(monkeypatch)
    cfg = Config()
    scheduler = MonitorScheduler(
        store=store, cfg=cfg, on_speedtest_complete=_cb
    )
    scheduler._running = True

    sleep_call_count = 0
    sleep_calls: list[float] = []

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_calls.append(float(seconds))
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False
        # Don't actually sleep in tests

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert len(callback_results) == 1
    assert callback_results[0].download_bps == 250_000_000
    assert len(store.inserted) == 1
    assert sleep_calls
    assert sleep_calls[0] > 0


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
    _patch_speedtest_schedule(monkeypatch)
    cfg = Config()
    scheduler = MonitorScheduler(store=store, cfg=cfg)  # no callback
    scheduler._running = True

    sleep_call_count = 0
    sleep_calls: list[float] = []

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_calls.append(float(seconds))
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert len(store.inserted) == 1  # stored, no crash
    assert sleep_calls
    assert sleep_calls[0] > 0


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
    _patch_speedtest_schedule(monkeypatch)
    cfg = Config()
    scheduler = MonitorScheduler(store=store, cfg=cfg)
    scheduler._running = True

    sleep_call_count = 0
    sleep_calls: list[float] = []

    async def _counting_sleep(seconds, *args, **kwargs):
        nonlocal sleep_call_count
        sleep_calls.append(float(seconds))
        sleep_call_count += 1
        if sleep_call_count >= 2:
            scheduler._running = False

    monkeypatch.setattr("asyncio.sleep", _counting_sleep)

    await scheduler._speedtest_loop()

    assert seen_sources == [None]
    assert len(store.inserted) == 1
    assert sleep_calls
    assert sleep_calls[0] > 0


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
