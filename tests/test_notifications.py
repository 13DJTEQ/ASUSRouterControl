"""Tests for asusroutercontrol.notifications."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import asusroutercontrol.notifications as notif_mod


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset lazy-init globals between tests."""
    notif_mod._un_center = None
    notif_mod._un_checked = False
    notif_mod._un_authorized = False
    yield
    notif_mod._un_center = None
    notif_mod._un_checked = False
    notif_mod._un_authorized = False


def test_notify_uses_un_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When UNUserNotificationCenter is importable, _deliver_un is called."""
    fake_center = MagicMock()
    fake_center.requestAuthorizationWithOptions_completionHandler_ = MagicMock()
    fake_center.addNotificationRequest_withCompletionHandler_ = MagicMock()

    fake_un_module = MagicMock()
    fake_un_module.UNUserNotificationCenter.currentNotificationCenter.return_value = (
        fake_center
    )
    fake_un_module.UNAuthorizationOptionAlert = 1
    fake_un_module.UNAuthorizationOptionSound = 2

    fake_content = MagicMock()
    fake_un_module.UNMutableNotificationContent.alloc.return_value.init.return_value = (
        fake_content
    )
    fake_request = MagicMock()
    fake_un_module.UNNotificationRequest.requestWithIdentifier_content_trigger_.return_value = (
        fake_request
    )

    import sys
    monkeypatch.setitem(sys.modules, "UserNotifications", fake_un_module)

    notif_mod.notify("Title", "Sub", "Body")

    fake_content.setTitle_.assert_called_once_with("Title")
    fake_content.setSubtitle_.assert_called_once_with("Sub")
    fake_content.setBody_.assert_called_once_with("Body")
    fake_center.addNotificationRequest_withCompletionHandler_.assert_called_once()


def test_notify_falls_back_to_osascript_when_un_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When UserNotifications import fails, osascript is used."""
    import builtins

    real_import = builtins.__import__

    def _block_un(name, *args, **kwargs):
        if name == "UserNotifications":
            raise ImportError("no UserNotifications")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_un)

    with patch.object(notif_mod, "_deliver_osascript") as mock_osa:
        notif_mod.notify("T", "S", "M")
        mock_osa.assert_called_once_with("T", "S", "M")


def test_notify_falls_back_when_un_delivery_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If _deliver_un raises, we fall back to osascript."""
    # Pretend UN init succeeded
    notif_mod._un_checked = True
    notif_mod._un_center = MagicMock()

    monkeypatch.setattr(
        notif_mod, "_deliver_un", MagicMock(side_effect=RuntimeError("boom"))
    )

    with patch.object(notif_mod, "_deliver_osascript") as mock_osa:
        notif_mod.notify("T", "S", "M")
        mock_osa.assert_called_once_with("T", "S", "M")


def test_ensure_un_caches_result() -> None:
    """_ensure_un only probes once (idempotent after first call)."""
    notif_mod._un_checked = True
    notif_mod._un_center = None
    # Second call should not re-probe, just return cached False
    assert notif_mod._ensure_un() is False
