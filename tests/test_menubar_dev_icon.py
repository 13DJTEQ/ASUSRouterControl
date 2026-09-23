"""DEV runtime uses the test-tube menubar glyph, not the prod satellite."""

from __future__ import annotations

import sys
import types
from pathlib import Path

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
        "NSImageOnly",
        "NSImageRight",
        "NSMenu",
        "NSMenuItem",
        "NSObject",
        "NSStatusBar",
        "NSTimer",
        "NSUnderlineStyleAttributeName",
        "NSUnderlineStyleSingle",
        "NSVariableStatusItemLength",
    ):
        setattr(appkit, name, type(name, (object,), {}))
    appkit.NSImageOnly = 2
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


def test_icon_prefix_prod_is_satellite(menubar_mod) -> None:
    assert menubar_mod._icon_prefix_for_runtime("prod") == "📡"


def test_icon_prefix_dev_is_test_tube(menubar_mod) -> None:
    assert menubar_mod._icon_prefix_for_runtime("dev") == "🧪"
    assert menubar_mod._icon_prefix_for_runtime("test") == "🧪"


def test_runtime_env_infers_dev_from_app_bundle_name(
    menubar_mod, monkeypatch, tmp_path
) -> None:
    app = tmp_path / "ASUSRouterControl DEV.app"
    app.mkdir()
    monkeypatch.delenv("ASUSROUTERCONTROL_RUNTIME_ENV", raising=False)
    monkeypatch.setenv("ASUSROUTERCONTROL_APP_BUNDLE", str(app))
    assert menubar_mod._runtime_environment() == "dev"


def test_runtime_env_infers_dev_from_dev_build_marker(
    menubar_mod, monkeypatch, tmp_path
) -> None:
    app = tmp_path / "ASUSRouterControl.app"
    marker = app / "Contents" / "Resources" / "DEV_BUILD"
    marker.parent.mkdir(parents=True)
    marker.write_text("")
    monkeypatch.delenv("ASUSROUTERCONTROL_RUNTIME_ENV", raising=False)
    monkeypatch.setenv("ASUSROUTERCONTROL_APP_BUNDLE", str(app))
    assert menubar_mod._runtime_environment() == "dev"


def test_runtime_env_explicit_wins_over_bundle(
    menubar_mod, monkeypatch, tmp_path
) -> None:
    app = tmp_path / "ASUSRouterControl DEV.app"
    app.mkdir()
    monkeypatch.setenv("ASUSROUTERCONTROL_RUNTIME_ENV", "prod")
    monkeypatch.setenv("ASUSROUTERCONTROL_APP_BUNDLE", str(app))
    assert menubar_mod._runtime_environment() == "prod"


def test_generate_dev_icon_script_uses_test_tube_glyph() -> None:
    src = Path("scripts/generate_dev_icon.py").read_text(encoding="utf-8")
    assert "🧪" in src
    assert "test-tube" in src.lower() or "test tube" in src.lower()


def test_menubar_builds_template_glyph_helper(menubar_mod) -> None:
    assert callable(menubar_mod._make_menubar_glyph_image)
    assert "NSImageOnly" in Path("src/asusroutercontrol/menubar.py").read_text(
        encoding="utf-8"
    )
    assert "statusItemWithLength_(22.0)" in Path(
        "src/asusroutercontrol/menubar.py"
    ).read_text(encoding="utf-8")


def test_dev_launcher_execs_venv_python() -> None:
    src = Path("scripts/build_macos_app.sh").read_text(encoding="utf-8")
    assert 'exec "\\${VENV_PY}" -m asusroutercontrol.menubar' in src
    assert "checking venv imports" in src
