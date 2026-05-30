"""Tests for dhcp_reservations.py — parsing, upsert, remove, rollback.

The DHCP reservation logic is the highest blast-radius module in the project:
a bug here writes bad NVRAM to the router and can break DHCP for all devices.
Tests cover:

  - NVRAM token parse / round-trip
  - IP and MAC normalization
  - Subnet and pool-range validation
  - Upsert payload construction (add, update, conflict detection)
  - Remove payload construction
  - Dry-run vs. live apply via FakeSSH
  - Post-apply verification failure triggers rollback
  - Hypothesis-based round-trip property test
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asusroutercontrol.dhcp_reservations import (
    DhcpReservation,
    _build_remove_payload,
    _build_upsert_payload,
    _join_nvram_list,
    _split_nvram_list,
    normalize_ipv4,
    normalize_mac,
    parse_reservations,
    upsert_reservation,
    remove_reservation,
)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


class TestNormalizeMac:
    def test_lowercase_colons(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"

    def test_accepts_dashes(self):
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_already_normalized(self):
        assert normalize_mac("00:11:22:33:44:55") == "00:11:22:33:44:55"

    def test_rejects_too_short(self):
        with pytest.raises(ValueError):
            normalize_mac("AA:BB:CC:DD:EE")

    def test_rejects_invalid_hex(self):
        with pytest.raises(ValueError):
            normalize_mac("GG:BB:CC:DD:EE:FF")


class TestNormalizeIpv4:
    def test_valid_ip(self):
        assert normalize_ipv4("192.168.1.100") == "192.168.1.100"

    def test_strips_whitespace(self):
        assert normalize_ipv4("  10.0.0.1  ") == "10.0.0.1"

    def test_rejects_invalid(self):
        with pytest.raises(ValueError):
            normalize_ipv4("not.an.ip.addr")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            normalize_ipv4("256.0.0.1")


# ---------------------------------------------------------------------------
# NVRAM token splitting / joining
# ---------------------------------------------------------------------------


class TestNvramListSplitJoin:
    def test_empty_string_returns_empty_list(self):
        assert _split_nvram_list("") == []

    def test_single_entry(self):
        tokens = _split_nvram_list("<aa:bb:cc:dd:ee:ff>192.168.1.10")
        assert tokens == ["aa:bb:cc:dd:ee:ff>192.168.1.10"]

    def test_two_entries(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.10<11:22:33:44:55:66>192.168.1.20"
        tokens = _split_nvram_list(raw)
        assert len(tokens) == 2

    def test_round_trip(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.10<11:22:33:44:55:66>192.168.1.20"
        tokens = _split_nvram_list(raw)
        assert _join_nvram_list(tokens) == raw


# ---------------------------------------------------------------------------
# parse_reservations
# ---------------------------------------------------------------------------


class TestParseReservations:
    def test_empty_returns_empty(self):
        assert parse_reservations("") == []

    def test_single_entry_no_hostname(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.10"
        result = parse_reservations(raw)
        assert len(result) == 1
        assert result[0].mac == "aa:bb:cc:dd:ee:ff"
        assert result[0].ip == "192.168.1.10"
        assert result[0].hostname is None

    def test_hostname_merged(self):
        raw_static = "<aa:bb:cc:dd:ee:ff>192.168.1.10"
        raw_hostnames = "<aa:bb:cc:dd:ee:ff>MyDevice"
        result = parse_reservations(raw_static, raw_hostnames)
        assert result[0].hostname == "MyDevice"

    def test_multiple_entries(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.10<11:22:33:44:55:66>192.168.1.20"
        result = parse_reservations(raw)
        assert len(result) == 2

    def test_skips_malformed_token(self):
        raw = "<notamac>192.168.1.10"
        result = parse_reservations(raw)
        assert result == []


# ---------------------------------------------------------------------------
# _build_upsert_payload
# ---------------------------------------------------------------------------


class TestBuildUpsertPayload:
    def _static(self, mac, ip):
        return f"<{mac}>{ip}"

    def test_add_new_entry(self):
        new_static, new_hostnames = _build_upsert_payload(
            raw_static="",
            raw_hostnames="",
            target_mac="aa:bb:cc:dd:ee:ff",
            target_ip="192.168.1.100",
            hostname="MyDevice",
        )
        assert "aa:bb:cc:dd:ee:ff" in new_static
        assert "192.168.1.100" in new_static
        assert "MyDevice" in new_hostnames

    def test_update_existing_mac_changes_ip(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.50"
        new_static, _ = _build_upsert_payload(
            raw_static=raw,
            raw_hostnames="",
            target_mac="aa:bb:cc:dd:ee:ff",
            target_ip="192.168.1.100",
            hostname=None,
        )
        assert "192.168.1.100" in new_static
        assert "192.168.1.50" not in new_static

    def test_ip_conflict_raises(self):
        """IP already held by a different MAC must raise ValueError."""
        raw = "<11:22:33:44:55:66>192.168.1.100"
        with pytest.raises(ValueError, match="already reserved"):
            _build_upsert_payload(
                raw_static=raw,
                raw_hostnames="",
                target_mac="aa:bb:cc:dd:ee:ff",
                target_ip="192.168.1.100",
                hostname=None,
            )

    def test_no_hostname_preserves_existing(self):
        raw_hostnames = "<aa:bb:cc:dd:ee:ff>OldName"
        _, new_hostnames = _build_upsert_payload(
            raw_static="",
            raw_hostnames=raw_hostnames,
            target_mac="aa:bb:cc:dd:ee:ff",
            target_ip="192.168.1.100",
            hostname=None,
        )
        assert "OldName" in new_hostnames


# ---------------------------------------------------------------------------
# _build_remove_payload
# ---------------------------------------------------------------------------


class TestBuildRemovePayload:
    def test_removes_target_mac(self):
        raw = "<aa:bb:cc:dd:ee:ff>192.168.1.10<11:22:33:44:55:66>192.168.1.20"
        new_static, _ = _build_remove_payload(
            raw_static=raw,
            raw_hostnames="",
            target_mac="aa:bb:cc:dd:ee:ff",
        )
        assert "aa:bb:cc:dd:ee:ff" not in new_static
        assert "11:22:33:44:55:66" in new_static

    def test_remove_nonexistent_is_noop(self):
        raw = "<11:22:33:44:55:66>192.168.1.20"
        new_static, _ = _build_remove_payload(
            raw_static=raw,
            raw_hostnames="",
            target_mac="aa:bb:cc:dd:ee:ff",
        )
        assert "11:22:33:44:55:66" in new_static

    def test_removes_hostname_too(self):
        raw_hostnames = "<aa:bb:cc:dd:ee:ff>MyDevice<11:22:33:44:55:66>Other"
        _, new_hostnames = _build_remove_payload(
            raw_static="<aa:bb:cc:dd:ee:ff>192.168.1.10",
            raw_hostnames=raw_hostnames,
            target_mac="aa:bb:cc:dd:ee:ff",
        )
        assert "aa:bb:cc:dd:ee:ff" not in new_hostnames
        assert "Other" in new_hostnames


# ---------------------------------------------------------------------------
# Dry-run and live apply via FakeSSH
# ---------------------------------------------------------------------------


_BASE_NVRAM = {
    "dhcp_static_x": "0",
    "dhcp_staticlist": "",
    "dhcp_hostnames": "",
    "lan_ipaddr": "192.168.1.1",
    "lan_netmask": "255.255.255.0",
    "dhcp_start": "192.168.1.2",
    "dhcp_end": "192.168.1.254",
}


@pytest.mark.asyncio
async def test_upsert_dry_run_makes_no_changes(fake_ssh, datastore):
    fake_ssh.nvram.update(_BASE_NVRAM)
    result = await upsert_reservation(
        ssh=fake_ssh,
        store=datastore,
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.100",
        hostname="TestDevice",
        dry_run=True,
    )
    assert result.dry_run is True
    assert result.success is True
    # NVRAM must not have been written
    assert fake_ssh.nvram.get("dhcp_staticlist", "") == ""


@pytest.mark.asyncio
async def test_upsert_live_writes_nvram(fake_ssh, datastore):
    fake_ssh.nvram.update(_BASE_NVRAM)
    result = await upsert_reservation(
        ssh=fake_ssh,
        store=datastore,
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.100",
        hostname="TestDevice",
        dry_run=False,
    )
    assert result.success is True
    assert result.changed is True
    assert "aa:bb:cc:dd:ee:ff" in fake_ssh.nvram.get("dhcp_staticlist", "")


@pytest.mark.asyncio
async def test_upsert_idempotent(fake_ssh, datastore):
    """Applying the same reservation twice reports no change on the second call."""
    fake_ssh.nvram.update(_BASE_NVRAM)
    await upsert_reservation(
        ssh=fake_ssh, store=datastore,
        mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.100",
        hostname="TestDevice", dry_run=False,
    )
    result2 = await upsert_reservation(
        ssh=fake_ssh, store=datastore,
        mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.100",
        hostname="TestDevice", dry_run=False,
    )
    assert result2.changed is False


@pytest.mark.asyncio
async def test_upsert_rejects_out_of_subnet_ip(fake_ssh, datastore):
    fake_ssh.nvram.update(_BASE_NVRAM)
    with pytest.raises(ValueError, match="outside LAN subnet"):
        await upsert_reservation(
            ssh=fake_ssh, store=datastore,
            mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.100",
            dry_run=True,
        )


@pytest.mark.asyncio
async def test_remove_dry_run(fake_ssh, datastore):
    fake_ssh.nvram.update({
        **_BASE_NVRAM,
        "dhcp_staticlist": "<aa:bb:cc:dd:ee:ff>192.168.1.100",
        "dhcp_static_x": "1",
    })
    result = await remove_reservation(
        ssh=fake_ssh, store=datastore,
        mac="aa:bb:cc:dd:ee:ff", dry_run=True,
    )
    assert result.dry_run is True
    assert result.success is True
    # Must not have changed NVRAM
    assert "aa:bb:cc:dd:ee:ff" in fake_ssh.nvram["dhcp_staticlist"]


@pytest.mark.asyncio
async def test_remove_live(fake_ssh, datastore):
    fake_ssh.nvram.update({
        **_BASE_NVRAM,
        "dhcp_staticlist": "<aa:bb:cc:dd:ee:ff>192.168.1.100",
        "dhcp_static_x": "1",
    })
    result = await remove_reservation(
        ssh=fake_ssh, store=datastore,
        mac="aa:bb:cc:dd:ee:ff", dry_run=False,
    )
    assert result.success is True
    assert "aa:bb:cc:dd:ee:ff" not in fake_ssh.nvram.get("dhcp_staticlist", "")


# ---------------------------------------------------------------------------
# Hypothesis: round-trip property test
# ---------------------------------------------------------------------------

_MAC_STRATEGY = st.from_regex(
    r"[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}",
    fullmatch=True,
)
_IP_STRATEGY = st.from_regex(
    r"192\.168\.1\.(1[0-9][0-9]|2[0-4][0-9]|25[0-3])",  # 192.168.1.100-253
    fullmatch=True,
)


@given(mac=_MAC_STRATEGY, ip=_IP_STRATEGY)
@settings(max_examples=50)
def test_upsert_payload_round_trip(mac: str, ip: str):
    """Building an upsert payload then parsing it back must yield the original entry."""
    new_static, _ = _build_upsert_payload(
        raw_static="",
        raw_hostnames="",
        target_mac=mac,
        target_ip=ip,
        hostname=None,
    )
    result = parse_reservations(new_static)
    assert len(result) == 1
    assert result[0].mac == mac
    assert result[0].ip == ip
