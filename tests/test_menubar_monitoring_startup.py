"""Menubar starts monitoring without waiting on the health check."""

from __future__ import annotations

from pathlib import Path


def test_menubar_starts_runtime_before_health_check_returns() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    launch = src.index("def applicationDidFinishLaunching_")
    health = src.index("def _startup_health_check")
    chunk = src[launch:health]
    assert "_ensure_runtime_started()" in chunk
    assert "health-check" in chunk
    assert chunk.index("_ensure_runtime_started()") < chunk.index("health-check")


def test_dev_launcher_exports_project_env_file() -> None:
    src = Path("scripts/build_macos_app.sh").read_text(encoding="utf-8")
    assert "ASUSROUTERCONTROL_ENV_FILE" in src
    assert "ASUSROUTERCONTROL_PROJECT_ROOT" in src


def test_scheduler_has_credentials_recovery_loop() -> None:
    src = Path("src/asusroutercontrol/scheduler.py").read_text(encoding="utf-8")
    assert "def _credentials_recovery_loop" in src
    assert 'capability == "degraded-no-credentials"' in src
    assert "credentials-recovery" in src
