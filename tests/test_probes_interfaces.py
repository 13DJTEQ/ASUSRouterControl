from __future__ import annotations

import pytest

from asusroutercontrol.probes import probe_client_traffic, probe_wifi


class _Result:
    def __init__(self, *, ok: bool, stdout: str) -> None:
        self.ok = ok
        self.stdout = stdout


class _FakeSSH:
    def __init__(self, responses: dict[str, _Result]) -> None:
        self._responses = responses
        self.commands: list[str] = []

    async def run(self, cmd: str) -> _Result:
        self.commands.append(cmd)
        return self._responses.get(cmd, _Result(ok=True, stdout=""))


@pytest.mark.asyncio
async def test_probe_client_traffic_discovers_primary_and_guest_interfaces() -> None:
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\.[0-9]+)?_ifname='"
    ssh = _FakeSSH({
        nvram_cmd: _Result(
            ok=True,
            stdout=(
                "wl0_ifname=eth1\n"
                "wl0.1_ifname=wl0.1\n"
                "wl1_ifname=eth2\n"
                "wl1.2_ifname=wl1.2\n"
            ),
        ),
        "wl -i eth1 assoclist 2>/dev/null": _Result(
            ok=True,
            stdout="assoclist AA:AA:AA:AA:AA:01\n",
        ),
        "wl -i wl0.1 assoclist 2>/dev/null": _Result(
            ok=True,
            stdout="assoclist AA:AA:AA:AA:AA:02\n",
        ),
        "wl -i eth2 assoclist 2>/dev/null": _Result(
            ok=True,
            stdout="assoclist BB:BB:BB:BB:BB:01\n",
        ),
        "wl -i wl1.2 assoclist 2>/dev/null": _Result(ok=True, stdout=""),
        "wl -i eth1 sta_info AA:AA:AA:AA:AA:01 2>/dev/null": _Result(
            ok=True,
            stdout=(
                "tx data bytes: 1000\n"
                "rx data bytes: 2000\n"
                "per antenna rssi of last rx data frame: -45 -47\n"
            ),
        ),
        "wl -i wl0.1 sta_info AA:AA:AA:AA:AA:02 2>/dev/null": _Result(
            ok=True,
            stdout=(
                "tx data bytes: 1100\n"
                "rx data bytes: 2100\n"
                "per antenna rssi of last rx data frame: -50 -52\n"
            ),
        ),
        "wl -i eth2 sta_info BB:BB:BB:BB:BB:01 2>/dev/null": _Result(
            ok=True,
            stdout=(
                "tx data bytes: 1200\n"
                "rx data bytes: 2200\n"
                "per antenna rssi of last rx data frame: -55 -57\n"
            ),
        ),
    })

    rows = await probe_client_traffic(ssh)
    by_mac = {row["mac"]: row for row in rows}

    assert by_mac["AA:AA:AA:AA:AA:01"]["band"] == "2.4GHz"
    assert by_mac["AA:AA:AA:AA:AA:02"]["band"] == "2.4GHz"
    assert by_mac["BB:BB:BB:BB:BB:01"]["band"] == "5GHz"
    assert any("wl -i wl0.1 assoclist" in cmd for cmd in ssh.commands)
    assert any("wl -i wl1.2 assoclist" in cmd for cmd in ssh.commands)


@pytest.mark.asyncio
async def test_probe_client_traffic_falls_back_to_eth_if_discovery_fails() -> None:
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\.[0-9]+)?_ifname='"
    ssh = _FakeSSH({
        nvram_cmd: _Result(ok=False, stdout=""),
        "wl -i eth1 assoclist 2>/dev/null": _Result(
            ok=True,
            stdout="assoclist AA:AA:AA:AA:AA:01\n",
        ),
        "wl -i eth1 sta_info AA:AA:AA:AA:AA:01 2>/dev/null": _Result(
            ok=True,
            stdout="tx data bytes: 1000\nrx data bytes: 2000\n",
        ),
        "wl -i eth2 assoclist 2>/dev/null": _Result(ok=True, stdout=""),
    })

    rows = await probe_client_traffic(ssh)
    assert len(rows) == 1
    assert rows[0]["band"] == "2.4GHz"
    assert any("wl -i eth1 assoclist" in cmd for cmd in ssh.commands)


