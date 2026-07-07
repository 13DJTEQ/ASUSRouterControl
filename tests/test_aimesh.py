"""Tests for AiMesh data collection and parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from asusroutercontrol.aimesh import (
    _classify_backhaul,
    _normalize_mac,
    _parse_aimesh_node,
    _parse_connection_type,
    collect_aimesh_topology,
    parse_aimesh_data,
)
from asusroutercontrol.models import AiMeshNode

# --- Helper function tests ---


def test_normalize_mac_colons() -> None:
    assert _normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_dashes() -> None:
    assert _normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_whitespace() -> None:
    assert _normalize_mac("  AA:BB:CC:DD:EE:FF  ") == "aa:bb:cc:dd:ee:ff"


def test_parse_connection_type_wired_string() -> None:
    assert _parse_connection_type({"connectionType": "wired"}) == "wired"


def test_parse_connection_type_wireless_string() -> None:
    assert _parse_connection_type({"connectionType": "wireless"}) == "wireless"


def test_parse_connection_type_numeric_wired() -> None:
    assert _parse_connection_type({"connectionType": "1"}) == "wired"


def test_parse_connection_type_numeric_wireless() -> None:
    assert _parse_connection_type({"connectionType": "2"}) == "wireless"


def test_parse_connection_type_fallback_rssi() -> None:
    assert _parse_connection_type({"rssi": -45}) == "wireless"


def test_parse_connection_type_none() -> None:
    assert _parse_connection_type({}) is None


def test_parse_aimesh_node_basic() -> None:
    info = {
        "ip": "192.168.1.2",
        "name": "Node-Bedroom",
        "model": "RT-AX86U",
        "fwVer": "3.0.0.4.388.22",
        "online": True,
        "parentMac": "AA:BB:CC:DD:EE:01",
        "connectionType": "wired",
        "clientCount": 5,
    }
    node = _parse_aimesh_node("AA:BB:CC:DD:EE:02", info, is_router=False)
    assert node.mac == "aa:bb:cc:dd:ee:02"
    assert node.ip == "192.168.1.2"
    assert node.hostname == "Node-Bedroom"
    assert node.model == "RT-AX86U"
    assert node.firmware == "3.0.0.4.388.22"
    assert node.is_online is True
    assert node.is_router is False
    assert node.connection_type == "wired"
    assert node.parent_mac == "aa:bb:cc:dd:ee:01"
    assert node.client_count == 5


def test_parse_aimesh_node_router() -> None:
    info = {
        "ip": "192.168.1.1",
        "name": "Main-Router",
        "model": "GT-AXE16000",
    }
    node = _parse_aimesh_node("AA:BB:CC:DD:EE:01", info, is_router=True)
    assert node.is_router is True
    assert node.parent_mac is None


def test_parse_aimesh_node_with_cpu_ram() -> None:
    info = {
        "cpu_usage": "23.5",
        "ram_usage": "67.2",
        "temperature": "52",
    }
    node = _parse_aimesh_node("AA:BB:CC:DD:EE:03", info)
    assert node.cpu_pct == 23.5
    assert node.ram_pct == 67.2
    assert node.temperature_c == 52.0


def test_parse_aimesh_node_traffic_bytes() -> None:
    info = {
        "rxBytes": 1000000,
        "txBytes": 500000,
    }
    node = _parse_aimesh_node("AA:BB:CC:DD:EE:04", info)
    assert node.rx_bytes == 1000000
    assert node.tx_bytes == 500000


# --- Backhaul classification tests ---


def test_classify_backhaul_all_wired() -> None:
    nodes = [
        AiMeshNode(mac="aa:01", is_router=True),
        AiMeshNode(mac="aa:02", connection_type="wired"),
        AiMeshNode(mac="aa:03", connection_type="wired"),
    ]
    assert _classify_backhaul(nodes) == "wired"


def test_classify_backhaul_all_wireless() -> None:
    nodes = [
        AiMeshNode(mac="aa:01", is_router=True),
        AiMeshNode(mac="aa:02", connection_type="wireless"),
    ]
    assert _classify_backhaul(nodes) == "wireless"


def test_classify_backhaul_mixed() -> None:
    nodes = [
        AiMeshNode(mac="aa:01", is_router=True),
        AiMeshNode(mac="aa:02", connection_type="wired"),
        AiMeshNode(mac="aa:03", connection_type="wireless"),
    ]
    assert _classify_backhaul(nodes) == "mixed"


def test_classify_backhaul_single_router() -> None:
    nodes = [AiMeshNode(mac="aa:01", is_router=True)]
    assert _classify_backhaul(nodes) is None


def test_classify_backhaul_empty() -> None:
    assert _classify_backhaul([]) is None


# --- Full parsing tests ---


def test_parse_aimesh_data_empty() -> None:
    topo = parse_aimesh_data(None)
    assert topo.node_count == 0
    assert topo.online_count == 0
    assert topo.nodes == []


def test_parse_aimesh_data_single_router() -> None:
    raw = {
        "AA:BB:CC:DD:EE:01": {
            "ip": "192.168.1.1",
            "name": "Main-Router",
            "model": "GT-AXE16000",
            "online": True,
        }
    }
    topo = parse_aimesh_data(raw)
    assert topo.node_count == 1
    assert topo.online_count == 1
    assert topo.nodes[0].is_router is True
    assert topo.backhaul_type is None


def test_parse_aimesh_data_multi_node() -> None:
    raw = {
        "AA:BB:CC:DD:EE:01": {
            "ip": "192.168.1.1",
            "name": "Main-Router",
            "online": True,
        },
        "AA:BB:CC:DD:EE:02": {
            "ip": "192.168.1.2",
            "name": "Node-Bedroom",
            "parentMac": "AA:BB:CC:DD:EE:01",
            "connectionType": "wired",
            "online": True,
            "clientCount": 3,
        },
        "AA:BB:CC:DD:EE:03": {
            "ip": "192.168.1.3",
            "name": "Node-Office",
            "parentMac": "AA:BB:CC:DD:EE:01",
            "connectionType": "wireless",
            "rssi": -55,
            "online": True,
        },
    }
    topo = parse_aimesh_data(raw)
    assert topo.node_count == 3
    assert topo.online_count == 3
    assert topo.backhaul_type == "mixed"

    router = next(n for n in topo.nodes if n.is_router)
    assert router.mac == "aa:bb:cc:dd:ee:01"

    bedroom = next(n for n in topo.nodes if n.hostname == "Node-Bedroom")
    assert bedroom.connection_type == "wired"
    assert bedroom.parent_mac == "aa:bb:cc:dd:ee:01"
    assert bedroom.client_count == 3


def test_parse_aimesh_data_with_node_info_enrichment() -> None:
    aimesh_raw = {
        "AA:BB:CC:DD:EE:01": {"ip": "192.168.1.1", "name": "Router"},
        "AA:BB:CC:DD:EE:02": {
            "ip": "192.168.1.2",
            "name": "Node",
            "parentMac": "AA:BB:CC:DD:EE:01",
        },
    }
    node_info_raw = {
        "AA:BB:CC:DD:EE:02": {
            "cpu_usage": "15.0",
            "ram_usage": "42.0",
            "temperature": "48",
            "model": "RT-AX58U",
        },
    }
    topo = parse_aimesh_data(aimesh_raw, node_info_raw)
    node2 = next(n for n in topo.nodes if n.mac == "aa:bb:cc:dd:ee:02")
    assert node2.cpu_pct == 15.0
    assert node2.ram_pct == 42.0
    assert node2.temperature_c == 48.0
    assert node2.model == "RT-AX58U"


def test_parse_aimesh_data_node_info_fallback() -> None:
    """When AIMESH is empty/None but NODE_INFO has data, use NODE_INFO."""
    node_info_raw = {
        "AA:BB:CC:DD:EE:01": {"ip": "192.168.1.1", "name": "Router"},
    }
    topo = parse_aimesh_data(None, node_info_raw)
    assert topo.node_count == 1
    assert topo.nodes[0].hostname == "Router"


def test_parse_aimesh_data_offline_node() -> None:
    raw = {
        "AA:BB:CC:DD:EE:01": {"ip": "192.168.1.1", "online": True},
        "AA:BB:CC:DD:EE:02": {
            "ip": "192.168.1.2",
            "parentMac": "AA:BB:CC:DD:EE:01",
            "online": False,
        },
    }
    topo = parse_aimesh_data(raw)
    assert topo.node_count == 2
    assert topo.online_count == 1


# --- Collection tests ---


@pytest.mark.asyncio
async def test_collect_aimesh_topology_no_router() -> None:
    """Backend without _router attribute returns empty topology."""
    backend = MagicMock()
    del backend._router  # ensure no _router
    backend._router = None

    topo = await collect_aimesh_topology(backend)
    assert topo.node_count == 0


@pytest.mark.asyncio
async def test_collect_aimesh_topology_with_data() -> None:
    """Backend with router fetches and parses data."""
    from asusrouter import AsusData

    aimesh_data = {
        "AA:BB:CC:DD:EE:01": {"ip": "192.168.1.1", "name": "Router"},
    }

    async def _mock_get_data(dtype, **kwargs):
        if dtype == AsusData.AIMESH:
            return aimesh_data
        return None

    mock_router = AsyncMock()
    mock_router.async_get_data = _mock_get_data

    backend = MagicMock()
    backend._router = mock_router

    topo = await collect_aimesh_topology(backend)
    assert topo.node_count == 1
    assert topo.nodes[0].hostname == "Router"


@pytest.mark.asyncio
async def test_collect_aimesh_topology_api_error() -> None:
    """Graceful handling when API calls fail."""
    import aiohttp

    mock_router = AsyncMock()
    mock_router.async_get_data = AsyncMock(
        side_effect=aiohttp.ClientError("connection refused")
    )

    backend = MagicMock()
    backend._router = mock_router

    topo = await collect_aimesh_topology(backend)
    assert topo.node_count == 0
