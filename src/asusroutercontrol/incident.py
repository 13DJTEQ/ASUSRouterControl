"""Incident triage and recovery helpers for connectivity issues."""

from __future__ import annotations

import ipaddress
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from asusroutercontrol.models import ConnectionType


@dataclass(frozen=True)
class ConnectivityPath:
    key: str
    label: str
    service: str
    device: str
    priority: int


PATHS: dict[str, ConnectivityPath] = {
    "ethernet-primary": ConnectivityPath(
        key="ethernet-primary",
        label="Ethernet primary",
        service="Ethernet 2",
        device="en1",
        priority=1,
    ),
    "wifi-secondary": ConnectivityPath(
        key="wifi-secondary",
        label="Wi-Fi secondary",
        service="Wi-Fi",
        device="en2",
        priority=2,
    ),
    "ethernet-secondary": ConnectivityPath(
        key="ethernet-secondary",
        label="Ethernet secondary",
        service="Ethernet 1",
        device="en0",
        priority=3,
    ),
}

DEFAULT_TRACKED_MACS: dict[str, str] = {
    "ethernet-primary": "00:3e:e1:c9:2c:0b",
    "wifi-secondary": "74:1b:b2:f1:c4:31",
    "ethernet-secondary": "00:3e:e1:c9:2c:0c",
}


@dataclass
class LocalCommandResult:
    command: str
    sudo: bool
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class RepairCommand:
    argv: tuple[str, ...]
    sudo: bool = True
    sleep_after_seconds: float = 0.0


@dataclass
class PathSnapshot:
    path_key: str
    service: str
    device: str
    service_enabled: bool | None
    status_active: bool
    ip: str | None
    link_local_ip: bool
    source_ping_ok: bool | None
    dhcp_lease_ip: str | None

    @property
    def healthy(self) -> bool:
        return (
            self.status_active
            and self.ip is not None
            and not self.link_local_ip
            and self.source_ping_ok is not False
        )


@dataclass
class RouterPathSnapshot:
    path_key: str
    mac: str
    ip: str | None
    online: bool
    connection: str


@dataclass
class IncidentSnapshot:
    timestamp: float
    paths: dict[str, PathSnapshot]
    default_interface: str | None
    default_gateway: str | None
    gateway_ping_ok: bool | None
    ui_conflicts: list[str] = field(default_factory=list)
    runtime_warning: str | None = None
    router_dhcp_static_enabled: bool | None = None
    router_static_reservation_count: int | None = None
    router_paths: dict[str, RouterPathSnapshot] = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["paths"] = {k: asdict(v) for k, v in self.paths.items()}
        payload["router_paths"] = {k: asdict(v) for k, v in self.router_paths.items()}
        return payload


@dataclass
class IncidentClassification:
    category: str
    healthy_paths: list[str]
    degraded_paths: list[str]
    reasons: list[str]
    has_reporting_conflict: bool

    @property
    def has_healthy_wired(self) -> bool:
        return any(path.startswith("ethernet-") for path in self.healthy_paths)

    @property
    def has_healthy_wifi(self) -> bool:
        return "wifi-secondary" in self.healthy_paths

    def to_dict(self) -> dict:
        return asdict(self)


_IP_RE = re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b")
_GATEWAY_RE = re.compile(r"\bgateway:\s+(\d+\.\d+\.\d+\.\d+)")
_IFACE_RE = re.compile(r"\binterface:\s+(\w+)")
_LEASE_RE = re.compile(r"\byiaddr\s*=\s*(\d+\.\d+\.\d+\.\d+)")
_STATIC_ENTRY_RE = re.compile(r"<([0-9A-Fa-f:]{17})>")


def runtime_environment_warning() -> str | None:
    venv_python = Path.cwd() / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    try:
        current = Path(sys.executable).resolve()
        preferred = venv_python.resolve()
    except OSError:
        return None
    if current == preferred:
        return None
    return (
        f"Interpreter mismatch detected ({current}); prefer {preferred} -m asusroutercontrol.cli "
        "to avoid module/path drift."
    )


