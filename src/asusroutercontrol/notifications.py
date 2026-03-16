"""macOS notification delivery for ASUSRouterControl.

Uses the modern UserNotifications framework (UNUserNotificationCenter) when
available.  Falls back to osascript if the native API cannot be loaded (e.g.
missing PyObjC bindings or running outside an app bundle).
"""

from __future__ import annotations

import logging
import subprocess
import uuid

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modern notification centre (lazy-init)
# ---------------------------------------------------------------------------

_un_center = None
_un_checked = False
_un_authorized = False


def _ensure_un() -> bool:
    """Try to initialise UNUserNotificationCenter once."""
    global _un_center, _un_checked, _un_authorized
    if _un_checked:
        return _un_center is not None
    _un_checked = True
    try:
        from UserNotifications import UNUserNotificationCenter

        _un_center = UNUserNotificationCenter.currentNotificationCenter()
        _request_authorization()
        return True
    except Exception:
        log.debug("UNUserNotificationCenter unavailable, will use osascript")
        return False


def _request_authorization() -> None:
    """Request notification permission (alert + sound).  Non-blocking."""
    global _un_authorized
    try:
        from UserNotifications import (
            UNAuthorizationOptionAlert,
            UNAuthorizationOptionSound,
        )

        options = UNAuthorizationOptionAlert | UNAuthorizationOptionSound

        def _callback(granted, error):
            global _un_authorized
            _un_authorized = bool(granted)
            if error:
                log.debug("Notification auth error: %s", error)

        _un_center.requestAuthorizationWithOptions_completionHandler_(
            options, _callback
        )
    except Exception:
        log.debug("Failed to request notification authorization")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify(title: str, subtitle: str = "", message: str = "") -> None:
    """Post a macOS notification.

    Tries UNUserNotificationCenter first, then falls back to osascript.
    """
    if _ensure_un():
        try:
            _deliver_un(title, subtitle, message)
            return
        except Exception:
            log.debug(
                "UNUserNotificationCenter delivery failed, "
                "falling back to osascript"
            )

    _deliver_osascript(title, subtitle, message)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _deliver_un(title: str, subtitle: str, message: str) -> None:
    from UserNotifications import (
        UNMutableNotificationContent,
        UNNotificationRequest,
    )

    content = UNMutableNotificationContent.alloc().init()
    content.setTitle_(title)
    content.setSubtitle_(subtitle)
    content.setBody_(message)

    request_id = uuid.uuid4().hex[:12]
    request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
        request_id, content, None
    )
    _un_center.addNotificationRequest_withCompletionHandler_(
        request, None
    )


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
