"""DHCP reservation profile loader.

Profiles drive the ``asusrouter dhcp reserve-<profile>`` shortcut commands.
They are stored in TOML format so users can customise them without editing
Python source code.

Loading order
-------------
1. ``~/.asusroutercontrol/dhcp_profiles.toml`` (user config, takes precedence)
2. ``<package>/dhcp_profiles.example.toml`` (packaged defaults, always present)

If both files exist, the user file is used exclusively.  If neither file
exists (unusual), an empty profile registry is returned.

Profile schema
--------------
Each ``[[profiles]]`` entry must contain:

  key        str  — unique identifier (used in CLI commands and triggered_by events)
  label      str  — human-readable name for display
  mac        str  — device MAC address (normalised on load)
  default_ip str  — desired static IP

Optional:
  hostname   str  — dnsmasq hostname label (default: key)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_EXAMPLE_TOML = Path(__file__).parent / "dhcp_profiles.example.toml"
_USER_TOML_NAME = "dhcp_profiles.toml"


def _load_toml(path: Path) -> dict:
    """Load a TOML file using tomllib (3.11+) or tomli."""
    if sys.version_info >= (3, 11):
        import tomllib

        with open(path, "rb") as fh:
            return tomllib.load(fh)
    else:
        try:
            import tomli

            with open(path, "rb") as fh:
                return tomli.load(fh)
        except ImportError as exc:
            raise ImportError(
                "Install 'tomli' for Python <3.11 TOML support: pip install tomli"
            ) from exc


@dataclass(frozen=True)
class DhcpProfile:
    """A single DHCP reservation profile."""

    key: str
    label: str
    mac: str
    default_ip: str
    hostname: str

    @property
    def triggered_by_reserve(self) -> str:
        return f"dhcp:reserve-{self.key.replace('_', '-')}"

    @property
    def triggered_by_unreserve(self) -> str:
        return f"dhcp:unreserve-{self.key.replace('_', '-')}"


def _parse_profiles(data: dict, source: str) -> dict[str, DhcpProfile]:
    """Parse raw TOML data into a {key: DhcpProfile} dict."""
    from asusroutercontrol.dhcp_reservations import normalize_ipv4, normalize_mac

    profiles: dict[str, DhcpProfile] = {}
    for entry in data.get("profiles", []):
        try:
            key = str(entry["key"]).strip()
            mac = normalize_mac(str(entry["mac"]))
            ip = normalize_ipv4(str(entry["default_ip"]))
            label = str(entry.get("label", key))
            hostname = str(entry.get("hostname", key))
            profiles[key] = DhcpProfile(
                key=key,
                label=label,
                mac=mac,
                default_ip=ip,
                hostname=hostname,
            )
        except (KeyError, ValueError) as exc:
            log.warning("Skipping invalid profile entry in %s: %s — %s", source, entry, exc)
    return profiles


def load_dhcp_profiles(data_dir: Path | None = None) -> dict[str, DhcpProfile]:
    """Return the DHCP reservation profile registry.

    Args:
        data_dir: The user config directory (``~/.asusroutercontrol`` by default).
                  If a ``dhcp_profiles.toml`` exists here it takes full precedence.

    Returns:
        Mapping of profile *key* → :class:`DhcpProfile`.
    """
    if data_dir is None:
        data_dir = Path.home() / ".asusroutercontrol"

    user_toml = data_dir / _USER_TOML_NAME
    if user_toml.exists():
        try:
            data = _load_toml(user_toml)
            profiles = _parse_profiles(data, str(user_toml))
            log.debug("Loaded %d DHCP profiles from %s", len(profiles), user_toml)
            return profiles
        except Exception as exc:
            log.error("Failed to load %s: %s — falling back to packaged defaults", user_toml, exc)

    if _EXAMPLE_TOML.exists():
        try:
            data = _load_toml(_EXAMPLE_TOML)
            profiles = _parse_profiles(data, str(_EXAMPLE_TOML))
            log.debug("Loaded %d DHCP profiles from packaged defaults", len(profiles))
            return profiles
        except Exception as exc:
            log.error("Failed to load packaged DHCP profiles: %s", exc)

    log.warning("No DHCP profile file found — profile registry is empty")
    return {}


def install_user_profiles(data_dir: Path | None = None, *, overwrite: bool = False) -> Path:
    """Copy the packaged example file to the user config directory.

    Args:
        data_dir: Target directory (defaults to ``~/.asusroutercontrol``).
        overwrite: If True, replace an existing user file.

    Returns:
        Path to the installed user profiles file.
    """
    if data_dir is None:
        data_dir = Path.home() / ".asusroutercontrol"

    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / _USER_TOML_NAME

    if dest.exists() and not overwrite:
        log.info("User profiles file already exists: %s", dest)
        return dest

    import shutil

    shutil.copy2(_EXAMPLE_TOML, dest)
    log.info("Installed DHCP profiles file: %s", dest)
    return dest
