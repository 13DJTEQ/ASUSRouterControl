from __future__ import annotations

import pytest

from asusroutercontrol.probes import probe_wired_clients


class _Result:
    def __init__(self, *, ok: bool, stdout: str) -> None:
        self.ok = ok
        self.stdout = stdout


class _FakeSSH:
    def __init__(self, responses: dict[str, _Result]) -> None:
        self._responses = responses

    async def run(self, cmd: str) -> _Result:
        return self._responses.get(cmd, _Result(ok=False, stdout=""))


@pytest.mark.asyncio
async def test_probe_wired_clients_uses_bridge_ports_to_exclude_wifi() -> None:
    bridge_out = """port no\tmac addr\t\tis local?\tageing timer
  1\t00:05:cd:d4:a5:3c\tno\t\t  12.37
  1\t00:3e:e1:c9:2c:0b\tno\t\t   0.04
  2\t40:ed:00:ac:96:db\tno\t\t   3.51
  3\t74:1b:b2:f1:c4:31\tno\t\t  16.19
"""
    neigh_out = """192.168.1.241 lladdr 00:05:cd:d4:a5:3c STALE
192.168.1.242 lladdr 00:3e:e1:c9:2c:0b REACHABLE
192.168.1.219 lladdr fe:74:81:88:34:cf REACHABLE
192.168.1.129 dev br0 lladdr c8:d0:83:df:be:af STALE
76.94.96.1 dev eth0 lladdr 00:01:5c:65:e2:46 STALE
"""
    ssh = _FakeSSH({
        "brctl showmacs br0 2>/dev/null": _Result(ok=True, stdout=bridge_out),
        "ip neigh show dev br0 2>/dev/null || ip neigh show 2>/dev/null": _Result(
            ok=True, stdout=neigh_out
        ),
    })

    rows = await probe_wired_clients(
        ssh,
        wifi_macs={"40:ed:00:ac:96:db", "74:1b:b2:f1:c4:31", "fe:74:81:88:34:cf"},
    )
    by_mac = {row["mac"]: row for row in rows}

    assert set(by_mac) == {
        "00:05:CD:D4:A5:3C",
        "00:3E:E1:C9:2C:0B",
        "C8:D0:83:DF:BE:AF",
    }
    assert by_mac["00:05:CD:D4:A5:3C"]["bridge_port"] == 1
    assert by_mac["00:3E:E1:C9:2C:0B"]["ip"] == "192.168.1.242"


@pytest.mark.asyncio
async def test_probe_wired_clients_falls_back_to_neighbor_table_when_bridge_missing() -> None:
    neigh_out = """192.168.1.130 lladdr 4a:17:49:20:1a:84 STALE
192.168.1.242 lladdr 00:3e:e1:c9:2c:0b REACHABLE
192.168.1.241 lladdr 00:05:cd:d4:a5:3c FAILED
"""
    ssh = _FakeSSH({
        "brctl showmacs br0 2>/dev/null": _Result(ok=False, stdout=""),
        "ip neigh show dev br0 2>/dev/null || ip neigh show 2>/dev/null": _Result(
            ok=True, stdout=neigh_out
        ),
    })

    rows = await probe_wired_clients(ssh, wifi_macs={"4a:17:49:20:1a:84"})
    assert [row["mac"] for row in rows] == ["00:3E:E1:C9:2C:0B"]
