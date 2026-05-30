"""Non-secret configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_SPEEDTEST_TIMES = tuple(range(24))
_DEFAULT_CDN_TARGETS = ("cachefly", "cloudfront", "fastly")


@dataclass(frozen=True)
class Config:
    router_backend: str = "merlin"  # merlin | freshtomato
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
    speedtest_times: tuple[int, ...] = _DEFAULT_SPEEDTEST_TIMES  # local-hour triggers
    cdn_targets: tuple[str, ...] = _DEFAULT_CDN_TARGETS  # download CDN comparators
    peak_start: int = 18  # 6 PM
    peak_end: int = 23    # 11 PM
    probe_interval: int = 1800   # 30 min
    client_traffic_interval: int = 60  # 1 min
    poll_interval: int = 300     # 5 min
    notify_on_speedtest: bool = True  # notify when scheduled speed test completes

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


def _parse_str_tuple(val: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse comma-separated strings from env var."""
    if not val:
        return default
    items = tuple(x.strip().lower() for x in val.split(",") if x.strip())
    return items or default

def _resolve_env_file(explicit_env_file: str | Path | None) -> Path | None:
    """Resolve optional env-file override from arg or environment variable."""
    candidate = explicit_env_file
    if candidate is None:
        env_override = os.environ.get("ASUSROUTERCONTROL_ENV_FILE", "").strip()
        candidate = env_override or None
    if candidate is None:
        return None
    return Path(candidate).expanduser()


def load_config(env_file: str | Path | None = None) -> Config:
    dotenv_path = _resolve_env_file(env_file)
    if dotenv_path is None:
        load_dotenv()
    else:
        if not dotenv_path.exists():
            raise FileNotFoundError(f"Config env file not found: {dotenv_path}")
        load_dotenv(dotenv_path=str(dotenv_path))
    load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", "~/.asusroutercontrol")).expanduser()
    return Config(
        router_backend=os.environ.get("ROUTER_BACKEND", "merlin").strip().lower(),
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
        speedtest_times=_parse_int_tuple(
            os.environ.get("SPEEDTEST_TIMES", ""),
            _DEFAULT_SPEEDTEST_TIMES,
        ),
        cdn_targets=_parse_str_tuple(
            os.environ.get("CDN_TARGETS", ""),
            _DEFAULT_CDN_TARGETS,
        ),
        peak_start=int(os.environ.get("PEAK_START", "18")),
        peak_end=int(os.environ.get("PEAK_END", "23")),
        probe_interval=int(os.environ.get("PROBE_INTERVAL", "1800")),
        client_traffic_interval=int(os.environ.get("CLIENT_TRAFFIC_INTERVAL", "60")),
        poll_interval=int(os.environ.get("POLL_INTERVAL", "300")),
        notify_on_speedtest=os.environ.get(
            "NOTIFY_ON_SPEEDTEST", "true"
        ).lower() in ("true", "1", "yes"),
    )
