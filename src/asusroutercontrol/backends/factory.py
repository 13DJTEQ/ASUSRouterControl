"""Backend factory — selects and constructs a FirmwareBackend from config.

Usage::

    from asusroutercontrol.backends.factory import create_backend
    backend = create_backend(cfg, username=username, password=password)

The backend implementation is chosen from ``cfg.router_backend``
(env var ``ROUTER_BACKEND``).  Supported values: ``merlin``, ``freshtomato``.
"""

from __future__ import annotations

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.config import Config


class UnknownBackendError(ValueError):
    """Raised when ROUTER_BACKEND names an unrecognised firmware backend."""


def create_backend(
    cfg: Config,
    *,
    username: str,
    password: str,
) -> FirmwareBackend:
    """Construct the appropriate FirmwareBackend for *cfg*.

    Args:
        cfg: Loaded :class:`~asusroutercontrol.config.Config` instance.
        username: Router login username (sourced from Keychain by the caller).
        password: Router login password (sourced from Keychain by the caller).

    Returns:
        A concrete :class:`~asusroutercontrol.backends.base.FirmwareBackend`
        that has **not** yet been connected.  Call ``await backend.connect()``
        before using it.

    Raises:
        UnknownBackendError: If ``cfg.router_backend`` is not recognised.
    """
    kind = (cfg.router_backend or "merlin").strip().lower()

    if kind == "merlin":
        from asusroutercontrol.backends.merlin import MerlinBackend

        return MerlinBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            use_ssl=cfg.use_ssl,
            port=cfg.router_port,
        )

    if kind == "freshtomato":
        from asusroutercontrol.backends.freshtomato import FreshTomatoBackend

        return FreshTomatoBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            ssh_port=cfg.ssh_port,
        )

    known = ", ".join(sorted(["merlin", "freshtomato"]))
    raise UnknownBackendError(
        f"Unknown ROUTER_BACKEND={kind!r}. Known backends: {known}"
    )
