"""Compatibility shim — use :class:`AsusWrtBackend` instead.

``MerlinBackend`` remains as an alias of :class:`~asusroutercontrol.backends.asuswrt.AsusWrtBackend`
with ``flavor="merlin"`` for one release cycle.
"""

from __future__ import annotations

from asusroutercontrol.backends.asuswrt import AsusWrtBackend


class MerlinBackend(AsusWrtBackend):
    """Deprecated alias for :class:`AsusWrtBackend` (Merlin flavor)."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("flavor", "merlin")
        super().__init__(*args, **kwargs)


__all__ = ["AsusWrtBackend", "MerlinBackend"]
