"""Minimal Entware helpers used by security regression tests."""

from __future__ import annotations

import re

_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")


def _validate_package_name(name: str) -> str:
    """Validate an Entware package name for shell-safe usage."""
    cleaned = (name or "").strip()
    if not cleaned or not _PACKAGE_NAME_RE.fullmatch(cleaned):
        raise ValueError("Invalid package name")
    return cleaned