def run_local_command(
    command: Sequence[str],
    *,
    sudo: bool = False,
    timeout_seconds: int = 20,
) -> LocalCommandResult:
    argv = list(command)
    if sudo:
        argv = ["sudo", "-n", *argv]
    rendered = " ".join(shlex.quote(token) for token in argv)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return LocalCommandResult(
            command=rendered,
            sudo=sudo,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\ncommand timed out",
        )
    return LocalCommandResult(
        command=rendered,
        sudo=sudo,
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def is_sudo_auth_failure(result: LocalCommandResult) -> bool:
    if not result.sudo or result.ok:
        return False
    haystack = f"{result.stdout}\n{result.stderr}".lower()
    patterns = (
        "password is required",
        "a password is required",
        "incorrect password",
        "sorry, try again",
        "authentication failed",
    )
    return any(pattern in haystack for pattern in patterns)


def _usable_ipv4(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return parsed.version == 4 and not parsed.is_link_local


def _extract_first_ipv4(text: str) -> str | None:
    match = _IP_RE.search(text or "")
    return match.group(1) if match else None


def _extract_lease_ipv4(text: str) -> str | None:
    match = _LEASE_RE.search(text or "")
    return match.group(1) if match else None


def _parse_service_enabled(text: str) -> bool | None:
    raw = (text or "").strip().lower()
    if "enabled" in raw:
        return True
    if "disabled" in raw:
        return False
    return None


def _parse_default_route(text: str) -> tuple[str | None, str | None]:
    gateway_match = _GATEWAY_RE.search(text or "")
    iface_match = _IFACE_RE.search(text or "")
    gateway = gateway_match.group(1) if gateway_match else None
    iface = iface_match.group(1) if iface_match else None
    return gateway, iface


def collect_local_snapshot(
    *,
    paths: dict[str, ConnectivityPath] | None = None,
) -> IncidentSnapshot:
    path_defs = paths or PATHS
    route_result = run_local_command(["route", "-n", "get", "default"])
    default_gateway, default_interface = _parse_default_route(route_result.stdout)
    gateway_ping_ok: bool | None = None
    if default_gateway:
        gateway_ping_ok = run_local_command(
            ["ping", "-c", "1", default_gateway],
            timeout_seconds=6,
        ).ok

    path_snapshots: dict[str, PathSnapshot] = {}
    ui_conflicts: list[str] = []

    for path_key in sorted(path_defs, key=lambda k: path_defs[k].priority):
        path = path_defs[path_key]
        enabled_result = run_local_command(
            ["networksetup", "-getnetworkserviceenabled", path.service]
        )
        service_enabled = _parse_service_enabled(enabled_result.stdout)

        ifconfig_result = run_local_command(["ifconfig", path.device])
        ipconfig_result = run_local_command(["ipconfig", "getifaddr", path.device])
        packet_result = run_local_command(
            ["ipconfig", "getpacket", path.device],
            timeout_seconds=8,
        )

        ifconfig_ip = _extract_first_ipv4(ifconfig_result.stdout)
        ipconfig_ip = ipconfig_result.stdout.strip() if ipconfig_result.ok else ""
        ip_addr = ipconfig_ip or ifconfig_ip
        ip_addr = ip_addr if ip_addr else None
        link_local = bool(ip_addr and ip_addr.startswith("169.254."))
        lease_ip = _extract_lease_ipv4(packet_result.stdout)
        status_active = "status: active" in (ifconfig_result.stdout or "").lower()

        source_ping_ok: bool | None = None
        if _usable_ipv4(ip_addr) and default_gateway:
            source_ping_ok = run_local_command(
                ["ping", "-c", "1", "-S", ip_addr, default_gateway],
                timeout_seconds=6,
            ).ok

        snapshot = PathSnapshot(
            path_key=path_key,
            service=path.service,
            device=path.device,
            service_enabled=service_enabled,
            status_active=status_active,
            ip=ip_addr,
            link_local_ip=link_local,
            source_ping_ok=source_ping_ok,
            dhcp_lease_ip=lease_ip,
        )
        path_snapshots[path_key] = snapshot

        if service_enabled is False and _usable_ipv4(ip_addr):
            ui_conflicts.append(
                f"{path.service} reports disabled while {path.device} has usable IP {ip_addr}."
            )

    wifi_power_result = run_local_command(["networksetup", "-getairportpower", "en2"])
    wifi_power_off = "off" in (wifi_power_result.stdout or "").lower()
    wifi_path = path_snapshots.get("wifi-secondary")
    if wifi_power_off and wifi_path and _usable_ipv4(wifi_path.ip):
        ui_conflicts.append(
            "Wi-Fi power reports off while en2 has usable DHCP connectivity."
        )

    return IncidentSnapshot(
        timestamp=time.time(),
        paths=path_snapshots,
        default_interface=default_interface,
        default_gateway=default_gateway,
        gateway_ping_ok=gateway_ping_ok,
        ui_conflicts=ui_conflicts,
        runtime_warning=runtime_environment_warning(),
    )


async def collect_router_observations(
    backend,
    ssh,
    *,
    tracked_macs: dict[str, str] | None = None,
) -> tuple[bool | None, int | None, dict[str, RouterPathSnapshot]]:
    tracked = tracked_macs or DEFAULT_TRACKED_MACS
    static_enabled: bool | None = None
    static_count: int | None = None
    router_paths: dict[str, RouterPathSnapshot] = {}

    static_flag = await ssh.run("nvram get dhcp_static_x")
    static_list = await ssh.run("nvram get dhcp_staticlist")
    if static_flag.ok:
        static_enabled = (static_flag.stdout or "").strip() == "1"
    if static_list.ok:
        static_count = len(_STATIC_ENTRY_RE.findall(static_list.stdout or ""))

    devices = await backend.get_connected_devices()
    by_mac = {(device.mac or "").lower(): device for device in devices}

    for path_key, mac in tracked.items():
        device = by_mac.get(mac.lower())
        if not device:
            router_paths[path_key] = RouterPathSnapshot(
                path_key=path_key,
                mac=mac.lower(),
                ip=None,
                online=False,
                connection=ConnectionType.UNKNOWN.value,
            )
            continue
        router_paths[path_key] = RouterPathSnapshot(
            path_key=path_key,
            mac=(device.mac or mac).lower(),
            ip=device.ip,
            online=bool(device.is_online),
            connection=device.connection.value,
        )

    return static_enabled, static_count, router_paths


def attach_router_observations(
    snapshot: IncidentSnapshot,
    *,
    static_enabled: bool | None,
    static_count: int | None,
    router_paths: dict[str, RouterPathSnapshot],
) -> IncidentSnapshot:
    snapshot.router_dhcp_static_enabled = static_enabled
    snapshot.router_static_reservation_count = static_count
    snapshot.router_paths = router_paths
    return snapshot


def classify_snapshot(snapshot: IncidentSnapshot) -> IncidentClassification:
    healthy_paths: list[str] = []
    degraded_paths: list[str] = []
    reasons: list[str] = []

    for path_key, path in snapshot.paths.items():
        if path.healthy:
            healthy_paths.append(path_key)
        else:
            degraded_paths.append(path_key)

    has_reporting_conflict = bool(snapshot.ui_conflicts)

    if degraded_paths:
        if healthy_paths:
            category = "path-isolated"
            reasons.append(
                f"Healthy paths={sorted(healthy_paths)}; degraded paths={sorted(degraded_paths)}."
            )
        else:
            category = "global-outage"
            reasons.append("No path met link+IP+gateway health criteria.")
    elif has_reporting_conflict:
        category = "reporting-only"
        reasons.append("Packet/lease data healthy but UI/service signals conflict.")
    else:
        category = "healthy"
        reasons.append("All monitored paths met health checks.")

    if snapshot.default_interface:
        reasons.append(
            "default route uses "
            f"{snapshot.default_interface} via {snapshot.default_gateway or '-'}."
        )
    if snapshot.gateway_ping_ok is False:
        reasons.append("Gateway probe failed.")

    for conflict in snapshot.ui_conflicts:
        reasons.append(conflict)

    for path_key in degraded_paths:
        local = snapshot.paths[path_key]
        remote = snapshot.router_paths.get(path_key)
        if (
            local.status_active
            and local.link_local_ip
            and remote is not None
            and remote.ip is None
        ):
            reasons.append(
                f"{local.device} is link-active with link-local IP, and router has "
                f"no active lease for {remote.mac}."
            )

    if snapshot.runtime_warning:
        reasons.append(snapshot.runtime_warning)

    return IncidentClassification(
        category=category,
        healthy_paths=sorted(healthy_paths),
        degraded_paths=sorted(degraded_paths),
        reasons=reasons,
        has_reporting_conflict=has_reporting_conflict,
    )


def build_repair_stage_commands(
    *,
    stage: str,
    path_key: str,
    allow_global_reset: bool = False,
) -> list[RepairCommand]:
    stage_key = stage.upper().strip()
    if stage_key not in {"A", "B", "C"}:
        raise ValueError("stage must be one of A, B, C")
    if path_key not in PATHS:
        raise ValueError(f"unknown path '{path_key}'")

    path = PATHS[path_key]
    commands: list[RepairCommand] = []

    if stage_key == "A":
        commands.append(
            RepairCommand(
                ("networksetup", "-setnetworkserviceenabled", path.service, "on")
            )
        )
        commands.append(RepairCommand(("networksetup", "-setdhcp", path.service)))
        if path.device == "en2":
            commands.append(
                RepairCommand(
                    ("networksetup", "-setairportpower", "en2", "off"),
                    sleep_after_seconds=2.0,
                )
            )
            commands.append(
                RepairCommand(
                    ("networksetup", "-setairportpower", "en2", "on"),
                    sleep_after_seconds=2.0,
                )
            )
        else:
            commands.append(
                RepairCommand(
                    ("ifconfig", path.device, "down"),
                    sleep_after_seconds=1.0,
                )
            )
            commands.append(
                RepairCommand(
                    ("ifconfig", path.device, "up"),
                    sleep_after_seconds=1.0,
                )
            )
    elif stage_key == "B":
        commands.append(
            RepairCommand(
                ("networksetup", "-setnetworkserviceenabled", path.service, "on")
            )
        )
        commands.append(
            RepairCommand(
                ("ipconfig", "set", path.device, "NONE"),
                sleep_after_seconds=1.0,
            )
        )
        commands.append(
            RepairCommand(
                ("ipconfig", "set", path.device, "DHCP"),
                sleep_after_seconds=2.0,
            )
        )
        commands.append(
            RepairCommand(
                ("networksetup", "-setdhcp", path.service),
                sleep_after_seconds=1.0,
            )
        )
    else:
        if allow_global_reset:
            commands.append(
                RepairCommand(
                    ("launchctl", "kickstart", "-k", "system/com.apple.configd"),
                    sleep_after_seconds=2.0,
                )
            )
        commands.append(
            RepairCommand(
                ("networksetup", "-setnetworkserviceenabled", path.service, "on")
            )
        )
        if path.device == "en2":
            commands.append(
                RepairCommand(
                    ("networksetup", "-setairportpower", "en2", "on"),
                    sleep_after_seconds=1.0,
                )
            )
        commands.append(
            RepairCommand(
                ("ipconfig", "set", path.device, "DHCP"),
                sleep_after_seconds=2.0,
            )
        )
        commands.append(RepairCommand(("networksetup", "-setdhcp", path.service)))

    return commands


def execute_repair_commands(
    commands: Sequence[RepairCommand],
    *,
    stop_on_error: bool = True,
) -> list[LocalCommandResult]:
    results: list[LocalCommandResult] = []
    for command in commands:
        result = run_local_command(command.argv, sudo=command.sudo)
        results.append(result)
        if command.sleep_after_seconds > 0:
            time.sleep(command.sleep_after_seconds)
        if stop_on_error and not result.ok:
            break
    return results

