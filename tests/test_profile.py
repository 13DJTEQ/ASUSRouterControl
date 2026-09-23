"""Tests for router profile store and config overlay."""

from __future__ import annotations

from pathlib import Path

import pytest

from asusroutercontrol.config import load_config
from asusroutercontrol.profile import (
    ProfileStore,
    RouterProfile,
    apply_profile,
    load_profiles,
    new_profile_id,
    save_profiles,
)


def test_new_profile_id_is_stable_shape():
    pid = new_profile_id("Living Room Router")
    assert pid.startswith("living-room-router-")
    assert len(pid) <= 64


def test_profile_roundtrip(tmp_path: Path):
    profile = RouterProfile(
        id="lab-router",
        display_name="Lab",
        host="192.168.50.1",
        ssh_port=1313,
        ssh_enabled=True,
        http_ok=True,
        ssh_ok=False,
    )
    store = ProfileStore()
    store.upsert(profile, make_active=True)
    path = save_profiles(store, tmp_path)
    assert path.exists()

    loaded = load_profiles(tmp_path)
    assert loaded.active_id == "lab-router"
    assert loaded.active is not None
    assert loaded.active.host == "192.168.50.1"
    assert loaded.active.ssh_port == 1313
    assert loaded.active.http_ok is True
    assert loaded.active.ssh_ok is False


def test_apply_profile_overlays_connection_fields(env_clean):
    cfg = load_config()
    profile = RouterProfile(
        id="cust-1",
        display_name="Customer",
        host="10.0.0.1",
        http_port=8443,
        use_ssl=True,
        ssh_port=22,
        backend="merlin",
    )
    overlaid = apply_profile(cfg, profile)
    assert overlaid.router_host == "10.0.0.1"
    assert overlaid.router_port == 8443
    assert overlaid.use_ssl is True
    assert overlaid.ssh_port == 22


def test_load_config_default_ssh_port_is_22(env_clean):
    cfg = load_config()
    assert cfg.ssh_port == 22


def test_load_config_profile_overlay_respects_env(
    tmp_path: Path, env_clean, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    profile = RouterProfile(
        id="env-wins",
        display_name="Env Wins",
        host="10.9.9.9",
        ssh_port=2222,
    )
    store = ProfileStore()
    store.upsert(profile, make_active=True)
    save_profiles(store, tmp_path)

    cfg = load_config()
    assert cfg.router_host == "10.9.9.9"
    assert cfg.ssh_port == 2222

    monkeypatch.setenv("SSH_PORT", "1313")
    monkeypatch.setenv("ROUTER_HOST", "router.asus.com")
    cfg2 = load_config()
    assert cfg2.ssh_port == 1313
    assert cfg2.router_host == "router.asus.com"


def test_load_config_profile_beats_stale_env_http_transport(
    tmp_path: Path, env_clean, monkeypatch: pytest.MonkeyPatch
):
    """Successful Connect profile (8443/ssl) must not lose to .env :80/false."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROUTER_PORT", "80")
    monkeypatch.setenv("USE_SSL", "false")
    profile = RouterProfile(
        id="https-profile",
        display_name="HTTPS",
        host="192.168.50.1",
        http_port=8443,
        use_ssl=True,
        ssh_port=1313,
    )
    store = ProfileStore()
    store.upsert(profile, make_active=True)
    save_profiles(store, tmp_path)

    cfg = load_config()
    assert cfg.router_port == 8443
    assert cfg.use_ssl is True
    assert cfg.router_host == "192.168.50.1"
