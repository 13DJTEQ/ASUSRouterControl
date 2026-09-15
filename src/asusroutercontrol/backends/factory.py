"""Backend factory — selects and constructs a FirmwareBackend from config.

Usage::

    from asusroutercontrol.backends.factory import create_backend
    backend = create_backend(cfg, username=username, password=password)

The backend implementation is chosen from ``cfg.router_backend``
(env var ``ROUTER_BACKEND``).

Supported values:
- ``merlin`` — AsusWrtBackend(flavor=\"merlin\")
- ``stock`` / ``asuswrt`` — AsusWrtBackend(flavor=\"stock\")
- ``freshtomato`` — hard-fail (deferred; stub retained in-tree)
"""

from __future__ import annotations

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.config import Config


class UnknownBackendError(ValueError):
    """Raised when ROUTER_BACKEND names an unrecognised firmware backend."""


class BackendDeferredError(RuntimeError):
    """Raised when a backend is intentionally not selectable yet."""


def create_backend(
    cfg: Config,
    *,
    username: str,
    password: str,
) -> FirmwareBackend:
    """Construct the appropriate FirmwareBackend for *cfg*.

    Args:
        cfg: Loaded :class:`~asusroutercontrol.config.Config` instance.
        username: Router login username (sourced by the caller).
        password: Router login password (sourced by the caller).

    Returns:
        A concrete :class:`~asusroutercontrol.backends.base.FirmwareBackend`
        that has **not** yet been connected.  Call ``await backend.connect()``
        before using it.

    Raises:
        UnknownBackendError: If ``cfg.router_backend`` is not recognised.
        BackendDeferredError: If FreshTomato is selected (not supported yet).
    """
    kind = (cfg.router_backend or "merlin").strip().lower()

    if kind in {"merlin", "stock", "asuswrt"}:
        from asusroutercontrol.backends.asuswrt import AsusWrtBackend

        flavor = "merlin" if kind == "merlin" else "stock"
        return AsusWrtBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            use_ssl=cfg.use_ssl,
            port=cfg.router_port,
            flavor=flavor,
        )

    if kind == "freshtomato":
        raise BackendDeferredError(
            "FreshTomato backend is deferred and not selectable. "
            "Use ROUTER_BACKEND=stock, asuswrt, or merlin. "
            "The stub remains in asusroutercontrol.backends.freshtomato for later work."
        )

    known = ", ".join(sorted(["asuswrt", "freshtomato (deferred)", "merlin", "stock"]))
    raise UnknownBackendError(
        f"Unknown ROUTER_BACKEND={kind!r}. Known backends: {known}"
    )
