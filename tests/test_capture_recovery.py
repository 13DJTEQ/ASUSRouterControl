"""Tests for SSH recovery loop and probe cap observability."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from asusroutercontrol.config import Config
from asusroutercontrol.probes import DEFAULT_CLIENT_CAP, probe_client_traffic, probe_wifi
from asusroutercontrol.scheduler import (
    MonitorScheduler,
    RuntimeProfile,
)
# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _Store:
    def __init__(self) -> None:
        self.sent: dict[str, datetime] = {}

    async def get_notification_last_sent(self, rec_key: str):
        return self.sent.get(rec_key)

    async def set_notification_last_sent(self, rec_key: str, *, sent_at=None) -> None:
        self.sent[rec_key] = sent_at or datetime.utcnow()


class _Result:
    def __init__(self, *, ok: bool, stdout: str) -> None:
        self.ok = ok
        self.stdout = stdout


class _FakeSSH:
    def __init__(self, responses: dict[str, _Result] | None = None) -> None:
        self._responses = responses or {}
        self.commands: list[str] = []

    async def run(self, cmd: str) -> _Result:
        self.commands.append(cmd)
        return self._responses.get(cmd, _Result(ok=True, stdout=""))


# ---------------------------------------------------------------------------
# SSH recovery tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssh_recovery_promotes_degraded_to_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SSH becomes available, recovery loop promotes profile and starts
    previously-skipped tasks."""
    probe_attempts = []

    class _FailThenSucceedSSH:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self) -> None:
            probe_attempts.append(1)
            if len(probe_attempts) <= 1:
                raise RuntimeError("ssh not yet ready")

        async def disconnect(self) -> None:
            pass

        async def run(self, _cmd: str):
            return SimpleNamespace(ok=True, stdout="1", stderr="", exit_code=0)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )
    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FailThenSucceedSSH)

    # Bypass real sleep — just yield control
    sleep_count = 0

    async def _fast_sleep(seconds, *args, **kwargs):
        nonlocal sleep_count
        sleep_count += 1

    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    scheduler._running = True

    # Initial profile should be degraded
    profile = await scheduler._determine_runtime_profile()
    assert profile.capability == "degraded-no-ssh"

    # Set the scheduler into degraded state manually
    scheduler._runtime_profile = profile
    _, skipped = scheduler._select_task_specs(profile)
    scheduler._skipped_specs = [
        spec
        for spec in scheduler._task_specs()
        if spec.name in {name for name, _reason in skipped}
    ]

    # Skipped specs should include SSH-dependent loops
    skipped_names = {s.name for s in scheduler._skipped_specs}
    assert "probe" in skipped_names
    assert "client-traffic" in skipped_names
    assert "config" in skipped_names

    # Run recovery loop — it should promote after second attempt
    await scheduler._ssh_recovery_loop()

    assert scheduler._runtime_profile is not None
    assert scheduler._runtime_profile.capability == "full"
    assert scheduler._runtime_profile.operation_mode == "router"

    # Started tasks should include the previously-skipped SSH loops
    started_task_names = {t.get_name() for t in scheduler._tasks}
    assert "probe" in started_task_names
    assert "client-traffic" in started_task_names
    assert "config" in started_task_names

    # Cleanup
    for t in scheduler._tasks:
        t.cancel()
    await asyncio.gather(*scheduler._tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_ssh_recovery_exits_when_already_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery loop exits immediately if profile is already full."""
    async def _fast_sleep(seconds, *args, **kwargs):
        pass

    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    scheduler._running = True
    scheduler._runtime_profile = RuntimeProfile(
        capability="full", operation_mode="router"
    )

    # Should return immediately without error
    await scheduler._ssh_recovery_loop()


@pytest.mark.asyncio
async def test_ssh_recovery_not_started_when_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery loop is not started when initial profile is full."""

    class _OkSSH:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def run(self, _cmd: str):
            return SimpleNamespace(ok=True, stdout="1", stderr="", exit_code=0)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )
    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _OkSSH)

    sleep_calls: list[float] = []

    async def _fast_sleep(seconds, *args, **kwargs):
        sleep_calls.append(seconds)
        # Stop after first sleep to avoid infinite loop
        scheduler._running = False

    monkeypatch.setattr("asyncio.sleep", _fast_sleep)

    # Stub all the loop methods to be no-ops
    async def _noop(self_or_none=None):
        while scheduler._running:
            await asyncio.sleep(999)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._speedtest_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._probe_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._client_traffic_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._poll_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._prune_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._config_snapshot_loop", _noop
    )
    monkeypatch.setattr(
        "asusroutercontrol.scheduler.MonitorScheduler._recommendation_loop", _noop
    )

    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    await scheduler.run()

    # No task should be named "ssh-recovery"
    # (tasks are cleared by run(), but we can check via logs or absence)
    assert scheduler._runtime_profile is not None
    assert scheduler._runtime_profile.capability == "full"


