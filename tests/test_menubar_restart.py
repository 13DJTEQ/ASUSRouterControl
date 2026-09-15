"""Menubar Restart works under launchd KeepAlive or by relaunching the .app bundle."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


def _install_pyobjc_stubs() -> None:
    if "AppKit" in sys.modules and hasattr(sys.modules["AppKit"], "NSObject"):
        return

    appkit = types.ModuleType("AppKit")

    class NSObject:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    for name in (
        "NSAlert",
        "NSAlertFirstButtonReturn",
        "NSApplication",
        "NSApplicationActivationPolicyAccessory",
        "NSAttributedString",
        "NSBezierPath",
        "NSColor",
        "NSFont",
        "NSFontAttributeName",
        "NSImage",
        "NSImageRight",
        "NSMenu",
        "NSMenuItem",
        "NSStatusBar",
        "NSTimer",
        "NSUnderlineStyleAttributeName",
        "NSUnderlineStyleSingle",
        "NSVariableStatusItemLength",
    ):
        setattr(appkit, name, type(name, (object,), {}))
    appkit.NSObject = NSObject
    sys.modules["AppKit"] = appkit

    objc = types.ModuleType("objc")

    def typedSelector(_sig):  # noqa: N802
        def decorator(fn):
            return fn

        return decorator

    objc.typedSelector = typedSelector
    objc.selector = typedSelector
    sys.modules["objc"] = objc

    pyobjc_tools = types.ModuleType("PyObjCTools")
    app_helper = types.ModuleType("PyObjCTools.AppHelper")
    app_helper.runEventLoop = lambda *a, **k: None
    sys.modules["PyObjCTools"] = pyobjc_tools
    sys.modules["PyObjCTools.AppHelper"] = app_helper


@pytest.fixture
def menubar_mod():
    _install_pyobjc_stubs()
    sys.modules.pop("asusroutercontrol.menubar", None)
    import asusroutercontrol.menubar as menubar

    return menubar


def test_app_bundle_path_reads_env(menubar_mod, tmp_path, monkeypatch) -> None:
    app = tmp_path / "ASUSRouterControl DEV.app"
    app.mkdir()
    monkeypatch.setenv("ASUSROUTERCONTROL_APP_BUNDLE", str(app))
    assert menubar_mod._app_bundle_path() == app


def test_quit_app_relaunches_bundle_when_not_under_launchd(
    menubar_mod, tmp_path, monkeypatch
) -> None:
    app = tmp_path / "ASUSRouterControl DEV.app"
    app.mkdir()
    monkeypatch.setenv("ASUSROUTERCONTROL_APP_BUNDLE", str(app))

    delegate = menubar_mod.AppDelegate.alloc().init()
    delegate._stop_scheduler = MagicMock()
    notifications: list[tuple] = []
    popen_calls: list[list[str]] = []
    terminate = MagicMock()

    ns_app = MagicMock()
    ns_app.terminate_ = terminate

    with (
        patch.object(menubar_mod, "_notify", lambda *a, **k: notifications.append(a)),
        patch.object(menubar_mod, "_launchd_service_loaded", return_value=False),
        patch.object(
            menubar_mod,
            "_menubar_launchd_plist_path",
            return_value=tmp_path / "missing.plist",
        ),
        patch.object(menubar_mod, "NSApplication") as ns_application,
        patch.object(
            menubar_mod.subprocess,
            "Popen",
            side_effect=lambda cmd, **k: popen_calls.append(list(cmd)),
        ),
    ):
        ns_application.sharedApplication.return_value = ns_app
        menubar_mod.AppDelegate.quitApp_(delegate, None)

    assert popen_calls == [["/usr/bin/open", "-n", str(app)]]
    terminate.assert_called_once()
    assert notifications == []


def test_quit_app_keeps_running_when_restart_impossible(menubar_mod, tmp_path) -> None:
    delegate = menubar_mod.AppDelegate.alloc().init()
    delegate._stop_scheduler = MagicMock()
    notifications: list[tuple] = []
    terminate = MagicMock()
    ns_app = MagicMock()
    ns_app.terminate_ = terminate

    with (
        patch.object(menubar_mod, "_notify", lambda *a, **k: notifications.append(a)),
        patch.object(menubar_mod, "_launchd_service_loaded", return_value=False),
        patch.object(menubar_mod, "_app_bundle_path", return_value=None),
        patch.object(
            menubar_mod,
            "_menubar_launchd_plist_path",
            return_value=tmp_path / "missing.plist",
        ),
        patch.object(menubar_mod, "NSApplication") as ns_application,
    ):
        ns_application.sharedApplication.return_value = ns_app
        menubar_mod.AppDelegate.quitApp_(delegate, None)

    terminate.assert_not_called()
    assert any("Restart Failed" in str(n[0]) for n in notifications)
