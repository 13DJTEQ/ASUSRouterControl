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
    soundshield_export_path: Path = field(
        default_factory=lambda: Path.home() / ".asusroutercontrol" / "soundshield_network.json"
    )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    load_dotenv()
    data_dir = Path(os.environ.get("DATA_DIR", "~/.asusroutercontrol")).expanduser()
    return Config(
        router_host=os.environ.get("ROUTER_HOST", "router.asus.com"),
        router_port=int(os.environ.get("ROUTER_PORT", "80")),
        use_ssl=os.environ.get("USE_SSL", "false").lower() in ("true", "1", "yes"),
        polling_interval=int(os.environ.get("POLLING_INTERVAL", "60")),
        data_dir=data_dir,
        soundshield_export_path=Path(
            os.environ.get("SOUNDSHIELD_EXPORT_PATH", str(data_dir / "soundshield_network.json"))
        ).expanduser(),
    )
