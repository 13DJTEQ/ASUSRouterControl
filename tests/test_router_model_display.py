"""Router model display must come from live router data, never hardcoded SKUs."""

from __future__ import annotations

from pathlib import Path

from asusroutercontrol.router_model import (
    format_router_model_menu_title,
    model_from_firmware_payload,
    normalize_router_model,
)


def test_normalize_router_model_accepts_be92u() -> None:
    assert normalize_router_model("RT-BE92U") == "RT-BE92U"
    assert normalize_router_model("rt-be92u") == "RT-BE92U"
    assert normalize_router_model("  RT-BE92U  ") == "RT-BE92U"


def test_normalize_router_model_rejects_empty() -> None:
    assert normalize_router_model(None) is None
    assert normalize_router_model("") is None
    assert normalize_router_model("unknown") is None


def test_format_title_uses_live_model_not_hardcoded_ac68() -> None:
    title = format_router_model_menu_title("RT-BE92U", 88.0)
    assert "RT-BE92U" in title
    assert "RT-AC68U" not in title
    assert "88/100" in title


def test_format_title_unknown_until_connected() -> None:
    assert format_router_model_menu_title(None) == "Router: unknown"
    assert format_router_model_menu_title("", 12) == "Router: unknown  ·  Health: 12/100"


def test_model_from_firmware_payload_productid() -> None:
    assert model_from_firmware_payload({"productid": "RT-BE92U"}) == "RT-BE92U"
    assert model_from_firmware_payload({"model": "RT-AC68U"}) == "RT-AC68U"
    assert model_from_firmware_payload({}) is None


def test_menubar_has_no_hardcoded_ac68u_or_be92u_sku() -> None:
    src = Path("src/asusroutercontrol/menubar.py").read_text(encoding="utf-8")
    assert "RT-AC68U" not in src
    assert "RT-BE92U" not in src
    assert "format_router_model_menu_title" in src
    assert "fetch_live_router_model" in src
    assert "_invalidate_router_model" in src


def test_build_script_stamps_bundle_version() -> None:
    src = Path("scripts/build_macos_app.sh").read_text(encoding="utf-8")
    assert "BUILD_STAMP" in src
    assert "bundle_version" in src
    assert 'CFBundleVersion</key>\n  <string>1</string>' not in src
