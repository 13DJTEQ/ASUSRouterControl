from __future__ import annotations

import pytest

from asusroutercontrol import dhcp_profiles


def test_install_user_profiles_copies_packaged_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged_defaults = tmp_path / "dhcp_profiles.example.toml"
    packaged_defaults.write_text(
        "[[profiles]]\n"
        'key = "sample"\n'
        'label = "Sample"\n'
        'mac = "aa:bb:cc:dd:ee:ff"\n'
        'default_ip = "192.168.1.10"\n'
        'hostname = "sample"\n'
    )
    monkeypatch.setattr(dhcp_profiles, "_EXAMPLE_TOML", packaged_defaults)

    data_dir = tmp_path / "data"
    installed_path = dhcp_profiles.install_user_profiles(data_dir=data_dir)
    expected_path = data_dir / "dhcp_profiles.toml"

    assert installed_path == expected_path
    assert expected_path.exists()
    assert expected_path.read_text() == packaged_defaults.read_text()


def test_install_user_profiles_fails_when_packaged_defaults_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_defaults = tmp_path / "missing-dhcp-profiles.example.toml"
    monkeypatch.setattr(dhcp_profiles, "_EXAMPLE_TOML", missing_defaults)

    with pytest.raises(FileNotFoundError, match="missing-dhcp-profiles\\.example\\.toml"):
        dhcp_profiles.install_user_profiles(data_dir=tmp_path / "data")
