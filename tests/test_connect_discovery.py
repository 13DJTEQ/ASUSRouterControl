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

    def _store(username, password, *, ssh_port=None, env=None, backend=None):
        stored["username"] = username
        stored["password"] = password
        stored["ssh_port"] = ssh_port
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
    assert stored["ssh_port"] == 1313

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


@pytest.mark.asyncio
async def test_setup_falls_back_when_credential_store_fails(tmp_path, monkeypatch):
    from asusroutercontrol.config import Config
    from asusroutercontrol.connect import ConnectionProbeResult, setup_router_connection

    async def _probe(cfg, username, password, *, try_ssh=True):
        return ConnectionProbeResult(http_ok=True, ssh_ok=None)

    calls = {"n": 0}

    def _store(username, password, *, ssh_port=None, env=None, backend=None):
        calls["n"] += 1
        if backend == "bitwarden":
            raise RuntimeError("bw encode failed")
        return backend or "keychain"

    monkeypatch.setattr("asusroutercontrol.connect.probe_connection", _probe)
    monkeypatch.setattr("asusroutercontrol.connect.store_router_credentials", _store)

    result = await setup_router_connection(
        host="192.168.50.1",
        username="admin",
        password="pw",
        credential_backend="bitwarden",
        data_dir=tmp_path,
        discover=False,
        cfg=Config(data_dir=tmp_path),
    )
    assert result.probe.http_ok is True
    assert result.credentials_backend == "keychain"
    assert calls["n"] == 2


def test_format_http_probe_error_login_endpoint():
    from asusroutercontrol.connect import format_http_probe_error

    msg = format_http_probe_error(
        RuntimeError("Cannot access EndpointService.LOGIN. Failed in `async_connect`"),
        host="router.asus.com",
        username="admin",
    )
    assert "router.asus.com" in msg
    assert "admin" in msg
    assert "bitwarden" in msg.lower() or "admin" in msg.lower()


def test_format_http_probe_error_credentials_cause():
    from asusrouter.error import AsusRouterAccessError
    from asusrouter.modules.endpoint.error import AccessError

    from asusroutercontrol.connect import format_http_probe_error

    cause = AsusRouterAccessError("Access error", AccessError.CREDENTIALS, {})
    try:
        raise AsusRouterAccessError(
            "Cannot access EndpointService.LOGIN. Failed in `async_connect`"
        ) from cause
    except AsusRouterAccessError as exc:
        msg = format_http_probe_error(exc, host="192.168.50.1", username="admin")
    assert "rejected login" in msg.lower()
    assert "192.168.50.1" in msg
    assert "admin" in msg


def test_format_http_probe_error_captcha_cause():
    from asusrouter.error import AsusRouterAccessError
    from asusrouter.modules.endpoint.error import AccessError

    from asusroutercontrol.connect import format_http_probe_error

    cause = AsusRouterAccessError("Access error", AccessError.CAPTCHA, {})
    try:
        raise AsusRouterAccessError(
            "Cannot access EndpointService.LOGIN. Failed in `async_connect`"
        ) from cause
    except AsusRouterAccessError as exc:
        msg = format_http_probe_error(exc, host="router.asus.com")
    assert "captcha" in msg.lower()


def test_format_http_probe_error_lockout_cause():
    from asusrouter.error import AsusRouterAccessError
    from asusrouter.modules.endpoint.error import AccessError

    from asusroutercontrol.connect import format_http_probe_error

    cause = AsusRouterAccessError(
        "Access error", AccessError.TRY_AGAIN, {"timeout": 42}
    )
    try:
        raise AsusRouterAccessError(
            "Cannot access EndpointService.LOGIN. Failed in `async_connect`"
        ) from cause
    except AsusRouterAccessError as exc:
        msg = format_http_probe_error(exc, host="router.asus.com")
    assert "locked" in msg.lower()
    assert "42" in msg


