"""Security analysis — firmware version check and port audit."""

from __future__ import annotations

import logging
import re

from asusroutercontrol.backends.base import FirmwareBackend

log = logging.getLogger(__name__)

# Known vulnerable firmware versions for RT-AC68U (simplified check)
KNOWN_VULNERABLE = {
    "3.0.0.4.386_51255": ["CVE-2022-26376", "CVE-2022-35401"],
    "3.0.0.4.386_50460": ["CVE-2022-26376"],
}

LATEST_STOCK = "3.0.0.4.386_52062"
LATEST_MERLIN = "386.14_2"


def check_firmware_status(version: str | None) -> dict:
    """Compare current firmware against known versions."""
    if not version:
        return {"status": "unknown", "message": "Firmware version unavailable"}

    vulns = KNOWN_VULNERABLE.get(version, [])
    if vulns:
        return {
            "status": "vulnerable",
            "version": version,
            "cves": vulns,
            "message": f"Known vulnerabilities: {', '.join(vulns)}",
        }

    # Extract numeric portion for comparison
    nums = re.findall(r"\d+", version)
    ver_str = "".join(nums)

    latest_nums = re.findall(r"\d+", LATEST_STOCK)
    latest_str = "".join(latest_nums)

    if ver_str >= latest_str:
        return {"status": "current", "version": version, "message": "Up to date"}

    return {
        "status": "outdated",
        "version": version,
        "latest_stock": LATEST_STOCK,
        "latest_merlin": LATEST_MERLIN,
        "message": f"Update available: stock {LATEST_STOCK}, Merlin {LATEST_MERLIN}",
    }


async def get_security_report(backend: FirmwareBackend) -> dict:
    """Generate a security posture report."""
    sys_info = await backend.get_system_info()
    fw_status = check_firmware_status(sys_info.firmware_version)

    port_rules = await backend.get_port_forwarding()
    open_ports = [r for r in port_rules if r.enabled]

    return {
        "firmware": fw_status,
        "port_forwarding": {
            "total_rules": len(port_rules),
            "active_rules": len(open_ports),
            "rules": [r.model_dump() for r in open_ports],
        },
        "recommendations": _build_recommendations(fw_status, open_ports),
    }


def _build_recommendations(fw: dict, open_ports: list) -> list[str]:
    recs = []
    if fw.get("status") == "vulnerable":
        recs.append(f"CRITICAL: Update firmware immediately — {fw.get('message')}")
    elif fw.get("status") == "outdated":
        recs.append(f"Update firmware: {fw.get('message')}")
    if len(open_ports) > 5:
        recs.append(f"Review port forwarding: {len(open_ports)} active rules is high")
    return recs
