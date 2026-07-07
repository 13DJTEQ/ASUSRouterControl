"""AiMesh mesh network monitoring — data collection via asusrouter library."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
from asusrouter import AsusData

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.models import AiMeshNode, AiMeshTopology

log = logging.getLogger(__name__)


def _safe_float(value: object) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    """Convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_mac(mac: str) -> str:
    """Normalize a MAC address to lowercase with colons."""
    return mac.strip().replace("-", ":").lower()


def _parse_aimesh_node(
    mac: str,
    info: dict,
    *,
    is_router: bool = False,
) -> AiMeshNode:
    """Parse a single AiMesh node from raw asusrouter data."""
    return AiMeshNode(
        mac=_normalize_mac(mac),
        ip=info.get("ip"),
        hostname=info.get("name") or info.get("hostname"),
        model=info.get("model"),
        firmware=info.get("fwVer") or info.get("firmware"),
        is_online=bool(info.get("online", True)),
        is_router=is_router,
        connection_type=_parse_connection_type(info),
        parent_mac=_normalize_mac(info["parentMac"]) if info.get("parentMac") else None,
        rssi=_safe_int(info.get("rssi")),
        cpu_pct=_safe_float(info.get("cpu_usage") or info.get("cpu")),
        ram_pct=_safe_float(info.get("ram_usage") or info.get("ram")),
        temperature_c=_safe_float(info.get("temperature")),
        client_count=int(info.get("clientCount", 0) or 0),
        rx_bytes=_safe_int(info.get("rxBytes") or info.get("rx_bytes")),
        tx_bytes=_safe_int(info.get("txBytes") or info.get("tx_bytes")),
    )


def _parse_connection_type(info: dict) -> str | None:
    """Determine backhaul connection type from node info."""
    conn = info.get("connectionType") or info.get("connection_type")
    if conn:
        conn_str = str(conn).lower()
        if conn_str in ("wireless", "2"):
            return "wireless"
        if conn_str in ("wired", "1"):
            return "wired"
        return conn_str

    # Fallback: infer from rssi (wireless nodes report RSSI)
    if info.get("rssi") is not None:
        return "wireless"
    return None


def _classify_backhaul(nodes: list[AiMeshNode]) -> str | None:
    """Classify the overall backhaul type from node connection types."""
    types = {n.connection_type for n in nodes if not n.is_router and n.connection_type}
    if not types:
        return None
    if types == {"wired"}:
        return "wired"
    if types == {"wireless"}:
        return "wireless"
    return "mixed"


def parse_aimesh_data(
    aimesh_raw: object,
    node_info_raw: object | None = None,
) -> AiMeshTopology:
    """Parse raw AsusData.AIMESH and AsusData.NODE_INFO into AiMeshTopology.

    Args:
        aimesh_raw: Raw data from router.async_get_data(AsusData.AIMESH).
        node_info_raw: Optional raw data from router.async_get_data(AsusData.NODE_INFO).

    Returns:
        Populated AiMeshTopology with parsed nodes.
    """
    now = datetime.utcnow()
    nodes: list[AiMeshNode] = []

    # Build a lookup from node_info for enrichment
    node_info_map: dict[str, dict] = {}
    if node_info_raw and isinstance(node_info_raw, dict):
        for mac, info in node_info_raw.items():
            if isinstance(info, dict):
                node_info_map[_normalize_mac(mac)] = info

    # Parse AIMESH data — typically a dict keyed by MAC
    if aimesh_raw and isinstance(aimesh_raw, dict):
        for mac, info in aimesh_raw.items():
            if not isinstance(info, dict):
                continue
            norm_mac = _normalize_mac(mac)

            # Enrich with NODE_INFO data if available
            enriched = dict(info)
            node_extra = node_info_map.get(norm_mac)
            if node_extra:
                for key in ("cpu_usage", "ram_usage", "temperature", "model", "fwVer"):
                    if key in node_extra and key not in enriched:
                        enriched[key] = node_extra[key]

            # The router node (primary) typically has no parent or type=0
            is_router = not bool(info.get("parentMac") or info.get("parent_mac"))
            node = _parse_aimesh_node(norm_mac, enriched, is_router=is_router)
            nodes.append(node)

    # If no AIMESH data but NODE_INFO is available, use that
    if not nodes and node_info_raw and isinstance(node_info_raw, dict):
        for mac, info in node_info_raw.items():
            if not isinstance(info, dict):
                continue
            norm_mac = _normalize_mac(mac)
            is_router = not bool(info.get("parentMac") or info.get("parent_mac"))
            node = _parse_aimesh_node(norm_mac, info, is_router=is_router)
            nodes.append(node)

    online_count = sum(1 for n in nodes if n.is_online)
    backhaul = _classify_backhaul(nodes)

    return AiMeshTopology(
        timestamp=now,
        nodes=nodes,
        node_count=len(nodes),
        online_count=online_count,
        backhaul_type=backhaul,
    )


async def collect_aimesh_topology(backend: FirmwareBackend) -> AiMeshTopology:
    """Collect AiMesh topology from a connected backend.

    Uses the asusrouter library's AsusData.AIMESH and AsusData.NODE_INFO
    endpoints to gather mesh network data.

    Args:
        backend: A connected FirmwareBackend (typically MerlinBackend).

    Returns:
        Parsed AiMeshTopology. Returns empty topology on failure.
    """
    router = getattr(backend, "_router", None)
    if router is None:
        log.warning("Backend does not expose _router; AiMesh data unavailable")
        return AiMeshTopology(timestamp=datetime.utcnow())

    aimesh_raw = None
    node_info_raw = None

    try:
        aimesh_raw = await router.async_get_data(AsusData.AIMESH)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        log.warning("Failed to fetch AiMesh data", exc_info=True)

    try:
        node_info_raw = await router.async_get_data(AsusData.NODE_INFO)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        log.debug("Failed to fetch NODE_INFO data", exc_info=True)

    return parse_aimesh_data(aimesh_raw, node_info_raw)
