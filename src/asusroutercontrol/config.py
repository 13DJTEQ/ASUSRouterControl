"""Non-secret configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    router_host: str = "router.asus.com"
    router_port: int = 80
    use_ssl: bool = False
    polling_interval: int = 60
    data_dir: Path = field(default_factory=lambda: Path.home() / ".asusroutercontrol")
    ssh_port: int = 1313
    ssh_trust_mode: str = "tofu_confirm"  # strict | tofu_confirm | tofu_auto
    ssh_host_key_fingerprint: str | None = None  # e.g. SHA256:...
    ssh_known_hosts_path: Path | None = None
    soundshield_export_path: Path = field(
        default_factory=lambda: Path.home() / ".asusroutercontrol" / "soundshield_network.json"
    )

    # Scheduler settings
    speedtest_times: tuple[int, ...] = (6, 14, 22)  # local-hour triggers
    peak_start: int = 18  # 6 PM
    peak_end: int = 23    # 11 PM
    probe_interval: int = 1800   # 30 min
    poll_interval: int = 300     # 5 min

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _parse_int_tuple(val: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """Parse comma-separated ints from env var."""
    if not val:
        return default
    try:
        return tuple(int(x.strip()) for x in val.split(","))
    except ValueError:
        return default


def load_config() -> Config:
    load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", "~/.asusroutercontrol")).expanduser()
    return Config(
        router_host=os.environ.get("ROUTER_HOST", "router.asus.com"),
        router_port=int(os.environ.get("ROUTER_PORT", "80")),
        use_ssl=os.environ.get("USE_SSL", "false").lower() in ("true", "1", "yes"),
        polling_interval=int(os.environ.get("POLLING_INTERVAL", "60")),
        data_dir=data_dir,
        ssh_port=int(os.environ.get("SSH_PORT", "1313")),
        ssh_trust_mode=os.environ.get("SSH_TRUST_MODE", "tofu_confirm").strip().lower(),
        ssh_host_key_fingerprint=(
            os.environ.get("SSH_HOST_KEY_FINGERPRINT", "").strip() or None
        ),
        ssh_known_hosts_path=(
            Path(os.environ["SSH_KNOWN_HOSTS_PATH"]).expanduser()
            if os.environ.get("SSH_KNOWN_HOSTS_PATH")
            else None
        ),
        soundshield_export_path=Path(
            os.environ.get("SOUNDSHIELD_EXPORT_PATH", str(data_dir / "soundshield_network.json"))
        ).expanduser(),
        speedtest_times=_parse_int_tuple(os.environ.get("SPEEDTEST_TIMES", ""), (6, 14, 22)),
        peak_start=int(os.environ.get("PEAK_START", "18")),
        peak_end=int(os.environ.get("PEAK_END", "23")),
        probe_interval=int(os.environ.get("PROBE_INTERVAL", "1800")),
        poll_interval=int(os.environ.get("POLL_INTERVAL", "300")),
    )
