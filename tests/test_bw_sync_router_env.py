"""Tests for scripts/bw_sync_router_env.sh non-interactive unlock policy."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bw_sync_router_env.sh"


def _write_fake_bw(bin_dir: Path, status_json: str) -> None:
    bw = bin_dir / "bw"
    bw.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "status" ]]; then\n'
        f"  echo '{status_json}'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "unlock" ]]; then\n'
        '  echo "INTERACTIVE_UNLOCK_CALLED" >&2\n'
        "  exit 99\n"
        "fi\n"
        'echo "unexpected bw args: $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    bw.chmod(bw.stat().st_mode | stat.S_IEXEC)


def _write_fake_python(bin_dir: Path, exit_code: int, stderr: str = "", stdout: str = "") -> None:
    """Stub python that mimics _try_keychain_unlock outcomes via env."""
    py = bin_dir / "python"
    # The script prefers ROOT/.venv/bin/python — caller places this there.
    py.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "# Consume heredoc from stdin (bw_sync passes a Python program).\n"
        "cat >/dev/null\n"
        f'echo "{stderr}" >&2\n'
        f'printf "%s" "{stdout}"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    py.chmod(py.stat().st_mode | stat.S_IEXEC)


def test_bw_sync_missing_mp_no_interactive(tmp_path, monkeypatch):
    """Missing Keychain MP must not call interactive bw unlock."""
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    path_bin = tmp_path / "path"
    path_bin.mkdir()

    script_body = SCRIPT.read_text(encoding="utf-8")
    (scripts / "bw_sync_router_env.sh").write_text(script_body, encoding="utf-8")
    (scripts / "bw_sync_router_env.sh").chmod(
        (scripts / "bw_sync_router_env.sh").stat().st_mode | stat.S_IEXEC
    )

    _write_fake_bw(path_bin, '{"status":"locked"}')
    _write_fake_python(
        venv_bin,
        exit_code=2,
        stderr="No Bitwarden master password in Keychain.",
    )

    env = os.environ.copy()
    env["PATH"] = f"{path_bin}:{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path / "home")
    env["ASUSROUTERCONTROL_ENV_FILE"] = str(tmp_path / "home" / ".asusroutercontrol.dev" / ".env")
    env.pop("BW_SESSION", None)
    env.pop("BW_SYNC_ALLOW_PROMPT", None)

    proc = subprocess.run(
        ["bash", str(scripts / "bw_sync_router_env.sh")],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 2
    assert "INTERACTIVE_UNLOCK_CALLED" not in combined
    assert "bw-master --set" in combined
    assert "enter master password" not in combined.lower()
    assert "BW_SYNC_ALLOW_PROMPT" not in combined


def test_bw_sync_failed_unlock_surfaces_error(tmp_path):
    """Failed Keychain unlock must surface the error and skip interactive prompt."""
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    path_bin = tmp_path / "path"
    path_bin.mkdir()

    script_body = SCRIPT.read_text(encoding="utf-8")
    (scripts / "bw_sync_router_env.sh").write_text(script_body, encoding="utf-8")
    (scripts / "bw_sync_router_env.sh").chmod(
        (scripts / "bw_sync_router_env.sh").stat().st_mode | stat.S_IEXEC
    )

    _write_fake_bw(path_bin, '{"status":"locked"}')
    _write_fake_python(
        venv_bin,
        exit_code=1,
        stderr="Keychain auto-unlock failed: wrong master password in Keychain",
    )

    env = os.environ.copy()
    env["PATH"] = f"{path_bin}:{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path / "home")
    env["ASUSROUTERCONTROL_ENV_FILE"] = str(tmp_path / "home" / ".asusroutercontrol.dev" / ".env")
    env.pop("BW_SESSION", None)
    # Even if legacy env is set, script must not interactive-unlock.
    env["BW_SYNC_ALLOW_PROMPT"] = "1"

    proc = subprocess.run(
        ["bash", str(scripts / "bw_sync_router_env.sh")],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 1
    assert "INTERACTIVE_UNLOCK_CALLED" not in combined
    assert "wrong master password" in combined
    assert "bw-master --status" in combined
    assert "Interactive bw unlock is disabled" in combined


def test_bw_sync_script_has_no_interactive_unlock_path():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "_interactive_unlock" not in script
    assert "BW_SYNC_ALLOW_PROMPT" not in script
    assert "bw unlock --raw" not in script
    assert "enter master password" not in script.lower()
    assert "Keychain" in script


def test_bw_sync_mirror_failure_exits_nonzero():
    """Keychain mirror failure inside the sync Python block must fail the script."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "keychain mirror failed" in script
    assert "raise SystemExit(1)" in script
    assert "mirror_router_login_to_keychain" in script


def test_scheduler_pause_login_attempts_flag():
    from asusroutercontrol.config import Config
    from asusroutercontrol.scheduler import MonitorScheduler

    class _Store:
        pass

    sched = MonitorScheduler(_Store(), cfg=Config())  # type: ignore[arg-type]
    assert sched._login_paused is False
    sched.pause_login_attempts(True)
    assert sched._login_paused is True
    sched.pause_login_attempts(False)
    assert sched._login_paused is False
    sched.update_config(Config(router_host="192.168.50.1", router_port=8443, use_ssl=True))
    assert sched._cfg.router_port == 8443
    assert sched._cfg.use_ssl is True