@pytest.mark.asyncio
async def test_probe_wifi_aggregates_guest_interfaces_by_band() -> None:
    nvram_cmd = "nvram show 2>/dev/null | grep -E '^wl[0-9]+(\\.[0-9]+)?_ifname='"
    ssh = _FakeSSH({
        nvram_cmd: _Result(
            ok=True,
            stdout=(
                "wl0_ifname=eth1\n"
                "wl0.1_ifname=wl0.1\n"
                "wl1_ifname=eth2\n"
            ),
        ),
        "cat /proc/net/dev": _Result(
            ok=True,
            stdout=(
                "Inter-|   Receive                                                |  Transmit\n"
                " face |bytes    packets errs drop fifo frame compressed multicast"
                "|bytes    packets errs drop fifo colls carrier compressed\n"
                " eth1: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n"
                " wl0.1: 50 0 0 0 0 0 0 0 70 0 0 0 0 0 0 0\n"
                " eth2: 300 0 0 0 0 0 0 0 400 0 0 0 0 0 0 0\n"
                " vlan1: 500 0 0 0 0 0 0 0 600 0 0 0 0 0 0 0\n"
            ),
        ),
        "wl -i eth1 assoclist 2>/dev/null": _Result(
            ok=True, stdout="assoclist AA:AA:AA:AA:AA:01\n"
        ),
        "wl -i wl0.1 assoclist 2>/dev/null": _Result(
            ok=True, stdout="assoclist AA:AA:AA:AA:AA:02\n"
        ),
        "wl -i eth2 assoclist 2>/dev/null": _Result(
            ok=True, stdout="assoclist BB:BB:BB:BB:BB:01\n"
        ),
        "wl -i eth1 sta_info AA:AA:AA:AA:AA:01 2>/dev/null | grep 'per antenna rssi'": _Result(
            ok=True, stdout="per antenna rssi of last rx data frame: -45 -47\n"
        ),
        "wl -i wl0.1 sta_info AA:AA:AA:AA:AA:02 2>/dev/null | grep 'per antenna rssi'": _Result(
            ok=True, stdout="per antenna rssi of last rx data frame: -50 -52\n"
        ),
        "wl -i eth2 sta_info BB:BB:BB:BB:BB:01 2>/dev/null | grep 'per antenna rssi'": _Result(
            ok=True, stdout="per antenna rssi of last rx data frame: -55 -57\n"
        ),
        "wl -i eth1 noise 2>/dev/null": _Result(ok=True, stdout="-92\n"),
        "wl -i wl0.1 noise 2>/dev/null": _Result(ok=True, stdout="-91\n"),
        "wl -i eth2 noise 2>/dev/null": _Result(ok=True, stdout="-95\n"),
        "wl -i eth1 channel 2>/dev/null": _Result(ok=True, stdout="current mac channel 6\n"),
        "wl -i wl0.1 channel 2>/dev/null": _Result(ok=True, stdout="current mac channel 6\n"),
        "wl -i eth2 channel 2>/dev/null": _Result(ok=True, stdout="current mac channel 36\n"),
    })

    rows = await probe_wifi(ssh)
    by_band = {row.band: row for row in rows}

    assert by_band["2.4"].client_count == 2
    assert by_band["2.4"].rx_bytes == 150
    assert by_band["2.4"].tx_bytes == 270
    assert by_band["2.4"].channel == "6"
    assert by_band["5"].client_count == 1
    assert by_band["wired"].rx_bytes == 500
    assert by_band["wired"].tx_bytes == 600
