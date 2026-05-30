"""Tests for asusroutercontrol.analysis.clients."""

from __future__ import annotations

import pytest

from asusroutercontrol.analysis.clients import (
    LOAD_CRIT_PCT,
    LOAD_WARN_PCT,
    RSSI_WEAK_DBM,
    _health_dot,
    compute_client_loads,
    format_client_load_display,
    get_client_load_summary,
)
from asusroutercontrol.models import ClientLoad, ConnectionType, Device

# --- _health_dot ---


def test_health_dot_green_low_load() -> None:
    assert _health_dot(10.0, -50) == "🟢"


def test_health_dot_yellow_medium_load() -> None:
    assert _health_dot(60.0, -50) == "🟡"


def test_health_dot_red_high_load() -> None:
    assert _health_dot(85.0, -50) == "🔴"


def test_health_dot_red_weak_signal() -> None:
    """Weak RSSI forces red even at low load."""
    assert _health_dot(10.0, -80) == "🔴"


def test_health_dot_green_no_rssi() -> None:
    """None RSSI should not force red."""
    assert _health_dot(10.0, None) == "🟢"


def test_health_dot_boundary_warn() -> None:
    assert _health_dot(LOAD_WARN_PCT, -50) == "🟡"


def test_health_dot_boundary_crit() -> None:
    assert _health_dot(LOAD_CRIT_PCT, -50) == "🔴"


def test_health_dot_boundary_rssi() -> None:
    """Exactly at threshold is not weak."""
    assert _health_dot(10.0, RSSI_WEAK_DBM) == "🟢"
    assert _health_dot(10.0, RSSI_WEAK_DBM - 1) == "🔴"


# --- compute_client_loads ---


def _make_device(**overrides) -> Device:
    defaults = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "ip": "192.168.1.100",
        "hostname": "test-device",
        "connection": ConnectionType.WIFI_5G,
        "band": "5GHz",
        "rssi": -45,
        "tx_rate_mbps": 100.0,
        "rx_rate_mbps": 50.0,
        "is_online": True,
    }
    defaults.update(overrides)
    return Device(**defaults)


def test_compute_loads_basic() -> None:
    dev = _make_device(tx_rate_mbps=300.0, rx_rate_mbps=100.0)
    loads = compute_client_loads([dev])
    assert len(loads) == 1
    cl = loads[0]
    assert cl.mac == "AA:BB:CC:DD:EE:FF"
    # 300 / 600 * 100 = 50%
    assert cl.load_pct == 50.0
    assert cl.health == "🟡"


def test_compute_loads_wired() -> None:
    dev = _make_device(connection=ConnectionType.WIRED, tx_rate_mbps=500.0, rssi=None)
    loads = compute_client_loads([dev])
    # 500 / 1000 * 100 = 50%
    assert loads[0].load_pct == 50.0


def test_compute_loads_24ghz() -> None:
    dev = _make_device(connection=ConnectionType.WIFI_2G, tx_rate_mbps=120.0)
    loads = compute_client_loads([dev])
    # 120 / 150 * 100 = 80%
    assert loads[0].load_pct == 80.0
    assert loads[0].health == "🔴"


def test_compute_loads_skips_offline() -> None:
    dev = _make_device(is_online=False)
    loads = compute_client_loads([dev])
    assert len(loads) == 0


def test_compute_loads_zero_rates() -> None:
    dev = _make_device(tx_rate_mbps=0.0, rx_rate_mbps=0.0)
    loads = compute_client_loads([dev])
    assert loads[0].load_pct == 0.0
    assert loads[0].health == "🟢"


def test_compute_loads_none_rates() -> None:
    dev = _make_device(tx_rate_mbps=None, rx_rate_mbps=None)
    loads = compute_client_loads([dev])
    assert loads[0].load_pct == 0.0


def test_compute_loads_caps_at_100() -> None:
    dev = _make_device(tx_rate_mbps=900.0)  # 900/600 = 150% → capped
    loads = compute_client_loads([dev])
    assert loads[0].load_pct == 100.0


