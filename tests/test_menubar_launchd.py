"""Unit tests for menubar launchd app-bundle resolution."""

from __future__ import annotations

from pathlib import Path

from asusroutercontrol.menubar_launchd import (
    DEV_APP_NAME,
    DEV_EXECUTABLE_NAME,
    PROD_APP_NAME,
    PROD_EXECUTABLE_NAME,
    candidate_menubar_app_bundles,
    find_menubar_app_bundle,
    program_arguments_xml,
    resolve_menubar_launch_target,
)


def _make_app(bundle: Path, executable_name: str) -> Path:
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    macho = macos / executable_name
    macho.write_text("#!/bin/sh\n")
    macho.chmod(0o755)
    return macho


def test_candidate_order_prod_prefers_applications(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    candidates = candidate_menubar_app_bundles(
        environment="prod",
        project_root=tmp_path / "repo",
        home=home,
    )
    assert candidates[0] == Path("/Applications") / PROD_APP_NAME
    assert candidates[1] == home / "Applications" / PROD_APP_NAME
    assert candidates[2] == (tmp_path / "repo").resolve() / "dist" / PROD_APP_NAME


def test_candidate_dev_uses_testbuilds(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    candidates = candidate_menubar_app_bundles(
        environment="dev",
        project_root=root,
        home=tmp_path / "home",
    )
    assert candidates == [root.resolve() / "testbuilds" / DEV_APP_NAME]


def test_resolve_prefers_installed_prod_app(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    apps = tmp_path / "ApplicationsFake"
    apps.mkdir(parents=True)
    home.mkdir()
    root.mkdir()

    prod_app = apps / PROD_APP_NAME
    macho = _make_app(prod_app, PROD_EXECUTABLE_NAME)

    # Patch /Applications via candidate list by putting app under home/Applications
    # (first /Applications won't exist; second will).
    home_apps = home / "Applications"
    home_apps.mkdir()
    home_prod = home_apps / PROD_APP_NAME
    home_macho = _make_app(home_prod, PROD_EXECUTABLE_NAME)

    target = resolve_menubar_launch_target(
        environment="prod",
        project_root=root,
        python_bin=tmp_path / "python",
        home=home,
        data_dir=home / ".asusroutercontrol",
    )
    assert target.uses_app_bundle
    assert target.mode == "app"
    assert target.program_arguments == (str(home_macho.resolve()),)
    assert target.environment["ASUSROUTERCONTROL_APP_BUNDLE"] == str(home_prod.resolve())
    assert target.environment["ASUSROUTERCONTROL_RUNTIME_ENV"] == "prod"
    assert target.environment["DATA_DIR"] == str(home / ".asusroutercontrol")
    assert "PYTHONPATH" not in target.environment
    # unused fake /Applications path must not confuse resolution
    assert macho.exists()


def test_resolve_dev_app_from_testbuilds(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir()
    (root / "testbuilds").mkdir(parents=True)
    dev_app = root / "testbuilds" / DEV_APP_NAME
    macho = _make_app(dev_app, DEV_EXECUTABLE_NAME)

    target = resolve_menubar_launch_target(
        environment="dev",
        project_root=root,
        python_bin=tmp_path / "bin" / "python",
        home=home,
        data_dir=home / ".asusroutercontrol.dev",
        env_file=root / ".env",
        src_path=root / "src",
    )
    assert target.uses_app_bundle
    assert target.program_arguments == (str(macho.resolve()),)
    assert target.environment["ASUSROUTERCONTROL_RUNTIME_ENV"] == "dev"
    assert target.environment["DATA_DIR"] == str(home / ".asusroutercontrol.dev")
    assert target.environment["ASUSROUTERCONTROL_ENV_FILE"] == str(root / ".env")
    assert target.environment["ASUSROUTERCONTROL_APP_BUNDLE"] == str(dev_app.resolve())


def test_resolve_falls_back_to_python_module(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    src = root / "src"
    root.mkdir()
    home.mkdir()
    src.mkdir()
    python = tmp_path / "python"
    python.write_text("")

    target = resolve_menubar_launch_target(
        environment="prod",
        project_root=root,
        python_bin=python,
        home=home,
        data_dir=home / ".asusroutercontrol",
        src_path=src,
    )
    assert not target.uses_app_bundle
    assert target.mode == "python"
    assert target.app_bundle is None
    assert target.program_arguments == (
        str(python.resolve()),
        "-u",
        "-m",
        "asusroutercontrol.menubar",
    )
    assert target.environment["PYTHONPATH"] == str(src)
    assert "ASUSROUTERCONTROL_APP_BUNDLE" not in target.environment


def test_find_skips_bundle_missing_macho(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir()
    (root / "dist" / PROD_APP_NAME / "Contents" / "MacOS").mkdir(parents=True)
    # empty MacOS dir — no executable
    assert (
        find_menubar_app_bundle(environment="prod", project_root=root, home=home)
        is None
    )


def test_program_arguments_xml_escapes() -> None:
    xml = program_arguments_xml(
        ("/Applications/ASUSRouterControl.app/Contents/MacOS/ASUSRouterControl",)
    )
    assert "<string>/Applications/ASUSRouterControl.app/Contents/MacOS/ASUSRouterControl</string>" in xml
    xml2 = program_arguments_xml(("/tmp/a&b<c>.app/bin",))
    assert "&amp;" in xml2
    assert "&lt;" in xml2
    assert "&gt;" in xml2