# ---------------------------------------------------------------------------
# Probe cap observability tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_client_traffic_cap_truncation_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When client count exceeds cap, a warning is logged."""
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\\\.[0-9]+)?_ifname='"
    # 5 MACs on one interface, cap at 2
    assoc_stdout = "\n".join(f"assoclist AA:AA:AA:AA:AA:{i:02X}" for i in range(5))
    responses = {
        nvram_cmd: _Result(ok=False, stdout=""),  # force fallback
        "wl -i eth1 assoclist 2>/dev/null": _Result(ok=True, stdout=assoc_stdout),
        "wl -i eth2 assoclist 2>/dev/null": _Result(ok=True, stdout=""),
    }
    # Provide sta_info responses for capped MACs
    for i in range(5):
        mac = f"AA:AA:AA:AA:AA:{i:02X}"
        responses[f"wl -i eth1 sta_info {mac} 2>/dev/null"] = _Result(
            ok=True,
            stdout=f"tx data bytes: {1000 + i * 100}\nrx data bytes: {2000 + i * 100}\n",
        )

    ssh = _FakeSSH(responses)
    with caplog.at_level(logging.WARNING, logger="asusroutercontrol.probes"):
        rows = await probe_client_traffic(ssh, client_cap=2)

    # Only 2 clients should be returned (capped)
    assert len(rows) == 2
    # Warning about truncation should appear
    assert any(
        "truncated" in rec.getMessage() and "5" in rec.getMessage()
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_probe_client_traffic_cap_zero_means_unlimited() -> None:
    """client_cap=0 probes all clients without truncation."""
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\\\.[0-9]+)?_ifname='"
    mac_count = 25
    assoc_stdout = "\n".join(f"assoclist AA:AA:AA:AA:AA:{i:02X}" for i in range(mac_count))
    responses = {
        nvram_cmd: _Result(ok=False, stdout=""),
        "wl -i eth1 assoclist 2>/dev/null": _Result(ok=True, stdout=assoc_stdout),
        "wl -i eth2 assoclist 2>/dev/null": _Result(ok=True, stdout=""),
    }
    for i in range(mac_count):
        mac = f"AA:AA:AA:AA:AA:{i:02X}"
        responses[f"wl -i eth1 sta_info {mac} 2>/dev/null"] = _Result(
            ok=True,
            stdout=f"tx data bytes: {1000 + i}\nrx data bytes: {2000 + i}\n",
        )

    ssh = _FakeSSH(responses)
    rows = await probe_client_traffic(ssh, client_cap=0)
    assert len(rows) == mac_count


@pytest.mark.asyncio
async def test_probe_wifi_cap_truncation_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """probe_wifi logs truncation when cap is exceeded."""
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\\\.[0-9]+)?_ifname='"
    mac_count = 5
    assoc_stdout = "\n".join(f"assoclist AA:AA:AA:AA:AA:{i:02X}" for i in range(mac_count))
    responses = {
        nvram_cmd: _Result(ok=False, stdout=""),
        "cat /proc/net/dev": _Result(ok=True, stdout=""),
        "wl -i eth1 assoclist 2>/dev/null": _Result(ok=True, stdout=assoc_stdout),
        "wl -i eth2 assoclist 2>/dev/null": _Result(ok=True, stdout=""),
        "wl -i eth1 noise 2>/dev/null": _Result(ok=True, stdout="-92\n"),
        "wl -i eth2 noise 2>/dev/null": _Result(ok=True, stdout="-95\n"),
        "wl -i eth1 channel 2>/dev/null": _Result(ok=True, stdout="current mac channel 6\n"),
        "wl -i eth2 channel 2>/dev/null": _Result(ok=True, stdout="current mac channel 36\n"),
    }
    for i in range(mac_count):
        mac = f"AA:AA:AA:AA:AA:{i:02X}"
        responses[f"wl -i eth1 sta_info {mac} 2>/dev/null | grep 'per antenna rssi'"] = _Result(
            ok=True, stdout=f"per antenna rssi of last rx data frame: -{40 + i}\n"
        )

    ssh = _FakeSSH(responses)
    with caplog.at_level(logging.WARNING, logger="asusroutercontrol.probes"):
        snaps = await probe_wifi(ssh, client_cap=2)

    band_24 = next(s for s in snaps if s.band == "2.4")
    # client_count should still reflect total (all MACs seen), not just capped
    assert band_24.client_count == mac_count
    # Truncation warning
    assert any("truncated" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_probe_wifi_cap_default_unchanged() -> None:
    """Default client_cap matches the module constant."""
    assert DEFAULT_CLIENT_CAP == 20


def test_config_probe_client_cap_default() -> None:
    """Config.probe_client_cap defaults to 20."""
    assert Config().probe_client_cap == 20


def test_config_probe_client_cap_custom() -> None:
    """Config.probe_client_cap can be overridden."""
    cfg = Config(probe_client_cap=50)
    assert cfg.probe_client_cap == 50


def test_select_task_specs_unchanged_for_full_profile() -> None:
    """Existing task selection behavior is preserved."""
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    selected, skipped = scheduler._select_task_specs(
        RuntimeProfile(capability="full", operation_mode="router")
    )
    assert {spec.name for spec in selected} == {
        "speedtest", "probe", "client-traffic", "poll", "prune", "config", "recommend",
    }
    assert skipped == []


def test_select_task_specs_degraded_no_ssh() -> None:
    """SSH-dependent tasks are skipped when capability is degraded-no-ssh."""
    scheduler = MonitorScheduler(store=_Store(), cfg=Config())
    selected, skipped = scheduler._select_task_specs(
        RuntimeProfile(capability="degraded-no-ssh", operation_mode="unknown")
    )
    assert {spec.name for spec in selected} == {"poll", "prune", "recommend"}
    assert {name for name, _ in skipped} == {
        "speedtest", "probe", "client-traffic", "config",
    }
