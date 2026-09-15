"""Phase 2: menubar reboot goes through create_backend (not MerlinBackend)."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _install_pyobjc_stubs() -> None:
    """Stub PyObjC so menubar can be imported on Linux CI."""
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

    def selector(_sig):
        def decorator(fn):
            return fn

        return decorator

    objc.typedSelector = typedSelector
    objc.selector = selector
    sys.modules["objc"] = objc

    pyobjc_tools = types.ModuleType("PyObjCTools")
    app_helper = types.ModuleType("PyObjCTools.AppHelper")
    app_helper.runEventLoop = lambda *a, **k: None
    sys.modules["PyObjCTools"] = pyobjc_tools
    sys.modules["PyObjCTools.AppHelper"] = app_helper


@pytest.fixture
def menubar_mod():
    _install_pyobjc_stubs()
    # Force re-import if a prior failed import left a broken module
    sys.modules.pop("asusroutercontrol.menubar", None)
    import asusroutercontrol.menubar as menubar

    return menubar


def test_menubar_reboot_source_uses_factory_not_merlin() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text()
    idx = src.index("def _do_reboot")
    chunk = src[idx : idx + 1500]
    assert "create_backend" in chunk
    assert "MerlinBackend(" not in chunk
    assert "BackendDeferredError" in chunk
    assert "BackendOperationUnsupported" in chunk


def test_do_reboot_uses_create_backend(menubar_mod) -> None:
    from asusroutercontrol.config import Config

    delegate = menubar_mod.AppDelegate()
    delegate._cfg = Config(router_backend="stock")

    backend = AsyncMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    backend.set_state = AsyncMock(return_value=True)

    notifications: list[tuple] = []

    loop = asyncio.new_event_loop()
    try:
        with (
            patch.object(
                menubar_mod, "_notify", lambda *a, **k: notifications.append(a)
            ),
            patch(
                "asusroutercontrol.credentials.get_router_credentials",
                return_value=("admin", "secret"),
            ),
            patch(
                "asusroutercontrol.backends.factory.create_backend",
                return_value=backend,
            ) as create_backend,
            patch.object(
                asyncio, "run", side_effect=lambda coro: loop.run_until_complete(coro)
            ),
        ):
            menubar_mod.AppDelegate._do_reboot(delegate)
    finally:
        loop.close()

    create_backend.assert_called_once()
    cfg_arg = create_backend.call_args.args[0]
    assert cfg_arg.router_backend == "stock"
    assert create_backend.call_args.kwargs["username"] == "admin"
    backend.connect.assert_awaited()
    backend.set_state.assert_awaited_with("reboot")
    backend.disconnect.assert_awaited()
    assert any("Rebooting" in str(n[0]) for n in notifications)


def test_do_reboot_notifies_when_backend_deferred(menubar_mod) -> None:
    from asusroutercontrol.backends.factory import BackendDeferredError
    from asusroutercontrol.config import Config

    delegate = menubar_mod.AppDelegate()
    delegate._cfg = Config(router_backend="freshtomato")
    notifications: list[tuple] = []

    with (
        patch.object(menubar_mod, "_notify", lambda *a, **k: notifications.append(a)),
        patch(
            "asusroutercontrol.credentials.get_router_credentials",
            return_value=("admin", "secret"),
        ),
        patch(
            "asusroutercontrol.backends.factory.create_backend",
            side_effect=BackendDeferredError("FreshTomato deferred"),
        ),
    ):
        menubar_mod.AppDelegate._do_reboot(delegate)

    assert any("Unsupported" in str(n[0]) for n in notifications)
