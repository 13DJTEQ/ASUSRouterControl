"""Tests for discovery helpers and connect orchestration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from asusroutercontrol.config import Config
from asusroutercontrol.connect import (
    ConnectionProbeResult,
    probe_connection,
    setup_router_connection,
)
from asusroutercontrol.discovery import (
    DiscoveryCandidate,
    discover_router_candidates,
    pick_default_host,
)
from asusroutercontrol.profile import ProfileStore, RouterProfile, load_profiles, save_profiles


def test_pick_default_host_prefers_reachable():
    candidates = [
        DiscoveryCandidate(host="router.asus.com", source="well-known", reachable=False),
        DiscoveryCandidate(host="192.168.1.1", source="gateway", reachable=True),
    ]
    assert pick_default_host(candidates) == "192.168.1.1"


def test_discover_router_candidates_no_probe():
    found = discover_router_candidates(probe=False, include_gateway=False)
    assert found
    assert all(c.reachable is None for c in found)
    assert any("asus" in c.host for c in found)


@pytest.mark.asyncio
async def test_probe_connection_http_only(monkeypatch: pytest.MonkeyPatch):
    async def _http_ok(cfg, username, password):
        return True, None

    async def _ssh_should_not_run(*_a, **_k):
        raise AssertionError("SSH should be skipped")

    monkeypatch.setattr("asusroutercontrol.connect.probe_http", _http_ok)
    monkeypatch.setattr("asusroutercontrol.connect.probe_ssh", _ssh_should_not_run)

    result = await probe_connection(Config(), "admin", "secret", try_ssh=False)
    assert result.http_ok is True
    assert result.ssh_ok is None
    assert result.ok is True


@pytest.mark.asyncio
async def test_setup_router_connection_persists_profile_and_creds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _probe(cfg, username, password, *, try_ssh=True):
        return ConnectionProbeResult(http_ok=True, ssh_ok=False, ssh_error="down")

    stored: dict[str, str] = {}

    def _store(username, password, *, env=None, backend=None):
        stored["username"] = username
        stored["password"] = password
        stored["backend"] = backend or "keychain"
        return stored["backend"]

    monkeypatch.setattr("asusroutercontrol.connect.probe_connection", _probe)
    monkeypatch.setattr("asusroutercontrol.connect.store_router_credentials", _store)

    result = await setup_router_connection(
        host="192.168.50.1",
        username="admin",
        password="pw",
        ssh_enabled=True,
        ssh_port=1313,
        credential_backend="keychain",
        data_dir=tmp_path,
        discover=False,
        cfg=Config(data_dir=tmp_path),
    )
    assert result.profile.host == "192.168.50.1"
    assert result.profile.ssh_enabled is False
    assert result.probe.http_ok is True
    assert result.credentials_backend == "keychain"
    assert stored["backend"] == "keychain"

    loaded = load_profiles(tmp_path)
    assert loaded.active is not None
    assert loaded.active.host == "192.168.50.1"


@pytest.mark.asyncio
async def test_scheduler_skips_ssh_when_profile_disables_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from asusroutercontrol.scheduler import MonitorScheduler

    profile = RouterProfile(
        id="no-ssh",
        display_name="No SSH",
        host="10.0.0.1",
        ssh_enabled=False,
    )
    store = ProfileStore()
    store.upsert(profile, make_active=True)
    save_profiles(store, tmp_path)

    monkeypatch.setattr(
        "asusroutercontrol.scheduler.get_router_credentials",
        lambda: ("admin", "secret"),
    )

    called = {"ssh": False}

    async def _probe_ssh(self, *, username: str, password: str):
        called["ssh"] = True
        return True, "router"

    monkeypatch.setattr(MonitorScheduler, "_probe_ssh_capabilities", _probe_ssh)

    sched = MonitorScheduler(
        store=SimpleNamespace(),
        cfg=Config(data_dir=tmp_path, runtime_env="prod"),
    )
    runtime = await sched._determine_runtime_profile()
    assert runtime.capability == "degraded-no-ssh"
    assert called["ssh"] is False
