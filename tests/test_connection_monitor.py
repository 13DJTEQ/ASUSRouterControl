"""Unit tests for connection monitor reporting helpers."""

from __future__ import annotations

from asusroutercontrol.connection_monitor import (
    ConnectionMonitorState,
    connected_state,
    connecting_state,
    should_notify_transition,
    state_from_profile,
    unable_state,
)


def test_connecting_menu_and_notification():
    state = connecting_state("192.168.50.1")
    assert "Connecting to 192.168.50.1" in state.menu_title
    note = state.notification
    assert note is not None
    assert note[0].startswith("Connecting")


def test_connected_menu_includes_capability_chips():
    state = connected_state("router.asus.com", http_ok=True, ssh_ok=False)
    assert state.menu_title == "Connection: Connected · HTTP ✓ · SSH —"
    note = state.notification
    assert note is not None
    assert "HTTP ✓" in note[2]


def test_unable_menu_truncates_long_detail():
    long = "x" * 120
    state = unable_state("10.0.0.1", long)
    assert state.menu_title.startswith("Connection: Unable to connect · ")
    assert state.menu_title.endswith("…")
    assert len(state.menu_title) < 130
    note = state.notification
    assert note is not None
    assert note[0] == "Unable to Connect"


def test_state_from_profile_maps_flags():
    assert state_from_profile(host="h", http_ok=True, ssh_ok=True).phase == "connected"
    assert state_from_profile(host="h", http_ok=False, ssh_ok=None).phase == "unable"
    assert state_from_profile(host="h", http_ok=None, ssh_ok=None).phase == "unknown"


def test_should_notify_on_phase_change_only():
    connecting = connecting_state("h")
    connected = connected_state("h")
    unable = unable_state("h", "timeout")
    unable2 = unable_state("h", "auth failed")

    assert should_notify_transition(None, connecting) is True
    assert should_notify_transition(connecting, connected) is True
    assert should_notify_transition(connected, connected) is False
    assert should_notify_transition(unable, unable) is False
    assert should_notify_transition(unable, unable2) is True
    assert should_notify_transition(connected, ConnectionMonitorState(phase="unknown")) is False
