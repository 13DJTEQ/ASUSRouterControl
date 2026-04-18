"""DHCP reservation profile definitions.

This module centralizes device-specific DHCP reservation profiles
that were previously hardcoded in cli.py.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DHCPProfile",
    "BUILTIN_PROFILES",
    "get_profile",
    "get_profile_field",
    "install_user_profiles",
    "load_dhcp_profiles",
]

_EXAMPLE_TOML = Path(__file__).parent / "dhcp_profiles.example.toml"
_USER_TOML_NAME = "dhcp_profiles.toml"


@dataclass(frozen=True)
class DHCPProfile:
    """A DHCP reservation profile for a known device."""

    key: str
    label: str
    mac: str
    hostname: str
    default_ip: str
    triggered_by_reserve: str
    triggered_by_unreserve: str


# Built-in device profiles (can be overridden by user TOML)
BUILTIN_PROFILES: dict[str, DHCPProfile] = {
    "macpro_primary": DHCPProfile(
        key="macpro_primary",
        label="MacPro12Core primary",
        mac="74:1b:b2:f1:c4:31",
        hostname="MacPro12Core",
        default_ip="192.168.1.240",
        triggered_by_reserve="dhcp:reserve-macpro-primary",
        triggered_by_unreserve="dhcp:unreserve-macpro-primary",
    ),
    "denon_second_port": DHCPProfile(
        key="denon_second_port",
        label="Denon second ethernet port",
        mac="00:05:cd:d4:a5:3c",
        hostname="Denon150",
        default_ip="192.168.1.241",
        triggered_by_reserve="dhcp:reserve-denon-second-port",
        triggered_by_unreserve="dhcp:unreserve-denon-second-port",
    ),
    "macpro_lan2": DHCPProfile(
        key="macpro_lan2",
        label="MacPro Ethernet 2",
        mac="00:3e:e1:c9:2c:0b",
        hostname="MacPro12Core-LAN2",
        default_ip="192.168.1.242",
        triggered_by_reserve="dhcp:reserve-macpro-lan2",
        triggered_by_unreserve="dhcp:unreserve-macpro-lan2",
    ),
    "macpro_lan1": DHCPProfile(
        key="macpro_lan1",
        label="MacPro Ethernet 1",
        mac="00:3e:e1:c9:2c:0c",
        hostname="MacPro12Core-LAN1",
        default_ip="192.168.1.243",
        triggered_by_reserve="dhcp:reserve-macpro-lan1",
        triggered_by_unreserve="dhcp:unreserve-macpro-lan1",
    ),
}

# Module-level cache for loaded profiles
_loaded_profiles: dict[str, DHCPProfile] | None = None


def load_dhcp_profiles(data_dir: Path | None = None) -> dict[str, DHCPProfile]:
    """Load DHCP profiles from user TOML file, falling back to built-in defaults.

    Args:
        data_dir: Optional data directory containing dhcp_profiles.toml.
                  If None, uses ~/.asusroutercontrol/

    Returns:
        Dictionary of profile_key -> DHCPProfile
    """
    global _loaded_profiles
    if _loaded_profiles is not None:
        return _loaded_profiles

    profiles = dict(BUILTIN_PROFILES)

    # Try loading user overrides from TOML
    if data_dir is None:
        data_dir = Path.home() / ".asusroutercontrol"

    toml_path = data_dir / "dhcp_profiles.toml"
    if toml_path.exists():
        try:
            with open(toml_path, "rb") as f:
                user_data = tomllib.load(f)
            for key, values in user_data.get("profiles", {}).items():
                profiles[key] = DHCPProfile(
                    key=key,
                    label=values.get("label", key),
                    mac=values["mac"],
                    hostname=values.get("hostname", ""),
                    default_ip=values["default_ip"],
                    triggered_by_reserve=values.get(
                        "triggered_by_reserve", f"dhcp:reserve-{key}"
                    ),
                    triggered_by_unreserve=values.get(
                        "triggered_by_unreserve", f"dhcp:unreserve-{key}"
                    ),
                )
        except Exception:
            pass  # Fall back to built-in profiles on any error

    _loaded_profiles = profiles
    return profiles


def get_profile(profile_key: str) -> DHCPProfile:
    """Get a DHCP profile by key.

    Raises:
        KeyError: If the profile key is not found.
    """
    profiles = load_dhcp_profiles()
    if profile_key not in profiles:
        raise KeyError(f"Unknown DHCP reservation profile: {profile_key}")
    return profiles[profile_key]


def install_user_profiles(
    data_dir: Path | None = None, *, overwrite: bool = False
) -> Path:
    """Copy the packaged example TOML to the user config directory.

    Args:
        data_dir: Target directory (defaults to ``~/.asusroutercontrol``).
        overwrite: If True, replace an existing user file.

    Returns:
        Path to the (existing or newly installed) user profiles file.
    """
    import shutil

    if data_dir is None:
        data_dir = Path.home() / ".asusroutercontrol"

    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / _USER_TOML_NAME

    if dest.exists() and not overwrite:
        return dest

    shutil.copy2(_EXAMPLE_TOML, dest)
    return dest


def get_profile_field(profile_key: str, field: str) -> str:
    """Get a specific field from a DHCP profile.

    Args:
        profile_key: The profile identifier.
        field: Field name (label, mac, hostname, default_ip, etc.)

    Returns:
        The field value as a string.

    Raises:
        KeyError: If profile or field not found.
    """
    profile = get_profile(profile_key)
    value = getattr(profile, field, None)
    if value is None:
        raise KeyError(f"Missing profile field: {profile_key}.{field}")
    return value
