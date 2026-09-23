"""Helpers for menubar launchd ProgramArguments / app-bundle resolution.

Sequoia ControlCenter often hides NSStatusItem when launchd runs bare
``python -m asusroutercontrol.menubar``. Prefer a real .app Mach-O
executable when one is installed or built locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from asusroutercontrol.config import default_data_dir_for_runtime, normalize_runtime_environment

PROD_APP_NAME = "ASUSRouterControl.app"
PROD_EXECUTABLE_NAME = "ASUSRouterControl"
DEV_APP_NAME = "ASUSRouterControl DEV.app"
DEV_EXECUTABLE_NAME = "ASUSRouterControlDevRuntime"


@dataclass(frozen=True)
class MenubarLaunchTarget:
    """Resolved launchd ProgramArguments + EnvironmentVariables payload."""

    program_arguments: tuple[str, ...]
    environment: dict[str, str]
    app_bundle: Path | None
    mode: str  # "app" | "python"

    @property
    def uses_app_bundle(self) -> bool:
        return self.mode == "app" and self.app_bundle is not None


def menubar_app_executable_name(environment: str) -> str:
    env = normalize_runtime_environment(environment)
    return DEV_EXECUTABLE_NAME if env == "dev" else PROD_EXECUTABLE_NAME


def candidate_menubar_app_bundles(
    *,
    environment: str,
    project_root: Path,
    home: Path | None = None,
) -> list[Path]:
    """Return ordered .app bundle candidates for the given runtime environment."""
    env = normalize_runtime_environment(environment)
    root = project_root.expanduser().resolve()
    home_dir = (home or Path.home()).expanduser()

    if env == "dev":
        return [root / "testbuilds" / DEV_APP_NAME]

    return [
        Path("/Applications") / PROD_APP_NAME,
        home_dir / "Applications" / PROD_APP_NAME,
        root / "dist" / PROD_APP_NAME,
    ]


def resolve_menubar_app_macho(app_bundle: Path, *, environment: str) -> Path | None:
    """Return the Mach-O executable inside *app_bundle* when present."""
    exe_name = menubar_app_executable_name(environment)
    macho = app_bundle / "Contents" / "MacOS" / exe_name
    if macho.is_file():
        return macho
    return None


def find_menubar_app_bundle(
    *,
    environment: str,
    project_root: Path,
    home: Path | None = None,
) -> tuple[Path, Path] | None:
    """Return ``(app_bundle, macho_executable)`` for the first usable candidate."""
    for bundle in candidate_menubar_app_bundles(
        environment=environment,
        project_root=project_root,
        home=home,
    ):
        if not bundle.is_dir():
            continue
        macho = resolve_menubar_app_macho(bundle, environment=environment)
        if macho is not None:
            return bundle.resolve(), macho.resolve()
    return None


def resolve_menubar_launch_target(
    *,
    environment: str,
    project_root: Path,
    python_bin: Path,
    home: Path | None = None,
    data_dir: Path | None = None,
    env_file: Path | None = None,
    src_path: Path | None = None,
) -> MenubarLaunchTarget:
    """Prefer a .app Mach-O; fall back to ``python -m asusroutercontrol.menubar``."""
    env = normalize_runtime_environment(environment)
    resolved_data_dir = (
        data_dir.expanduser()
        if data_dir is not None
        else default_data_dir_for_runtime(env)
    )

    env_vars: dict[str, str] = {
        "ASUSROUTERCONTROL_RUNTIME_ENV": env,
        "DATA_DIR": str(resolved_data_dir),
    }
    if env_file is not None:
        env_vars["ASUSROUTERCONTROL_ENV_FILE"] = str(env_file)

    found = find_menubar_app_bundle(
        environment=env,
        project_root=project_root,
        home=home,
    )
    if found is not None:
        app_bundle, macho = found
        env_vars["ASUSROUTERCONTROL_APP_BUNDLE"] = str(app_bundle)
        return MenubarLaunchTarget(
            program_arguments=(str(macho),),
            environment=env_vars,
            app_bundle=app_bundle,
            mode="app",
        )

    # Python module fallback — Sequoia may hide the status item.
    if src_path is not None and src_path.exists():
        env_vars["PYTHONPATH"] = str(src_path)
    return MenubarLaunchTarget(
        program_arguments=(
            str(Path(python_bin).resolve()),
            "-u",
            "-m",
            "asusroutercontrol.menubar",
        ),
        environment=env_vars,
        app_bundle=None,
        mode="python",
    )


def program_arguments_xml(program_arguments: tuple[str, ...] | list[str]) -> str:
    """Render ProgramArguments array entries for a launchd plist."""
    lines = []
    for arg in program_arguments:
        escaped = (
            str(arg)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        lines.append(f"        <string>{escaped}</string>")
    return "\n".join(lines)


def environment_variables_xml(environment: dict[str, str]) -> str:
    """Render EnvironmentVariables dict entries for a launchd plist."""
    parts: list[str] = []
    for key, value in environment.items():
        safe_key = (
            str(key)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        safe_value = (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        parts.append(f"        <key>{safe_key}</key>\n        <string>{safe_value}</string>\n")
    return "".join(parts)
