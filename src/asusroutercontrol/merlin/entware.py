"""Entware (opkg) package manager for AsusWRT-Merlin.

Entware is installed on a USB drive mounted at /opt.
It provides opkg, a lightweight package manager with
thousands of Linux packages compiled for ARM/MIPS routers.

Requires: USB storage plugged into the router.
Install via AMTM or manual setup script.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field

from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)

ENTWARE_BIN = "/opt/bin/opkg"
ENTWARE_INIT = "/opt/etc/init.d/rc.unslung"
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


@dataclass
class EntwareStatus:
    installed: bool
    usb_mounted: bool
    opt_path: str | None = None
    package_count: int = 0
    arch: str | None = None
    version: str | None = None


@dataclass
class Package:
    name: str
    version: str = ""
    size: str = ""
    description: str = ""
    installed: bool = False


@dataclass
class EntwareInfo:
    status: EntwareStatus
    installed_packages: list[Package] = field(default_factory=list)


def _validate_package_name(name: str) -> str:
    if not _PKG_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid package name: {name!r}")
    return name


async def get_status(ssh: RouterSSH) -> EntwareStatus:
    """Check Entware installation status."""
    usb = await ssh.run("mount | grep /tmp/mnt")
    usb_mounted = usb.ok and bool(usb.stdout)

    opkg_exists = await ssh.file_exists(ENTWARE_BIN)
    if not opkg_exists:
        return EntwareStatus(installed=False, usb_mounted=usb_mounted)

    # Get architecture
    arch_r = await ssh.run(f"{ENTWARE_BIN} print-architecture 2>/dev/null")
    arch = None
    if arch_r.ok:
        for line in arch_r.stdout.splitlines():
            if "entware" in line.lower() or "arm" in line.lower() or "mips" in line.lower():
                arch = line.split()[1] if len(line.split()) > 1 else line.strip()
                break

    # Package count
    count_r = await ssh.run(f"{ENTWARE_BIN} list-installed 2>/dev/null | wc -l")
    pkg_count = int(count_r.stdout) if count_r.ok and count_r.stdout.isdigit() else 0

    # Version
    ver_r = await ssh.run(f"{ENTWARE_BIN} --version 2>/dev/null")
    version = ver_r.stdout.split("\n")[0] if ver_r.ok else None

    return EntwareStatus(
        installed=True,
        usb_mounted=usb_mounted,
        opt_path="/opt",
        package_count=pkg_count,
        arch=arch,
        version=version,
    )


async def list_installed(ssh: RouterSSH) -> list[Package]:
    """List installed Entware packages."""
    r = await ssh.run(f"{ENTWARE_BIN} list-installed 2>/dev/null")
    if not r.ok:
        return []
    packages: list[Package] = []
    for line in r.stdout.splitlines():
        # Format: "name - version"
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            packages.append(
                Package(name=parts[0].strip(), version=parts[1].strip(), installed=True)
            )
    return packages


async def search_packages(ssh: RouterSSH, query: str) -> list[Package]:
    """Search available packages matching query."""
    q = query.strip().lower()
    if not q:
        return []
    r = await ssh.run(f"{ENTWARE_BIN} list 2>/dev/null")
    if not r.ok:
        return []
    packages: list[Package] = []
    for line in r.stdout.splitlines():
        if q not in line.lower():
            continue
        parts = line.split(" - ", 2)
        if len(parts) >= 2:
            packages.append(
                Package(
                    name=parts[0].strip(),
                    version=parts[1].strip(),
                    description=parts[2].strip() if len(parts) > 2 else "",
                )
            )
    return packages


async def install_package(ssh: RouterSSH, name: str) -> bool:
    """Install an Entware package."""
    safe_name = _validate_package_name(name)
    r = await ssh.run(f"{ENTWARE_BIN} install {shlex.quote(safe_name)}")
    if r.ok:
        log.info("Installed package: %s", safe_name)
    else:
        log.error("Failed to install %s: %s", safe_name, r.stderr)
    return r.ok


async def remove_package(ssh: RouterSSH, name: str) -> bool:
    """Remove an Entware package."""
    safe_name = _validate_package_name(name)
    r = await ssh.run(f"{ENTWARE_BIN} remove {shlex.quote(safe_name)}")
    if r.ok:
        log.info("Removed package: %s", safe_name)
    else:
        log.error("Failed to remove %s: %s", safe_name, r.stderr)
    return r.ok


async def update_feeds(ssh: RouterSSH) -> bool:
    """Update opkg package feeds."""
    r = await ssh.run(f"{ENTWARE_BIN} update")
    return r.ok


async def upgrade_packages(ssh: RouterSSH) -> str:
    """Upgrade all installed packages. Returns output."""
    r = await ssh.run(f"{ENTWARE_BIN} upgrade")
    return r.stdout if r.ok else r.stderr


async def install_entware(ssh: RouterSSH) -> str:
    """Install Entware using AMTM if available, else manual script.

    Returns output/status message.
    """
    # Check USB is mounted
    usb = await ssh.run("ls /tmp/mnt/ 2>/dev/null")
    if not usb.ok or not usb.stdout:
        return "ERROR: No USB storage detected. Plug in a USB drive and format it ext4."

    # Check if AMTM is available (Merlin's addon manager)
    amtm = await ssh.file_exists("/usr/sbin/amtm")
    if amtm:
        return (
            "AMTM is available. Install Entware via AMTM for best results:\n"
            "  1. SSH into router: ssh 13Maschine@router.asus.com\n"
            "  2. Run: amtm\n"
            "  3. Select 'ep' for Entware package manager\n"
            "  4. Follow prompts to install on USB\n"
            "This ensures proper /opt mount and init scripts."
        )

    # Fallback: direct install script
    mount_r = await ssh.run("ls /tmp/mnt/ | head -1")
    if not mount_r.stdout:
        return "ERROR: USB mounted but no partition found."

    script = (
        "wget -O - http://bin.entware.net/armv7sf-k2.6/installer/generic.sh "
        "| sh"
    )
    r = await ssh.run(script)
    return r.stdout + "\n" + r.stderr if r.stdout or r.stderr else "Install script completed."
