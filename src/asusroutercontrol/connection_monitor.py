"""Connection monitoring state helpers for menubar / CLI reporting.

Pure functions — no AppKit dependency — so unit tests can cover UX copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConnectionPhase = Literal["unknown", "connecting", "connected", "unable"]


@dataclass(frozen=True)
class ConnectionMonitorState:
    """Snapshot of router reachability for UI and notifications."""

    phase: ConnectionPhase
    host: str | None = None
    detail: str | None = None
    http_ok: bool | None = None
    ssh_ok: bool | None = None

    @property
    def menu_title(self) -> str:
        host = self.host or "router"
        if self.phase == "connecting":
            return f"Connection: Connecting to {host}…"
        if self.phase == "connected":
            http = _chip(self.http_ok, missing="?")
            ssh = _chip(self.ssh_ok, missing="?", off_label=None)
            if self.ssh_ok is None and self.detail == "ssh_off":
                ssh = "off"
            return f"Connection: Connected · HTTP {http} · SSH {ssh}"
        if self.phase == "unable":
            detail = (self.detail or "unreachable").strip()
            if len(detail) > 80:
                detail = detail[:77] + "…"
            return f"Connection: Unable to connect · {detail}"
        return "Connection: Unknown"

    @property
    def tooltip(self) -> str:
        if self.phase == "connecting":
            return f"Connecting to {self.host or 'router'}…"
        if self.phase == "connected":
            return f"Connected to {self.host or 'router'}"
        if self.phase == "unable":
            return f"Unable to connect: {self.detail or 'unreachable'}"
        return "Router connection status unknown"

    @property
    def notification(self) -> tuple[str, str, str] | None:
        """Return (title, subtitle, body) when a user-facing alert is warranted."""
        host = self.host or "router"
        if self.phase == "connecting":
            return ("Connecting to Router…", host, "Testing HTTP admin login")
        if self.phase == "connected":
            ssh = (
                "SSH ✓"
                if self.ssh_ok is True
                else ("SSH —" if self.ssh_ok is False else "SSH off/skipped")
            )
            return ("Router Connected", host, f"HTTP ✓ · {ssh}")
        if self.phase == "unable":
            return (
                "Unable to Connect",
                host,
                (self.detail or "Router unreachable")[:120],
            )
        return None


def _chip(ok: bool | None, *, missing: str = "?", off_label: str | None = None) -> str:
    if ok is True:
        return "✓"
    if ok is False:
        return "—"
    if off_label is not None:
        return off_label
    return missing


def connecting_state(host: str) -> ConnectionMonitorState:
    return ConnectionMonitorState(phase="connecting", host=host)


def connected_state(
    host: str,
    *,
    http_ok: bool = True,
    ssh_ok: bool | None = None,
    ssh_enabled: bool = True,
) -> ConnectionMonitorState:
    detail = None if ssh_enabled else "ssh_off"
    return ConnectionMonitorState(
        phase="connected",
        host=host,
        detail=detail,
        http_ok=http_ok,
        ssh_ok=None if not ssh_enabled else ssh_ok,
    )


def unable_state(host: str | None, detail: str) -> ConnectionMonitorState:
    return ConnectionMonitorState(
        phase="unable",
        host=host,
        detail=detail,
        http_ok=False,
    )


def state_from_profile(
    *,
    host: str | None,
    http_ok: bool | None,
    ssh_ok: bool | None,
    ssh_enabled: bool = True,
    last_error: str | None = None,
) -> ConnectionMonitorState:
    """Derive monitor state from persisted profile capability flags."""
    if http_ok is True:
        return connected_state(
            host or "router",
            http_ok=True,
            ssh_ok=ssh_ok,
            ssh_enabled=ssh_enabled,
        )
    if http_ok is False:
        return unable_state(host, last_error or "HTTP admin unreachable")
    return ConnectionMonitorState(phase="unknown", host=host)


def should_notify_transition(
    previous: ConnectionMonitorState | None,
    current: ConnectionMonitorState,
) -> bool:
    """Notify on meaningful phase changes; skip unknown→unknown noise."""
    if previous is None:
        return current.phase in {"connecting", "connected", "unable"}
    if previous.phase == current.phase:
        # Re-notify unable only when the detail/reason changes.
        if current.phase == "unable":
            return (previous.detail or "") != (current.detail or "")
        return False
    # Ignore unknown flapping.
    if current.phase == "unknown":
        return False
    return True
