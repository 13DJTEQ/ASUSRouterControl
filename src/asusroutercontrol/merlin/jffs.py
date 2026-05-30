"""Minimal JFFS helpers used by security regression tests."""

from __future__ import annotations

import re

_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_script_name(name: str) -> str:
    """Validate Merlin JFFS script names for safe command usage."""
    cleaned = (name or "").strip()
    if not cleaned or not _SCRIPT_NAME_RE.fullmatch(cleaned):
        raise ValueError("Invalid script name")
    return cleaned
