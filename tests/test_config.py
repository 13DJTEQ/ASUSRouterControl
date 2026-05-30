"""Tests for config.py — env parsing, defaults, env-file override, type coercion."""

from __future__ import annotations

from pathlib import Path

import pytest

from asusroutercontrol.config import Config, load_config


class TestConfigDefaults:
    def test_default_backend_is_merlin(self, env_clean):
        cfg = load_config()
        assert cfg.router_backend == "merlin"

    def test_default_host(self, env_clean):
        cfg = load_config()
        assert cfg.router_host == "router.asus.com"

    def test_default_port(self, env_clean):
        cfg = load_config()
        assert cfg.router_port == 80

    def test_default_ssl_is_false(self, env_clean):
        cfg = load_config()
        assert cfg.use_ssl is False

    def test_default_ssh_port(self, env_clean):
        cfg = load_config()
        assert cfg.ssh_port == 1313

    def test_default_data_dir_is_under_home(self, env_clean):
        cfg = load_config()
        assert cfg.data_dir == Path.home() / ".asusroutercontrol"

    def test_frozen_dataclass(self, env_clean):
        cfg = load_config()
        with pytest.raises((AttributeError, TypeError)):
            cfg.router_host = "changed"  # type: ignore[misc]


class TestConfigFromEnv:
    def test_router_backend_env(self, env_clean, monkeypatch):
        monkeypatch.setenv("ROUTER_BACKEND", "freshtomato")
        cfg = load_config()
        assert cfg.router_backend == "freshtomato"

    def test_router_host_env(self, env_clean, monkeypatch):
        monkeypatch.setenv("ROUTER_HOST", "192.168.1.1")
        cfg = load_config()
        assert cfg.router_host == "192.168.1.1"

    def test_router_port_env(self, env_clean, monkeypatch):
        monkeypatch.setenv("ROUTER_PORT", "8080")
        cfg = load_config()
        assert cfg.router_port == 8080

    def test_use_ssl_true_variants(self, env_clean, monkeypatch):
        for val in ("true", "1", "yes"):
            monkeypatch.setenv("USE_SSL", val)
            cfg = load_config()
            assert cfg.use_ssl is True, f"Expected True for USE_SSL={val!r}"

    def test_use_ssl_false_variants(self, env_clean, monkeypatch):
        for val in ("false", "0", "no"):
            monkeypatch.setenv("USE_SSL", val)
            cfg = load_config()
            assert cfg.use_ssl is False

    def test_speedtest_times_csv(self, env_clean, monkeypatch):
        monkeypatch.setenv("SPEEDTEST_TIMES", "0,6,12,18")
        cfg = load_config()
        assert cfg.speedtest_times == (0, 6, 12, 18)

    def test_speedtest_times_invalid_falls_back_to_default(self, env_clean, monkeypatch):
        monkeypatch.setenv("SPEEDTEST_TIMES", "not,valid")
        cfg = load_config()
        # Invalid → should return the hardcoded default (all 24 hours)
        assert len(cfg.speedtest_times) == 24

    def test_cdn_targets_csv(self, env_clean, monkeypatch):
        monkeypatch.setenv("CDN_TARGETS", "cachefly,fastly")
        cfg = load_config()
        assert cfg.cdn_targets == ("cachefly", "fastly")

    def test_ssh_trust_mode(self, env_clean, monkeypatch):
        monkeypatch.setenv("SSH_TRUST_MODE", "strict")
        cfg = load_config()
        assert cfg.ssh_trust_mode == "strict"

    def test_data_dir_from_env(self, env_clean, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        cfg = load_config()
        assert cfg.data_dir == tmp_path

    def test_peak_start_end(self, env_clean, monkeypatch):
        monkeypatch.setenv("PEAK_START", "20")
        monkeypatch.setenv("PEAK_END", "22")
        cfg = load_config()
        assert cfg.peak_start == 20
        assert cfg.peak_end == 22


class TestConfigEnvFile:
    def test_env_file_overrides_defaults(self, env_clean, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("ROUTER_HOST=10.0.0.1\nROUTER_PORT=443\nUSE_SSL=true\n")
        cfg = load_config(env_file=env_file)
        assert cfg.router_host == "10.0.0.1"
        assert cfg.router_port == 443
        assert cfg.use_ssl is True

    def test_missing_env_file_raises(self, env_clean):
        with pytest.raises(FileNotFoundError):
            load_config(env_file="/nonexistent/path/.env")


class TestEnsureDirs:
    def test_creates_data_dir(self, env_clean, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "newdir"))
        cfg = load_config()
        cfg.ensure_dirs()
        assert cfg.data_dir.exists()


class TestConfigDirect:
    def test_config_direct_construction(self):
        cfg = Config(router_backend="merlin", router_host="192.168.1.1")
        assert cfg.router_backend == "merlin"
        assert cfg.router_host == "192.168.1.1"