def test_compute_loads_sorted_descending() -> None:
    devices = [
        _make_device(mac="AA:AA:AA:AA:AA:01", tx_rate_mbps=50.0, rx_rate_mbps=10.0),
        _make_device(mac="AA:AA:AA:AA:AA:02", tx_rate_mbps=400.0, rx_rate_mbps=10.0),
        _make_device(mac="AA:AA:AA:AA:AA:03", tx_rate_mbps=200.0, rx_rate_mbps=10.0),
    ]
    loads = compute_client_loads(devices)
    pcts = [cl.load_pct for cl in loads]
    assert pcts == sorted(pcts, reverse=True)


def test_compute_loads_preserves_hostname_and_band() -> None:
    dev = _make_device(hostname="my-phone", band="5GHz")
    loads = compute_client_loads([dev])
    assert loads[0].hostname == "my-phone"
    assert loads[0].band == "5GHz"


def test_compute_loads_uses_connection_as_band_fallback() -> None:
    dev = _make_device(band=None, connection=ConnectionType.WIFI_5G)
    loads = compute_client_loads([dev])
    assert loads[0].band == "5GHz"


# --- ClientLoad model ---


def test_client_load_defaults() -> None:
    cl = ClientLoad(mac="AA:BB:CC:DD:EE:FF")
    assert cl.load_pct == 0.0
    assert cl.health == "🟢"
    assert cl.timestamp is not None


def test_format_client_load_display_precision_and_idle() -> None:
    assert format_client_load_display(None) == "n/a"
    assert format_client_load_display(0.0) == "idle"
    assert format_client_load_display(0.4) == "0.4%"
    assert format_client_load_display(9.9) == "9.9%"
    assert format_client_load_display(10.0) == "10%"


@pytest.mark.asyncio
async def test_get_client_load_summary_prioritizes_signal_rows() -> None:
    class _Store:
        async def get_client_loads(self, *, hours: int = 1):
            return [
                {
                    "mac": "AA:AA:AA:AA:AA:01",
                    "timestamp": "2026-03-21T10:00:00",
                    "tx_rate_mbps": None,
                    "rx_rate_mbps": None,
                    "rssi": None,
                    "load_pct": 99.0,
                },
                {
                    "mac": "AA:AA:AA:AA:AA:01",
                    "timestamp": "2026-03-21T10:00:10",
                    "tx_rate_mbps": 2.5,
                    "rx_rate_mbps": 1.2,
                    "rssi": -60,
                    "load_pct": 0.8,
                },
                {
                    "mac": "AA:AA:AA:AA:AA:02",
                    "timestamp": "2026-03-21T10:00:20",
                    "tx_rate_mbps": None,
                    "rx_rate_mbps": None,
                    "rssi": None,
                    "load_pct": 0.0,
                },
            ]

    rows = await get_client_load_summary(_Store())
    by_mac = {r["mac"]: r for r in rows}
    assert by_mac["AA:AA:AA:AA:AA:01"]["tx_rate_mbps"] == 2.5
    assert by_mac["AA:AA:AA:AA:AA:02"]["load_pct"] == 0.0

@pytest.mark.asyncio
async def test_get_client_load_summary_keeps_wired_row_when_top_n_is_wifi() -> None:
    class _Store:
        async def get_client_loads(self, *, hours: int = 1):
            rows = []
            for idx in range(20):
                rows.append({
                    "mac": f"AA:AA:AA:AA:AA:{idx:02X}",
                    "timestamp": f"2026-03-21T10:00:{idx:02d}",
                    "tx_rate_mbps": 15.0,
                    "rx_rate_mbps": 10.0,
                    "rssi": -55,
                    "load_pct": 40.0 - (idx * 0.5),
                    "band": "5GHz",
                })
            rows.append({
                "mac": "BB:BB:BB:BB:BB:01",
                "timestamp": "2026-03-21T10:01:30",
                "tx_rate_mbps": None,
                "rx_rate_mbps": None,
                "rssi": None,
                "load_pct": 0.0,
                "band": "wired",
            })
            return rows

    rows = await get_client_load_summary(_Store())
    assert len(rows) == 15
    assert any(str(row.get("band")).lower() == "wired" for row in rows)
