"""JFFS custom scripts manager for AsusWRT-Merlin.

Merlin supports user scripts in /jffs/scripts/ that hook into
various system events. Scripts must be executable (chmod +x).
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass

from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)

SCRIPTS_DIR = "/jffs/scripts"
_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Well-known Merlin script hooks and their trigger descriptions
MERLIN_HOOKS: dict[str, str] = {
    "init-start": "Early boot, before any services start",
    "pre-mount": "Before a USB partition is mounted (arg: mount point)",
    "post-mount": "After a USB partition is mounted (arg: mount point)",
    "services-start": "After all services have started at boot",
    "services-stop": "Before all services are stopped at shutdown",
    "wan-start": "WAN interface is up (arg: WAN unit)",
    "wan-event": "WAN event occurred (args: WAN unit, event type)",
    "firewall-start": "Firewall has been (re)started (args: WAN IP, WAN iface)",
    "nat-start": "NAT rules have been applied (arg: WAN iface)",
    "mount-start": "Before Entware init script runs",
    "unmount": "Before a USB partition is unmounted (arg: mount point)",
    "dhcpc-event": "DHCP client event (args: iface, event)",
    "openvpn-event": "OpenVPN event (args: see Merlin wiki)",
    "ddns-start": "Before DDNS update (return 1 to skip built-in)",
    "update-notification": "Firmware update available",
    "dnsmasq.postconf": "After dnsmasq config generated (arg: config path)",
}


@dataclass
class ScriptInfo:
    name: str
    path: str
    executable: bool
    size: int
    hook_description: str | None = None
    content: str | None = None


def _validate_script_name(name: str) -> str:
    if not _SCRIPT_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid script name: {name!r}")
    return name


async def is_jffs_enabled(ssh: RouterSSH) -> bool:
    """Check if JFFS partition is mounted and writable."""
    r = await ssh.run("nvram get jffs2_on")
    return r.stdout == "1"


async def list_scripts(ssh: RouterSSH) -> list[ScriptInfo]:
    """List all scripts in /jffs/scripts/."""
    r = await ssh.run(f"ls -la {SCRIPTS_DIR}/ 2>/dev/null")
    if not r.ok or not r.stdout:
        return []

    scripts: list[ScriptInfo] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 9 or parts[0].startswith("total") or parts[0].startswith("d"):
            continue
        perms = parts[0]
        size = int(parts[4]) if parts[4].isdigit() else 0
        name = parts[8]
        is_exec = "x" in perms
        scripts.append(
            ScriptInfo(
                name=name,
                path=f"{SCRIPTS_DIR}/{name}",
                executable=is_exec,
                size=size,
                hook_description=MERLIN_HOOKS.get(name),
            )
        )
    return scripts


async def read_script(ssh: RouterSSH, name: str) -> ScriptInfo | None:
    """Read a script's content."""
    name = _validate_script_name(name)
    path = f"{SCRIPTS_DIR}/{name}"
    content = await ssh.read_file(path)
    if content is None:
        return None
    qpath = shlex.quote(path)
    r = await ssh.run(f"[ -x {qpath} ] && echo yes || echo no")
    r_size = await ssh.run(f"wc -c < {qpath}")
    r_size = await ssh.run(f"wc -c < {path}")

    return ScriptInfo(
        name=name,
        path=path,
        executable=r.stdout == "yes",
        size=int(r_size.stdout) if r_size.ok and r_size.stdout.isdigit() else 0,
        hook_description=MERLIN_HOOKS.get(name),
        content=content,
    )


async def write_script(ssh: RouterSSH, name: str, content: str) -> bool:
    """Write a script to /jffs/scripts/ and make it executable."""
    name = _validate_script_name(name)
    path = f"{SCRIPTS_DIR}/{name}"
    # Ensure scripts directory exists
    await ssh.run(f"mkdir -p {SCRIPTS_DIR}")

    ok = await ssh.write_file(path, content)
    if ok:
        await ssh.run(f"chmod +x {shlex.quote(path)}", check=True)
        log.info("Wrote script: %s", path)
    return ok


async def enable_script(ssh: RouterSSH, name: str) -> bool:
    """Make a script executable."""
    name = _validate_script_name(name)
    r = await ssh.run(f"chmod +x {shlex.quote(f'{SCRIPTS_DIR}/{name}')}")
    return r.ok


async def disable_script(ssh: RouterSSH, name: str) -> bool:
    """Remove execute permission from a script (keeps file)."""
    name = _validate_script_name(name)
    r = await ssh.run(f"chmod -x {shlex.quote(f'{SCRIPTS_DIR}/{name}')}")
    return r.ok


async def delete_script(ssh: RouterSSH, name: str) -> bool:
    """Delete a script from /jffs/scripts/."""
    name = _validate_script_name(name)
    path = f"{SCRIPTS_DIR}/{name}"
    exists = await ssh.file_exists(path)
    if not exists:
        return False
    r = await ssh.run(f"rm {shlex.quote(path)}")
    if r.ok:
        log.info("Deleted script: %s", path)
    return r.ok


async def create_hook_script(
    ssh: RouterSSH, hook: str, commands: list[str]
) -> bool:
    """Create a script for a known Merlin hook with given commands."""
    if hook not in MERLIN_HOOKS:
        log.warning("Unknown hook: %s", hook)
    content = "#!/bin/sh\n" + "\n".join(commands) + "\n"
    return await write_script(ssh, hook, content)
