# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AsusRouterMonitor.app

Build:
    .venv/bin/pyinstaller AsusRouterMonitor.spec

Output:  dist/AsusRouterMonitor.app
"""
import os
import sys
from pathlib import Path

block_cipher = None

HERE = Path(SPECPATH)
SRC = HERE / "src"
VENV_SP = HERE / ".venv" / "lib" / "python3.11" / "site-packages"

# ── Data files to bundle ────────────────────────────────────────────
datas = [
    (str(SRC / "asusroutercontrol" / "dhcp_profiles.example.toml"), "asusroutercontrol"),
]

# ── Hidden imports that PyInstaller cannot detect automatically ──────
hidden_imports = [
    # PyObjC
    "objc",
    "AppKit",
    "Foundation",
    "PyObjCTools",
    "PyObjCTools.AppHelper",
    # asusroutercontrol subpackages loaded dynamically
    "asusroutercontrol.analysis",
    "asusroutercontrol.analysis.clients",
    "asusroutercontrol.analyzer",
    "asusroutercontrol.backends",
    "asusroutercontrol.backends.factory",
    "asusroutercontrol.backends.merlin",
    "asusroutercontrol.backends.freshtomato",
    "asusroutercontrol.backends.base",
    "asusroutercontrol.config",
    "asusroutercontrol.credentials",
    "asusroutercontrol.datastore",
    "asusroutercontrol.executor",
    "asusroutercontrol.integrations",
    "asusroutercontrol.merlin",
    "asusroutercontrol.models",
    "asusroutercontrol.notifications",
    "asusroutercontrol.optimizer",
    "asusroutercontrol.probes",
    "asusroutercontrol.reporting",
    "asusroutercontrol.rollout",
    "asusroutercontrol.scheduler",
    "asusroutercontrol.speedtest",
    "asusroutercontrol.speedtest_providers",
    "asusroutercontrol.ssh",
    "asusroutercontrol._time",
    # Third-party runtime deps
    "aiohttp",
    "aiosqlite",
    "asyncssh",
    "certifi",
    "click",
    "dotenv",
    "keyring",
    "keyring.backends",
    "keyring.backends.macOS",
    "pydantic",
    "rich",
    "multidict",
    "yarl",
    "aiohttp.web",
    "frozenlist",
    "aiosignal",
    "async_timeout",
    "charset_normalizer",
    "idna",
    "attrs",
    # stdlib extras
    "sqlite3",
    "statistics",
    "json",
    "ssl",
]

a = Analysis(
    [str(SRC / "asusroutercontrol" / "menubar.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "xmlrpc", "unittest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AsusRouterMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app, no terminal window
    target_arch=None,         # universal / current arch
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AsusRouterMonitor",
)

app = BUNDLE(
    coll,
    name="AsusRouterMonitor.app",
    icon=None,  # replace with "assets/AppIcon.icns" when available
    bundle_identifier="com.asusroutermonitor",
    info_plist={
        "CFBundleName": "AsusRouterMonitor",
        "CFBundleDisplayName": "AsusRouterMonitor",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,            # menu-bar only, no Dock icon
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
    },
)
