from __future__ import annotations

from datetime import datetime

import pytest

from asusroutercontrol.config import Config
from asusroutercontrol.scheduler import MonitorScheduler


class _Store:
    def __init__(self) -> None:
        self.sent: dict[str, datetime] = {}

    async def get_notification_last_sent(self, rec_key: str):
        return self.sent.get(rec_key)

    async def set_notification_last_sent(self, rec_key: str, *, sent_at=None) -> None:
        self.sent[rec_key] = sent_at or datetime.utcnow()


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

    monkeypatch.setattr("asusroutercontrol.optimizer.generate_recommendations", _fake_generate_recommendations)
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

    async def _fake_probe_latency(_ssh):
        return [object()]

    async def _fake_probe_system(_ssh):
        return object()

    async def _fake_probe_wifi(_ssh):
        return [object()]

    async def _fake_sleep(_seconds: float):
        scheduler._running = False

    monkeypatch.setattr("asusroutercontrol.scheduler.RouterSSH", _FakeSSH)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_latency", _fake_probe_latency)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_system", _fake_probe_system)
    monkeypatch.setattr("asusroutercontrol.scheduler.probe_wifi", _fake_probe_wifi)

    store = _ProbeBatchStore()
    scheduler = MonitorScheduler(store=store, cfg=Config())
    scheduler._running = True
    monkeypatch.setattr(scheduler, "_interruptible_sleep", _fake_sleep)

    await scheduler._probe_loop()
    assert ("latency", False) in store.calls
    assert ("system", False) in store.calls
    assert ("wifi", False) in store.calls
    assert store.commits == 1
    assert store.rollbacks == 0
