"""Backend factory helpers."""

from __future__ import annotations

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.backends.freshtomato import FreshTomatoBackend
from asusroutercontrol.backends.merlin import MerlinBackend
from asusroutercontrol.config import Config


def create_backend(
    cfg: Config,
    *,
    username: str,
    password: str,
) -> FirmwareBackend:
    backend = (cfg.router_backend or "merlin").strip().lower()
    if backend == "merlin":
        return MerlinBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            use_ssl=cfg.use_ssl,
            port=cfg.router_port,
        )
    if backend == "freshtomato":
        return FreshTomatoBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            ssh_port=cfg.ssh_port,
        )
    raise ValueError(
        f"Unsupported ROUTER_BACKEND `{cfg.router_backend}`; use `merlin` or `freshtomato`."
    )
