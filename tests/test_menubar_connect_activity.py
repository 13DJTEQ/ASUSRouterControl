"""Connect flow must spin the menubar icon for visibility."""

from __future__ import annotations

from pathlib import Path


def test_menubar_connect_starts_spinner_activity() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    assert "def _begin_connect_activity" in src
    assert "_begin_connect_activity(host)" in src
    assert 'self._start_spinner(f"Connecting to {host}…")' in src
    assert "def showConnectingStatus_" in src
    assert "_begin_connect_activity(host_s)" in src


def test_menubar_finish_connect_stops_spinner() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    for name in (
        "finishConnectSuccess_",
        "finishConnectFailure_",
        "showUnableStatus_",
        "showConnectedStatus_",
    ):
        idx = src.index(f"def {name}")
        end = src.find("\n    def ", idx + 1)
        if end < 0:
            end = src.find("\n    @objc", idx + 1)
        chunk = src[idx:end if end > 0 else idx + 900]
        assert "_stop_spinner()" in chunk, name
        # Stop after connection state is applied so tooltip matches outcome.
        assert chunk.index("_set_connection_state") < chunk.index("_stop_spinner()"), name


def test_apply_data_preserves_connecting_icon() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    assert "not busy and not connecting" in src
    assert 'phase == "connecting"' in src


def test_menubar_connect_resolves_blank_password() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    assert "resolve_blank_connect_password" in src
    assert "Connect failure detail:" in src
    assert "fill_source" in src
