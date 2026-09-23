"""Lightweight LAN discovery helpers for customer router setup.

No wide port scanning — only well-known ASUS hostnames plus the default gateway.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

log = logging.getLogger(__name__)

_DEFAULT_CANDIDATES = (
    "router.asus.com",
    "www.asusrouter.com",
)


@dataclass(frozen=True)
class DiscoveryCandidate:
    host: str
    source: str  # gateway | well-known
    reachable: bool | None = None


def default_gateway_ipv4() -> str | None:
    """Best-effort IPv4 default gateway via a UDP connect trick (no packets sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            local_ip = sock.getsockname()[0]
    except OSError as exc:
        log.debug("Could not infer local IP for gateway discovery: %s", exc)
        return None

    parts = local_ip.split(".")
    if len(parts) != 4:
        return None
    # Common home-router convention: gateway is .1 on the local /24.
    return ".".join([*parts[:3], "1"])


def probe_tcp(host: str, port: int, *, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_router_candidates(
    *,
    http_port: int = 80,
    https_port: int = 8443,
    include_gateway: bool = True,
    probe: bool = True,
    timeout: float = 1.5,
) -> list[DiscoveryCandidate]:
    """Return ordered connection candidates for the setup wizard.

    Reachability probes both HTTP and HTTPS admin ports (80 + 8443 by default)
    so HTTPS-only routers are not ranked as unreachable.
    """
    ordered: list[DiscoveryCandidate] = []
    seen: set[str] = set()

    def _reachable(host: str) -> bool | None:
        if not probe:
            return None
        if probe_tcp(host, http_port, timeout=timeout):
            return True
        if https_port and https_port != http_port:
            if probe_tcp(host, https_port, timeout=timeout):
                return True
        return False

    def _add(host: str, source: str) -> None:
        key = host.strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        ordered.append(
            DiscoveryCandidate(host=host, source=source, reachable=_reachable(host))
        )

    for host in _DEFAULT_CANDIDATES:
        _add(host, "well-known")

    if include_gateway:
        gw = default_gateway_ipv4()
        if gw:
            _add(gw, "gateway")

    ordered.sort(
        key=lambda c: (c.reachable is not True, c.source != "well-known", c.host)
    )
    return ordered


def pick_default_host(candidates: list[DiscoveryCandidate] | None = None) -> str:
    found = candidates if candidates is not None else discover_router_candidates()
    for candidate in found:
        if candidate.reachable:
            return candidate.host
    return found[0].host if found else "router.asus.com"