@pytest.mark.asyncio
async def test_setup_skips_transport_retry_on_credential_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Wrong password should not burn time retrying HTTPS ports."""
    calls: list[tuple[str, int, bool]] = []

    async def _probe(cfg, username, password, *, try_ssh=True):
        calls.append((cfg.router_host, cfg.router_port, cfg.use_ssl))
        return ConnectionProbeResult(
            http_ok=False,
            ssh_ok=None,
            http_error=(
                "Router rejected login for router.asus.com (tried user 'admin'). "
                "Username/password are wrong."
            ),
        )

    monkeypatch.setattr("asusroutercontrol.connect.probe_connection", _probe)
    monkeypatch.setattr(
        "asusroutercontrol.connect.diagnose_http_admin",
        lambda host, **kwargs: "tcp:80=open (mocked)",
    )

    with pytest.raises(ConnectionError, match="rejected login"):
        await setup_router_connection(
            host="router.asus.com",
            username="admin",
            password="wrong",
            ssh_enabled=False,
            data_dir=tmp_path,
            discover=False,
            cfg=Config(data_dir=tmp_path),
        )
    assert len(calls) == 1
    assert calls[0] == ("router.asus.com", 8443, True)


@pytest.mark.asyncio
async def test_setup_retries_asus_https_8443_on_reachability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, int, bool]] = []

    async def _probe(cfg, username, password, *, try_ssh=True):
        calls.append((cfg.router_host, cfg.router_port, cfg.use_ssl))
        if cfg.use_ssl and cfg.router_port == 8443:
            return ConnectionProbeResult(http_ok=True, ssh_ok=None)
        return ConnectionProbeResult(
            http_ok=False,
            ssh_ok=None,
            http_error="Timed out reaching router.asus.com",
        )

    monkeypatch.setattr("asusroutercontrol.connect.probe_connection", _probe)
    monkeypatch.setattr(
        "asusroutercontrol.connect.store_router_credentials",
        lambda *a, **k: "keychain",
    )

    result = await setup_router_connection(
        host="router.asus.com",
        username="admin",
        password="pw",
        ssh_enabled=False,
        data_dir=tmp_path,
        discover=False,
        cfg=Config(data_dir=tmp_path),
    )
    assert result.probe.http_ok is True
    assert result.profile.http_port == 8443
    assert result.profile.use_ssl is True
    # HTTPS:8443 is attempted first now.
    assert calls[0] == ("router.asus.com", 8443, True)
    assert ("router.asus.com", 8443, True) in calls


def test_http_transport_attempts_prefers_https():
    from asusroutercontrol.connect import _http_transport_attempts

    attempts = _http_transport_attempts("192.168.50.1", http_port=80, use_ssl=False)
    assert attempts[0] == ("192.168.50.1", 8443, True)
    assert ("192.168.50.1", 80, False) in attempts


def test_format_http_probe_error_mentions_router_settings():
    from asusroutercontrol.connect import format_http_probe_error

    msg = format_http_probe_error(
        RuntimeError("Cannot access EndpointService.LOGIN. Failed in `async_connect`"),
        host="router.asus.com",
        username="admin",
    )
    assert "captcha" in msg.lower()
    assert "8443" in msg or "authentication method" in msg.lower()


@pytest.mark.asyncio
async def test_setup_blocks_blank_password_when_vault_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Blank password + locked vault must fail before any HTTP LOGIN attempt."""
    calls: list[tuple] = []

    async def _probe(cfg, username, password, *, try_ssh=True):
        calls.append((cfg.router_host, cfg.router_port, cfg.use_ssl))
        return ConnectionProbeResult(http_ok=True, ssh_ok=None)

    monkeypatch.setattr("asusroutercontrol.connect.probe_connection", _probe)
    monkeypatch.setattr(
        "asusroutercontrol.credentials.bitwarden_vault_status",
        lambda: "locked",
    )
    monkeypatch.setattr(
        "asusroutercontrol.credentials.get_router_credentials",
        lambda host_hint=None: (None, None),
    )
    monkeypatch.setattr(
        "asusroutercontrol.credentials.load_runtime_env_files",
        lambda: None,
    )

    with pytest.raises(ConnectionError, match="bw_sync_router_env"):
        await setup_router_connection(
            host="router.asus.com",
            username="13Maschine",
            password="",
            ssh_enabled=False,
            data_dir=tmp_path,
            discover=False,
            cfg=Config(data_dir=tmp_path),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_probe_http_transport_ladder_prefers_https(
    monkeypatch: pytest.MonkeyPatch,
):
    from asusroutercontrol.connect import probe_http_transport_ladder

    seen: list[tuple[str, int, bool]] = []

    async def _probe(cfg, username, password):
        seen.append((cfg.router_host, cfg.router_port, cfg.use_ssl))
        if cfg.router_port == 8443 and cfg.use_ssl:
            return True, None
        return False, "refused"

    monkeypatch.setattr("asusroutercontrol.connect.probe_http", _probe)
    ok, err, winning = await probe_http_transport_ladder(
        Config(router_host="192.168.50.1", router_port=80, use_ssl=False),
        "admin",
        "pw",
    )
    assert ok is True
    assert err is None
    assert winning.router_port == 8443
    assert winning.use_ssl is True
    assert seen[0] == ("192.168.50.1", 8443, True)


def test_discover_probes_https_admin_port(monkeypatch: pytest.MonkeyPatch):
    from asusroutercontrol import discovery as disc

    probed: list[tuple[str, int]] = []

    def fake_probe(host: str, port: int, *, timeout: float = 1.5) -> bool:
        probed.append((host, port))
        return port == 8443 and host == "router.asus.com"

    monkeypatch.setattr(disc, "probe_tcp", fake_probe)
    monkeypatch.setattr(disc, "default_gateway_ipv4", lambda: None)
    found = disc.discover_router_candidates(probe=True, include_gateway=False)
    assert any(c.host == "router.asus.com" and c.reachable for c in found)
    assert any(port == 8443 for _host, port in probed)
    assert any(port == 80 for _host, port in probed)
