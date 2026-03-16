from __future__ import annotations

import pytest

from asusroutercontrol.backends.base import BackendOperationUnsupported
from asusroutercontrol.backends.freshtomato import FreshTomatoBackend


def test_parse_arp_table_extracts_devices() -> None:
    raw = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.10      0x1         0x2         AA:BB:CC:DD:EE:01     *        br0
192.168.1.11      0x1         0x2         AA:BB:CC:DD:EE:02     *        vlan1
"""
    rows = FreshTomatoBackend._parse_arp_table(raw)
    assert len(rows) == 2
    assert rows[0].mac == "aa:bb:cc:dd:ee:01"
    assert rows[1].ip == "192.168.1.11"


def test_parse_net_dev_sums_non_loopback_interfaces() -> None:
    raw = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|"
        "bytes packets errs drop fifo colls carrier compressed\n"
        "lo: 10 0 0 0 0 0 0 0 10 0 0 0 0 0 0 0\n"
        "eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n"
        "br0: 500 0 0 0 0 0 0 0 800 0 0 0 0 0 0 0\n"
    )
    rx, tx = FreshTomatoBackend._parse_net_dev(raw)
    assert rx == 1500
    assert tx == 2800


@pytest.mark.asyncio
async def test_write_ops_raise_unsupported() -> None:
    backend = FreshTomatoBackend(hostname="h", username="u", password="p")
    with pytest.raises(BackendOperationUnsupported):
        await backend.set_state("reboot")
    with pytest.raises(BackendOperationUnsupported):
        await backend.set_port_forwarding([])
