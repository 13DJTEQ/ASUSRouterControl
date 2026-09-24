"""Allow ``python -m asusroutercontrol.cli`` (CI package-smoke)."""

from __future__ import annotations

from asusroutercontrol.cli import cli

if __name__ == "__main__":
    cli(prog_name="asusrouter")
