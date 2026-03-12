"""macOS notification delivery for ASUSRouterControl.

Uses native NSUserNotificationCenter when available (routes through the
running app, not Script Editor).  Falls back to osascript if the native
API is unavailable.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Native notification centre (lazy-init)
# ---------------------------------------------------------------------------

_native_center = None
_native_checked = False


def _ensure_native() -> bool:
    """Try to initialise the native notification centre once."""
    global _native_center, _native_checked
    if _native_checked:
        return _native_center is not None
    _native_checked = True
    try:
        from Foundation import NSUserNotificationCenter

        _native_center = NSUserNotificationCenter.defaultUserNotificationCenter()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify(title: str, subtitle: str = "", message: str = "") -> None:
    """Post a macOS notification.

    Tries the native PyObjC path first (attributed to the running app),
    then falls back to osascript.
    """
    if _ensure_native():
        try:
            _deliver_native(title, subtitle, message)
            return
        except Exception:
            log.debug("Native notification failed, falling back to osascript")

    _deliver_osascript(title, subtitle, message)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _deliver_native(title: str, subtitle: str, message: str) -> None:
    from Foundation import NSUserNotification

    n = NSUserNotification.alloc().init()
    n.setTitle_(title)
    n.setSubtitle_(subtitle)
    n.setInformativeText_(message)
    _native_center.deliverNotification_(n)  # type: ignore[union-attr]


def _deliver_osascript(title: str, subtitle: str, message: str) -> None:
    try:

        def esc(s: str) -> str:
            return s.replace('"', '\\"')

        script = (
            f'display notification "{esc(message)}" '
            f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
        )
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.warning("Failed to post notification: %s", title)
