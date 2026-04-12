"""CLI interface for ASUSRouterControl.

This module is being refactored. Command groups are being extracted to:
- cli/dhcp.py - DHCP reservation management
- cli/optimize.py - Router optimization commands
- cli/incident.py - Incident triage (TODO)
- cli/scripts.py, cli/entware.py, cli/menubar.py, cli/scheduler.py (TODO)
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time

import click
from rich.console import Console
from rich.table import Table

# Import extracted CLI submodules
from asusroutercontrol.cli.dhcp import dhcp_group
from asusroutercontrol.cli.optimize import optimize_group
from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import (
    delete_legacy_credentials,
    get_router_credentials,
    migrate_legacy_credentials,
    store_credential,
)
from asusroutercontrol.datastore import DataStore

console = Console()
_DHCP_EVENT_RE = re.compile(
    r"dnsmasq-dhcp\[\d+\]: "
    r"(DHCPDISCOVER|DHCPOFFER|DHCPREQUEST|DHCPACK|DHCPNAK)\(br0\)\s*"
    r"(?:(\d+\.\d+\.\d+\.\d+)\s+)?([0-9A-Fa-f:]{17})(?:\s+(.+))?"
)
_AUTH_EVENT_RE = re.compile(
    r"wlceventd_proc_event\(\d+\):\s+"
    r"(eth\d):\s+(Auth|Assoc|Disassoc|Deauth)\s+([0-9A-F:]{17}),\s+"
    r"status:\s+([^,]+)(?:,\s+reason:\s+(.+?))?(?:,\s+rssi:.*)?$"
)


def _get_backend():
    """Create and return a configured firmware backend instance."""
    from asusroutercontrol.backends.factory import create_backend

    cfg = load_config()
    username, password = get_router_credentials()
    if not username or not password:
        console.print(
            "[red]Router credentials not configured.[/red] Run: [bold]asusrouter setup[/bold]"
        )
        sys.exit(1)
    try:
        return create_backend(cfg, username=username, password=password)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    m = mac.strip().replace("-", ":").lower()
    parts = m.split(":")
    if len(parts) != 6 or any(len(p) != 2 for p in parts):
        raise click.BadParameter("MAC must look like AA:BB:CC:DD:EE:FF")
    try:
        int("".join(parts), 16)
    except ValueError as e:
        raise click.BadParameter("MAC must contain only hex bytes") from e
    return m


def _parse_live_event(line: str) -> dict | None:
    ts = " ".join(line.split()[:3]) if len(line.split()) >= 3 else ""

    m = _DHCP_EVENT_RE.search(line)
    if m:
        event, ip, mac, rest = m.groups()
        host = None
        if rest:
            host_token = rest.strip().split()[0]
            if host_token and host_token != "*":
                host = host_token
        return {
            "kind": "dhcp",
            "ts": ts,
            "event": event,
            "ip": ip,
            "mac": mac.lower(),
            "host": host,
        }

    m = _AUTH_EVENT_RE.search(line)
    if m:
        iface, event, mac, status, reason = m.groups()
        return {
            "kind": "auth",
            "ts": ts,
            "event": event,
            "iface": iface,
            "mac": mac.lower(),
            "status": status.strip(),
            "reason": (reason or "").strip(),
        }
    return None


async def _read_new_syslog_lines(ssh, last_line: int) -> tuple[list[str], int]:
    count_result = await ssh.run("wc -l < /tmp/syslog.log")
    try:
        current_line = int((count_result.stdout or "0").strip() or "0")
    except ValueError:
        return [], last_line

    if current_line < last_line:
        last_line = 0  # log rotated/truncated
    if current_line == last_line:
        return [], current_line

    start = last_line + 1
    chunk = await ssh.run(f"sed -n '{start},{current_line}p' /tmp/syslog.log")
    lines = [ln for ln in (chunk.stdout or "").splitlines() if ln.strip()]
    return lines, current_line


def _diagnose_capture(state: dict) -> tuple[str, str]:
    if state.get("ack"):
        ip = state.get("ip", "?")
        return "ok", f"DHCPACK seen; lease established ({ip})."
    if state.get("nak"):
        return "fail", "DHCPNAK seen; lease negotiation failed."
    if state.get("discover") and not state.get("offer"):
        return "fail", "DISCOVER seen without OFFER."
    if state.get("offer") and not state.get("request"):
        return "fail", "OFFER seen but no REQUEST from client."
    if state.get("request") and not state.get("ack"):
        return "fail", "REQUEST seen without ACK."
    if state.get("assoc") and not (
        state.get("discover") or state.get("request") or state.get("ack")
    ):
        return "warn", "Wi-Fi association occurred but no DHCP traffic followed."
    if state.get("disassoc_reason"):
        return "warn", f"Disassociation observed: {state['disassoc_reason']}"
    return "warn", "No decisive failure signature captured."

_DHCP_RESERVATION_PROFILES: dict[str, dict[str, str]] = {
    "macpro_primary": {
        "label": "MacPro12Core primary",
        "mac": "74:1b:b2:f1:c4:31",
        "hostname": "MacPro12Core",
        "default_ip": "192.168.1.240",
        "triggered_by_reserve": "dhcp:reserve-macpro-primary",
        "triggered_by_unreserve": "dhcp:unreserve-macpro-primary",
    },
    "denon_second_port": {
        "label": "Denon second ethernet port",
        "mac": "00:05:cd:d4:a5:3c",
        "hostname": "Denon150",
        "default_ip": "192.168.1.241",
        "triggered_by_reserve": "dhcp:reserve-denon-second-port",
        "triggered_by_unreserve": "dhcp:unreserve-denon-second-port",
    },
    "macpro_lan2": {
        "label": "MacPro Ethernet 2",
        "mac": "00:3e:e1:c9:2c:0b",
        "hostname": "MacPro12Core-LAN2",
        "default_ip": "192.168.1.242",
        "triggered_by_reserve": "dhcp:reserve-macpro-lan2",
        "triggered_by_unreserve": "dhcp:unreserve-macpro-lan2",
    },
    "macpro_lan1": {
        "label": "MacPro Ethernet 1",
        "mac": "00:3e:e1:c9:2c:0c",
        "hostname": "MacPro12Core-LAN1",
        "default_ip": "192.168.1.243",
        "triggered_by_reserve": "dhcp:reserve-macpro-lan1",
        "triggered_by_unreserve": "dhcp:unreserve-macpro-lan1",
    },
}


def _render_dhcp_apply_result(result) -> None:
    target = result.reservation
    target_text = (
        f"{target.mac} -> {target.ip}"
        f"{f' ({target.hostname})' if target and target.hostname else ''}"
        if target
        else "(none)"
    )
    status = "[green]DRY-RUN[/green]" if result.dry_run else "[green]APPLIED[/green]"
    if not result.success:
        status = "[red]FAILED[/red]"

    console.print(f"Result: {status}  action={result.action}  target={target_text}")
    if result.message:
        msg_color = "green" if result.success else "red"
        console.print(f"[{msg_color}]{result.message}[/{msg_color}]")
    if result.changed:
        table = Table(title="DHCP NVRAM Changes", show_header=True)
        table.add_column("Key", style="bold cyan")
        table.add_column("Old")
        table.add_column("New")
        for key in ("dhcp_static_x", "dhcp_staticlist", "dhcp_hostnames"):
            table.add_row(key, result.old_values.get(key, ""), result.new_values.get(key, ""))
        console.print(table)
    else:
        console.print("[dim]No NVRAM changes required.[/dim]")


def _profile_field(profile_key: str, field: str) -> str:
    profile = _DHCP_RESERVATION_PROFILES.get(profile_key)
    if not profile:
        raise click.ClickException(f"Unknown DHCP reservation profile: {profile_key}")
    value = profile.get(field)
    if value is None:
        raise click.ClickException(f"Missing profile field: {profile_key}.{field}")
    return value


def _profile_target(
    profile_key: str,
    *,
    mac: str | None,
    hostname: str | None,
) -> tuple[str, str]:
    target_mac = _normalize_mac(mac or _profile_field(profile_key, "mac"))
    target_hostname = hostname or _profile_field(profile_key, "hostname")
    return target_mac, target_hostname


def _render_device_row(device) -> str:
    return (
        f"{device.mac} ip={device.ip or '-'} host={device.hostname or '-'} "
        f"conn={device.connection.value} online={device.is_online}"
    )


async def _collect_device_match_rows(
    target_mac: str,
    target_hostname: str | None,
) -> tuple[list[str], list[str]]:
    target_mac = target_mac.lower()
    hostname_lower = (target_hostname or "").strip().lower()

    async def _collect(backend):
        devices = await backend.get_connected_devices()
        exact_rows: list[str] = []
        related_rows: list[str] = []
        for device in devices:
            mac = (device.mac or "").lower()
            host = (device.hostname or "").lower()
            is_exact = mac == target_mac
            is_related = (
                device.connection.value == "wired"
                and (
                    (hostname_lower and hostname_lower in host)
                    or ("denon" in host)
                    or ("d m holdings" in host)
                )
            )
            if is_exact:
                exact_rows.append(_render_device_row(device))
            elif is_related:
                related_rows.append(_render_device_row(device))
        return exact_rows, related_rows

    return await _run_with_backend(_collect)


def _print_profile_device_match_summary(
    profile_label: str,
    target_mac: str,
    target_hostname: str | None,
) -> None:
    console.print(f"[bold]Device match check[/bold] ({profile_label})")
    try:
        exact_rows, related_rows = asyncio.run(
            _collect_device_match_rows(target_mac, target_hostname)
        )
    except Exception as exc:
        console.print(
            f"[yellow]Could not fetch live device match summary: {exc}[/yellow]"
        )
        return

    if exact_rows:
        console.print("[green]Exact MAC match:[/green]")
        for row in exact_rows:
            console.print(f"  - {row}")
    else:
        console.print("[yellow]No exact MAC match currently visible.[/yellow]")

    if related_rows:
        console.print("[dim]Related wired candidates:[/dim]")
        for row in related_rows:
            console.print(f"  - {row}")


def _run_profile_reservation(
    *,
    profile_key: str,
    ip: str,
    mac: str | None,
    hostname: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    from asusroutercontrol.dhcp_reservations import upsert_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac, target_hostname = _profile_target(
        profile_key,
        mac=mac,
        hostname=hostname,
    )
    profile_label = _profile_field(profile_key, "label")
    if not dry_run:
        _print_profile_device_match_summary(profile_label, target_mac, target_hostname)
        if not yes and not click.confirm(
            f"Apply {profile_label} reservation {target_mac} -> {ip}?"
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    cfg = load_config()

    async def _reserve():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            async with RouterSSH() as ssh:
                return await upsert_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    ip=ip,
                    hostname=target_hostname,
                    dry_run=dry_run,
                    triggered_by=_profile_field(profile_key, "triggered_by_reserve"),
                )
        finally:
            await store.close()

    result = asyncio.run(_reserve())
    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)


def _run_profile_unreserve(
    *,
    profile_key: str,
    mac: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    from asusroutercontrol.dhcp_reservations import remove_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac, target_hostname = _profile_target(
        profile_key,
        mac=mac,
        hostname=None,
    )
    profile_label = _profile_field(profile_key, "label")
    if not dry_run:
        _print_profile_device_match_summary(profile_label, target_mac, target_hostname)
        if not yes and not click.confirm(
            f"Remove {profile_label} reservation for {target_mac}?"
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    cfg = load_config()

    async def _remove():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            async with RouterSSH() as ssh:
                return await remove_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    dry_run=dry_run,
                    triggered_by=_profile_field(profile_key, "triggered_by_unreserve"),
                )
        finally:
            await store.close()

    result = asyncio.run(_remove())
    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)

@click.group()
@click.version_option(package_name="asusroutercontrol")
def cli():
    """ASUSRouterControl — manage your ASUS router."""


# Register extracted command groups
cli.add_command(dhcp_group, name="dhcp")
cli.add_command(optimize_group, name="optimize")


# =============================================================================
# LEGACY: The dhcp and optimize groups below are now in cli/dhcp.py and
# cli/optimize.py. Keeping this code temporarily for reference during the
# refactoring transition. These definitions are SHADOWED by the add_command
# calls above and will NOT be used.
# =============================================================================

# @cli.group("dhcp")  # MOVED to cli/dhcp.py
# def dhcp_group():
#     """Manage DHCP reservations."""


@dhcp_group.command("show")
def dhcp_show():
    """Show parsed DHCP reservations and raw NVRAM keys."""
    from asusroutercontrol.dhcp_reservations import get_reservations, read_dhcp_nvram
    from asusroutercontrol.ssh import RouterSSH

    async def _show():
        async with RouterSSH() as ssh:
            values = await read_dhcp_nvram(ssh)
            reservations = await get_reservations(ssh)

        console.print(
            f"dhcp_static_x={values.get('dhcp_static_x', '')}  "
            f"reservations={len(reservations)}"
        )
        if not reservations:
            console.print("[dim]No DHCP reservations parsed.[/dim]")
            return

        table = Table(title="DHCP Reservations")
        table.add_column("MAC", style="bold")
        table.add_column("IP")
        table.add_column("Hostname")
        for row in sorted(reservations, key=lambda r: (r.ip, r.mac)):
            table.add_row(row.mac, row.ip, row.hostname or "-")
        console.print(table)

    asyncio.run(_show())


@dhcp_group.command("health")
def dhcp_health():
    """Assert required MAC→IP reservation mappings in one check."""
    from asusroutercontrol.dhcp_reservations import get_reservations
    from asusroutercontrol.ssh import RouterSSH

    required_profiles = (
        "macpro_primary",
        "denon_second_port",
        "macpro_lan2",
        "macpro_lan1",
    )

    async def _health():
        async with RouterSSH() as ssh:
            rows = await get_reservations(ssh)
        by_mac = {row.mac.lower(): row for row in rows}

        table = Table(title="DHCP Reservation Health")
        table.add_column("Profile", style="bold")
        table.add_column("MAC")
        table.add_column("Expected IP")
        table.add_column("Actual IP")
        table.add_column("Status")

        failures: list[str] = []
        for profile_key in required_profiles:
            label = _profile_field(profile_key, "label")
            mac = _profile_field(profile_key, "mac").lower()
            expected_ip = _profile_field(profile_key, "default_ip")
            actual = by_mac.get(mac)
            actual_ip = actual.ip if actual else "-"
            ok = actual is not None and actual_ip == expected_ip
            status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
            table.add_row(label, mac, expected_ip, actual_ip, status)
            if not ok:
                failures.append(f"{label} expected {expected_ip}, got {actual_ip}")

        console.print(table)
        if failures:
            raise click.ClickException(
                "Reservation health check failed: " + "; ".join(failures)
            )
        console.print("[green]All required DHCP reservation mappings are healthy.[/green]")

    asyncio.run(_health())


@dhcp_group.command("set")
@click.option("--mac", required=True, help="Target client MAC.")
@click.option("--ip", required=True, help="Reserved IPv4 address.")
@click.option("--hostname", default=None, help="Optional hostname mapping.")
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview by default. Use --apply to write changes.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt for --apply.")
def dhcp_set(mac: str, ip: str, hostname: str | None, dry_run: bool, yes: bool):
    """Create or update a DHCP reservation."""
    from asusroutercontrol.dhcp_reservations import upsert_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac = _normalize_mac(mac)
    if not dry_run and not yes:
        if not click.confirm(f"Apply DHCP reservation {target_mac} -> {ip}?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    cfg = load_config()

    async def _set():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            async with RouterSSH() as ssh:
                return await upsert_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    ip=ip,
                    hostname=hostname,
                    dry_run=dry_run,
                    triggered_by="dhcp:set",
                )
        finally:
            await store.close()

    result = asyncio.run(_set())
    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)


@dhcp_group.command("remove")
@click.option("--mac", required=True, help="Target client MAC.")
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview by default. Use --apply to write changes.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt for --apply.")
def dhcp_remove(mac: str, dry_run: bool, yes: bool):
    """Remove a DHCP reservation by MAC."""
    from asusroutercontrol.dhcp_reservations import remove_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac = _normalize_mac(mac)
    if not dry_run and not yes:
        if not click.confirm(f"Remove DHCP reservation for {target_mac}?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    cfg = load_config()

    async def _remove():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            async with RouterSSH() as ssh:
                return await remove_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    dry_run=dry_run,
                    triggered_by="dhcp:remove",
                )
        finally:
            await store.close()

    result = asyncio.run(_remove())
    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)


@dhcp_group.command("reserve-macpro")
@click.option(
    "--ip",
    default=_DHCP_RESERVATION_PROFILES["macpro_primary"]["default_ip"],
    show_default=True,
    help="Reserved IP for MacPro12Core.",
)
@click.option(
    "--mac",
    default=_DHCP_RESERVATION_PROFILES["macpro_primary"]["mac"],
    show_default=True,
    help="MAC override for Mac Pro profile.",
)
@click.option(
    "--hostname",
    default=_DHCP_RESERVATION_PROFILES["macpro_primary"]["hostname"],
    show_default=True,
    help="Hostname override for Mac Pro profile.",
)
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview by default. Use --apply to write changes.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt for --apply.")
def dhcp_reserve_macpro(
    ip: str,
    mac: str,
    hostname: str,
    dry_run: bool,
    yes: bool,
):
    """Set DHCP reservation for MacPro12Core."""
    _run_profile_reservation(
        profile_key="macpro_primary",
        ip=ip,
        mac=mac,
        hostname=hostname,
        dry_run=dry_run,
        yes=yes,
    )


@dhcp_group.command("reserve-denon-second-port")
@click.option(
    "--ip",
    default=_DHCP_RESERVATION_PROFILES["denon_second_port"]["default_ip"],
    show_default=True,
    help="Reserved IP for Denon second ethernet endpoint.",
)
@click.option(
    "--mac",
    default=_DHCP_RESERVATION_PROFILES["denon_second_port"]["mac"],
    show_default=True,
    help="MAC override for Denon second-port profile.",
)
@click.option(
    "--hostname",
    default=_DHCP_RESERVATION_PROFILES["denon_second_port"]["hostname"],
    show_default=True,
    help="Hostname override for Denon second-port profile.",
)
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview by default. Use --apply to write changes.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt for --apply.")
def dhcp_reserve_denon_second_port(
    ip: str,
    mac: str,
    hostname: str,
    dry_run: bool,
    yes: bool,
):
    """Optionally reserve DHCP IP for second ethernet port (Denon150)."""
    _run_profile_reservation(
        profile_key="denon_second_port",
        ip=ip,
        mac=mac,
        hostname=hostname,
        dry_run=dry_run,
        yes=yes,
    )


@dhcp_group.command("unreserve-denon-second-port")
@click.option(
    "--mac",
    default=_DHCP_RESERVATION_PROFILES["denon_second_port"]["mac"],
    show_default=True,
    help="MAC override for Denon second-port profile.",
)
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview by default. Use --apply to write changes.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt for --apply.")
def dhcp_unreserve_denon_second_port(
    mac: str,
    dry_run: bool,
    yes: bool,
):
    """Optionally remove DHCP reservation for second ethernet port."""
    _run_profile_unreserve(
        profile_key="denon_second_port",
        mac=mac,
        dry_run=dry_run,
        yes=yes,
    )


@cli.group()
def incident():
    """Incident triage and recovery workflows."""

def _collect_incident_snapshot():
    from asusroutercontrol.incident import (
        attach_router_observations,
        collect_local_snapshot,
        collect_router_observations,
    )
    from asusroutercontrol.ssh import RouterSSH

    snapshot = collect_local_snapshot()

    async def _router():
        async def _collect(backend):
            async with RouterSSH() as ssh:
                return await collect_router_observations(backend, ssh)

        return await _run_with_backend(_collect)

    try:
        static_enabled, static_count, router_paths = asyncio.run(_router())
        attach_router_observations(
            snapshot,
            static_enabled=static_enabled,
            static_count=static_count,
            router_paths=router_paths,
        )
    except Exception as exc:
        snapshot.ui_conflicts.append(f"router observation failed: {exc}")
    return snapshot


def _render_incident_snapshot(snapshot, classification) -> None:
    from asusroutercontrol.incident import PATHS

    table = Table(title="Incident Snapshot")
    table.add_column("Path", style="bold")
    table.add_column("Service")
    table.add_column("Device")
    table.add_column("Enabled")
    table.add_column("IP")
    table.add_column("Link")
    table.add_column("Gateway Probe")
    table.add_column("Health")

    for path_key in sorted(PATHS, key=lambda key: PATHS[key].priority):
        state = snapshot.paths[path_key]
        if state.service_enabled is True:
            enabled = "yes"
        elif state.service_enabled is False:
            enabled = "no"
        else:
            enabled = "?"
        source_probe = (
            "ok"
            if state.source_ping_ok is True
            else "fail"
            if state.source_ping_ok is False
            else "-"
        )
        health = "[green]healthy[/green]" if state.healthy else "[red]degraded[/red]"
        link = "active" if state.status_active else "down"
        table.add_row(
            PATHS[path_key].label,
            state.service,
            state.device,
            enabled,
            state.ip or "-",
            link,
            source_probe,
            health,
        )
    console.print(table)

    route_summary = (
        f"default route: {snapshot.default_interface or '-'} via {snapshot.default_gateway or '-'}"
    )
    console.print(route_summary)
    if snapshot.router_dhcp_static_enabled is not None:
        console.print(
            "router dhcp_static_x="
            f"{1 if snapshot.router_dhcp_static_enabled else 0} "
            f"(reservations={snapshot.router_static_reservation_count})"
        )

    if snapshot.router_paths:
        rt = Table(title="Router View (Tracked MACs)")
        rt.add_column("Path", style="bold")
        rt.add_column("MAC")
        rt.add_column("Router IP")
        rt.add_column("Connection")
        rt.add_column("Online")
        for path_key in sorted(PATHS, key=lambda key: PATHS[key].priority):
            row = snapshot.router_paths.get(path_key)
            if not row:
                continue
            rt.add_row(
                PATHS[path_key].label,
                row.mac,
                row.ip or "-",
                row.connection,
                "yes" if row.online else "no",
            )
        console.print(rt)

    status_color = {
        "healthy": "green",
        "reporting-only": "yellow",
        "path-isolated": "yellow",
        "global-outage": "red",
    }.get(classification.category, "white")
    console.print(
        f"classification: [{status_color}]{classification.category}[/{status_color}]"
    )
    for reason in classification.reasons:
        console.print(f"  - {reason}")


@incident.command("snapshot")
@click.option("--json", "as_json", is_flag=True, help="Output JSON snapshot/classification.")
def incident_snapshot(as_json: bool):
    """Capture local+router connectivity baseline and classify incident state."""
    import json as json_mod

    from asusroutercontrol.incident import classify_snapshot

    snapshot = _collect_incident_snapshot()
    classification = classify_snapshot(snapshot)

    if as_json:
        console.print(
            json_mod.dumps(
                {
                    "snapshot": snapshot.to_dict(),
                    "classification": classification.to_dict(),
                },
                indent=2,
                default=str,
            )
        )
        return

    _render_incident_snapshot(snapshot, classification)


@incident.command("classify")
@click.option("--json", "as_json", is_flag=True, help="Output JSON classification.")
def incident_classify(as_json: bool):
    """Classify current incident state: healthy/reporting-only/path-isolated/global-outage."""
    import json as json_mod

    from asusroutercontrol.incident import classify_snapshot

    snapshot = _collect_incident_snapshot()
    classification = classify_snapshot(snapshot)
    if as_json:
        console.print(json_mod.dumps(classification.to_dict(), indent=2, default=str))
        return

    status_color = {
        "healthy": "green",
        "reporting-only": "yellow",
        "path-isolated": "yellow",
        "global-outage": "red",
    }.get(classification.category, "white")
    console.print(
        f"incident classification: [{status_color}]{classification.category}[/{status_color}]"
    )
    for reason in classification.reasons:
        console.print(f"  - {reason}")

@incident.command("repair-macos")
@click.option(
    "--stage",
    required=True,
    type=click.Choice(["A", "B", "C"], case_sensitive=False),
    help="Repair stage to execute.",
)
@click.option(
    "--path",
    "path_key",
    default="ethernet-primary",
    show_default=True,
    type=click.Choice(["ethernet-primary", "wifi-secondary", "ethernet-secondary"]),
    help="Operate on one path only (Ethernet primary first, Wi-Fi second).",
)
@click.option(
    "--allow-global-reset",
    is_flag=True,
    help="Stage C only: allow global configd restart (disruptive).",
)
@click.option("--dry-run", is_flag=True, help="Print commands without executing.")
def incident_repair_macos(
    stage: str,
    path_key: str,
    allow_global_reset: bool,
    dry_run: bool,
):
    """Run one macOS repair stage on a single connectivity path."""
    from asusroutercontrol.incident import (
        PATHS,
        build_repair_stage_commands,
        classify_snapshot,
        collect_local_snapshot,
        execute_repair_commands,
        is_sudo_auth_failure,
    )

    stage_key = stage.upper()
    commands = build_repair_stage_commands(
        stage=stage_key,
        path_key=path_key,
        allow_global_reset=allow_global_reset,
    )

    if dry_run:
        console.print(
            f"[bold]Dry run[/bold] stage={stage_key} path={PATHS[path_key].label}"
        )
        for step in commands:
            prefix = "sudo -n " if step.sudo else ""
            console.print(f"  - {prefix}{' '.join(step.argv)}")
        return

    before = collect_local_snapshot()
    before_class = classify_snapshot(before)
    console.print(
        f"before: {before_class.category}  healthy={before_class.healthy_paths} "
        f"degraded={before_class.degraded_paths}"
    )

    results = execute_repair_commands(commands, stop_on_error=True)
    table = Table(title=f"Repair Stage {stage_key} — {PATHS[path_key].label}")
    table.add_column("Command", style="bold")
    table.add_column("Exit", justify="right")
    table.add_column("Status")
    for result in results:
        status = "[green]ok[/green]" if result.ok else "[red]fail[/red]"
        table.add_row(result.command, str(result.exit_code), status)
    console.print(table)

    first_failure = next((result for result in results if not result.ok), None)
    if first_failure:
        if is_sudo_auth_failure(first_failure):
            raise click.ClickException(
                "Local sudo authentication failed. Router keychain credentials do not control "
                "local sudo; authenticate with local macOS admin sudo first."
            )
        details = first_failure.stderr.strip() or first_failure.stdout.strip() or "unknown"
        raise click.ClickException(
            f"Repair command failed (exit {first_failure.exit_code}): "
            f"{first_failure.command} :: {details}"
        )

    after = collect_local_snapshot()
    after_class = classify_snapshot(after)
    console.print(
        f"after: {after_class.category}  healthy={after_class.healthy_paths} "
        f"degraded={after_class.degraded_paths}"
    )
    if path_key not in after_class.healthy_paths:
        raise click.ClickException(
            f"Path {PATHS[path_key].label} remains degraded after stage {stage_key}."
        )
    console.print("[green]Stage completed and target path is healthy.[/green]")


@incident.command("rollback")
@click.option("--profile", default="last-rollback", show_default=True)
@click.option("--watch-mac", default=None, help="Optional target client MAC.")
@click.option(
    "--max-loss",
    default=5.0,
    show_default=True,
    type=click.FloatRange(0.0, 100.0),
)
@click.option(
    "--hold-seconds",
    default=90,
    show_default=True,
    type=click.IntRange(10, 1800),
)
@click.option(
    "--poll-seconds",
    default=3.0,
    show_default=True,
    type=click.FloatRange(0.5, 30.0),
)
@click.option("--force", is_flag=True, help="Bypass rollback guardrails.")
@click.option("--dry-run", is_flag=True, help="Evaluate rollback flow without writes.")
def incident_rollback(
    profile: str,
    watch_mac: str | None,
    max_loss: float,
    hold_seconds: int,
    poll_seconds: float,
    force: bool,
    dry_run: bool,
):
    """Run guarded rollback profile after incident classification checks."""
    from asusroutercontrol.incident import classify_snapshot
    from asusroutercontrol.rollout import run_rollout_profile

    snapshot = _collect_incident_snapshot()
    classification = classify_snapshot(snapshot)

    if classification.has_healthy_wired and classification.has_healthy_wifi and not force:
        raise click.ClickException(
            "Rollback blocked: wired and Wi-Fi paths are currently healthy. "
            "Use --force to override."
        )
    if classification.category == "path-isolated" and not force:
        raise click.ClickException(
            "Rollback blocked: fault classified as path-isolated. "
            "Fix the isolated path first or pass --force."
        )

    cfg = load_config()

    async def _run():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            return await run_rollout_profile(
                store,
                profile=profile,
                max_loss_pct=max_loss,
                hold_seconds=hold_seconds,
                poll_seconds=poll_seconds,
                watch_mac=watch_mac,
                no_disconnect=True,
                allow_disruptive=False,
                dry_run=dry_run,
            )
        finally:
            await store.close()

    result = asyncio.run(_run())
    table = Table(title=f"Incident Rollback: {profile}")
    table.add_column("Key", style="bold cyan")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Reason")
    for step in result.step_results:
        color = {
            "pass": "green",
            "skipped": "yellow",
            "rolled_back": "yellow",
            "fail": "red",
            "dry_run_pass": "green",
        }.get(step.status, "white")
        table.add_row(
            step.key,
            step.target,
            f"[{color}]{step.status}[/{color}]",
            step.reason[:140],
        )
    console.print(table)
    if result.completed:
        console.print("[green]Incident rollback profile completed.[/green]")
        return
    raise click.ClickException(f"Incident rollback stopped: {result.aborted_reason or 'unknown'}")


async def _run_with_backend(coro_factory):
    """Connect, run coroutine, disconnect."""
    from asusroutercontrol.backends.base import BackendOperationUnsupported
    backend = _get_backend()
    try:
        await backend.connect()
        try:
            return await coro_factory(backend)
        except BackendOperationUnsupported as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await backend.disconnect()


@cli.command()
def setup():
    """Store router credentials in macOS Keychain (universal-keychain format)."""
    console.print("[bold]ASUSRouterControl Setup[/bold]\n")

    username = click.prompt("Router username", default="admin")
    password = click.prompt("Router password", hide_input=True)

    ok_user = store_credential("router_username", username)
    ok_pass = store_credential("router_password", password)

    if ok_user and ok_pass:
        console.print("\n[green]Credentials stored in macOS Keychain (universal-keychain).[/green]")
        console.print("Config file: copy .env.example to .env and adjust ROUTER_HOST if needed.")
    else:
        console.print("\n[red]Failed to store credentials.[/red]")


@cli.group()
def credentials():
    """Manage router credentials."""


@credentials.command("migrate")
@click.option("--dry-run", is_flag=True, help="Show what would be migrated without writing.")
def credentials_migrate(dry_run: bool):
    """Migrate legacy com.asusroutercontrol.* entries to universal-keychain format."""
    import logging
    logging.basicConfig(level=logging.INFO)

    migrated = migrate_legacy_credentials(dry_run=dry_run)
    if not migrated:
        console.print(
            "[dim]Nothing to migrate — all entries already in universal-keychain format.[/dim]"
        )
        return
    verb = "Would migrate" if dry_run else "Migrated"
    for key in migrated:
        console.print(f"  [green]{verb}[/green] {key}")
    if not dry_run:
        console.print(
            "\n[bold]Run [cyan]asusrouter credentials cleanup[/cyan]"
            " to remove legacy entries.[/bold]"
        )


@credentials.command("cleanup")
def credentials_cleanup():
    """Remove deprecated legacy keychain entries after migration."""
    removed = delete_legacy_credentials()
    if not removed:
        console.print("[dim]No legacy entries to remove.[/dim]")
        return
    for key in removed:
        console.print(f"  [yellow]Removed[/yellow] com.asusroutercontrol.{key}")
    console.print("[green]Legacy entries cleaned up.[/green]")


@cli.command()
def status():
    """Show router system info and WAN status."""

    async def _status(backend):
        with console.status("Fetching router status..."):
            sys_info = await backend.get_system_info()
            wan = await backend.get_wan_status()

        table = Table(title="Router Status", show_header=False, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")

        if sys_info.model:
            table.add_row("Model", sys_info.model)
        if sys_info.firmware_version:
            table.add_row("Firmware", sys_info.firmware_version)
        if sys_info.uptime_seconds is not None:
            days = sys_info.uptime_seconds // 86400
            hours = (sys_info.uptime_seconds % 86400) // 3600
            mins = (sys_info.uptime_seconds % 3600) // 60
            parts = []
            if days:
                parts.append(f"{days}d")
            parts.append(f"{hours}h {mins}m")
            table.add_row("Uptime", " ".join(parts))
        if sys_info.cpu_usage_percent is not None:
            cpu_str = f"{sys_info.cpu_usage_percent:.1f}%"
            if sys_info.temperature_c is not None:
                cpu_str += f" ({sys_info.temperature_c:.0f}°C)"
            table.add_row("CPU", cpu_str)
        if sys_info.ram_usage_percent is not None:
            table.add_row(
                "RAM",
                f"{sys_info.ram_usage_percent:.1f}% "
                f"({sys_info.ram_used_mb:.0f}/{sys_info.ram_total_mb:.0f} MB)",
            )

        table.add_row("WAN", wan.status)
        if wan.ip_address:
            table.add_row("WAN IP", wan.ip_address)
        if wan.gateway:
            table.add_row("Gateway", wan.gateway)
        if wan.dns:
            table.add_row("DNS", ", ".join(wan.dns))

        console.print(table)

    asyncio.run(_run_with_backend(_status))


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def devices(as_json: bool):
    """List connected devices."""

    async def _devices(backend):
        with console.status("Fetching connected devices..."):
            devs = await backend.get_connected_devices()

        if as_json:
            import json

            console.print(json.dumps([d.model_dump(mode="json") for d in devs], indent=2))
            return

        table = Table(title=f"Connected Devices ({len(devs)})")
        table.add_column("Hostname", style="bold")
        table.add_column("IP")
        table.add_column("MAC", style="dim")
        table.add_column("Connection")
        table.add_column("Signal", justify="right")

        for d in sorted(devs, key=lambda x: x.hostname or x.mac):
            signal = f"{d.rssi} dBm" if d.rssi is not None else "-"
            table.add_row(
                d.hostname or "(unknown)",
                d.ip or "-",
                d.mac,
                d.connection.value,
                signal,
            )

        console.print(table)

    asyncio.run(_run_with_backend(_devices))


@cli.command()
@click.option("--interval", "-i", default=60, help="Poll interval in seconds")
def monitor(interval: int):
    """Continuously monitor router (Ctrl+C to stop)."""
    from asusroutercontrol.service import RouterControlService

    cfg = load_config()
    cfg.ensure_dirs()

    async def _monitor():
        backend = _get_backend()
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        await backend.connect()
        svc = RouterControlService(
            backend, store,
            interval=interval,
            soundshield_path=cfg.soundshield_export_path,
            on_new_device=lambda d: console.print(
                f"[yellow]New device:[/yellow] {d.hostname or d.mac} ({d.ip})"
            ),
        )
        try:
            console.print(
                f"[bold]Monitoring router[/bold] (interval={interval}s, Ctrl+C to stop)"
            )
            await svc.run()
        except KeyboardInterrupt:
            svc.stop()
        finally:
            await backend.disconnect()
            await store.close()

    asyncio.run(_monitor())


@cli.command()
@click.option("--hours", "-h", default=24, help="Window in hours")
def traffic(hours: int):
    """Show traffic summary and anomalies."""
    from asusroutercontrol.analysis.traffic import detect_anomalies, get_traffic_summary

    cfg = load_config()

    async def _traffic():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            summary = await get_traffic_summary(store, hours=hours)
            if not summary.get("samples"):
                console.print("[dim]No traffic data yet. Run 'asusrouter monitor' first.[/dim]")
                return

            table = Table(title=f"Traffic Summary ({hours}h)", show_header=False)
            table.add_column("Key", style="bold cyan")
            table.add_column("Value")
            table.add_row("Samples", str(summary["samples"]))
            table.add_row("Total Download", summary["total_download"])
            table.add_row("Total Upload", summary["total_upload"])
            table.add_row("Avg Download", summary["avg_download_rate"])
            table.add_row("Avg Upload", summary["avg_upload_rate"])
            table.add_row("Peak Download", summary["peak_download_rate"])
            table.add_row("Peak Upload", summary["peak_upload_rate"])
            console.print(table)

            anomalies = await detect_anomalies(store, window_hours=hours)
            if anomalies:
                console.print(f"\n[yellow]Anomalies detected: {len(anomalies)}[/yellow]")
                for a in anomalies[:10]:
                    console.print(
                        f"  {a['timestamp']}  RX={a['rx_rate']} ({a['rx_factor']}x)  "
                        f"TX={a['tx_rate']} ({a['tx_factor']}x)"
                    )
        finally:
            await store.close()

    asyncio.run(_traffic())


@cli.command()
@click.option("--hours", default=1, show_default=True, type=click.IntRange(1, 168))
def client_load_diagnostics(hours: int):
    """Show client load data-quality diagnostics for a recent window."""
    cfg = load_config()

    async def _diag():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            stats = await store.get_client_load_window_stats(hours=hours)
        finally:
            await store.close()

        samples = int(stats.get("samples") or 0)
        if samples == 0:
            console.print("[dim]No client load rows in the selected window.[/dim]")
            return

        signal_rows = int(stats.get("signal_rows") or 0)
        placeholder_rows = int(stats.get("placeholder_rows") or 0)
        max_load = stats.get("max_load_pct")
        avg_load = stats.get("avg_load_pct")

        table = Table(title=f"Client Load Diagnostics ({hours}h)", show_header=False)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        table.add_row("Samples", str(samples))
        table.add_row("Signal rows", str(signal_rows))
        table.add_row("Placeholder rows", str(placeholder_rows))
        table.add_row(
            "Signal ratio",
            f"{(signal_rows / samples) * 100:.1f}%",
        )
        table.add_row(
            "Max load",
            f"{float(max_load):.1f}%" if max_load is not None else "—",
        )
        table.add_row(
            "Avg load",
            f"{float(avg_load):.2f}%" if avg_load is not None else "—",
        )
        console.print(table)

    asyncio.run(_diag())


@cli.command()
@click.argument("mac", required=False)
def history(mac: str | None):
    """Show device history. Provide MAC for specific device, or omit for all."""
    cfg = load_config()

    async def _history():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            if mac:
                sessions = await store.get_device_sessions(mac, limit=20)
                if not sessions:
                    console.print(f"[dim]No history for {mac}[/dim]")
                    return
                table = Table(title=f"History: {mac}")
                table.add_column("Seen At")
                table.add_column("IP")
                table.add_column("Connection")
                table.add_column("RSSI", justify="right")
                for s in sessions:
                    table.add_row(
                        s["seen_at"], s["ip"] or "-",
                        s["connection"], str(s.get("rssi") or "-"),
                    )
                console.print(table)
            else:
                all_devs = await store.get_all_devices()
                if not all_devs:
                    console.print("[dim]No devices recorded yet.[/dim]")
                    return
                table = Table(title=f"All Known Devices ({len(all_devs)})")
                table.add_column("Hostname", style="bold")
                table.add_column("MAC", style="dim")
                table.add_column("First Seen")
                table.add_column("Last Seen")
                table.add_column("Known", justify="center")
                for d in all_devs:
                    table.add_row(
                        d["hostname"] or "(unknown)", d["mac"],
                        d["first_seen"][:16], d["last_seen"][:16],
                        "Y" if d["is_known"] else "N",
                    )
                console.print(table)
        finally:
            await store.close()

    asyncio.run(_history())


# --- Phase 3: Control commands ---


@cli.command()
@click.confirmation_option(prompt="Reboot the router?")
def reboot():
    """Reboot the router."""

    async def _reboot(backend):
        ok = await backend.set_state("reboot")
        if ok:
            console.print("[green]Reboot command sent.[/green]")
        else:
            console.print("[red]Reboot failed.[/red]")

    asyncio.run(_run_with_backend(_reboot))


@cli.command()
@click.argument("action", type=click.Choice(["on", "off"]))
@click.argument("band", type=click.Choice(["2.4", "5", "all"]), default="all")
def wifi(action: str, band: str):
    """Toggle WiFi radios. Usage: asusrouter wifi on|off [2.4|5|all]"""

    async def _wifi(backend):
        # Map to set_state actions
        targets = []
        if band in ("2.4", "all"):
            targets.append(f"wlan_2g_{action}")
        if band in ("5", "all"):
            targets.append(f"wlan_5g_{action}")

        for t in targets:
            ok = await backend.set_state(t)
            status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
            console.print(f"  {t}: {status}")

    asyncio.run(_run_with_backend(_wifi))


@cli.command()
def ports():
    """Show port forwarding rules."""

    async def _ports(backend):
        with console.status("Fetching port forwarding rules..."):
            rules = await backend.get_port_forwarding()

        if not rules:
            console.print("[dim]No port forwarding rules configured.[/dim]")
            return

        table = Table(title=f"Port Forwarding ({len(rules)} rules)")
        table.add_column("Name")
        table.add_column("Proto")
        table.add_column("Src Port")
        table.add_column("Dst IP")
        table.add_column("Dst Port")
        table.add_column("Enabled", justify="center")

        for r in rules:
            table.add_row(
                r.name or "-", r.protocol, r.src_port,
                r.dst_ip, r.dst_port,
                "Y" if r.enabled else "N",
            )
        console.print(table)

    asyncio.run(_run_with_backend(_ports))


@cli.command()
def security():
    """Run security posture check."""
    from asusroutercontrol.analysis.security import get_security_report

    async def _security(backend):
        with console.status("Running security check..."):
            report = await get_security_report(backend)

        fw = report["firmware"]
        style = {
            "current": "green", "outdated": "yellow",
            "vulnerable": "red", "unknown": "dim",
        }.get(fw["status"], "white")
        console.print(f"Firmware: [{style}]{fw['message']}[/{style}]")

        pf = report["port_forwarding"]
        console.print(f"Port forwarding: {pf['active_rules']}/{pf['total_rules']} active")

        if report["recommendations"]:
            console.print("\n[bold]Recommendations:[/bold]")
            for rec in report["recommendations"]:
                console.print(f"  - {rec}")
        else:
            console.print("[green]No issues found.[/green]")

    asyncio.run(_run_with_backend(_security))


@cli.command("live-dhcp-auth")
@click.option("--mac", default=None, help="Target phone MAC (recommended).")
@click.option("--seconds", "-s", default=120, show_default=True, type=click.IntRange(10, 1800))
@click.option(
    "--poll",
    "poll_seconds",
    default=1.5,
    show_default=True,
    type=click.FloatRange(0.5, 10.0),
    help="Polling interval in seconds.",
)
def live_dhcp_auth(mac: str | None, seconds: int, poll_seconds: float):
    """Tail DHCP/Wi-Fi auth logs live and diagnose phone reconnect failures."""
    from asusroutercontrol.ssh import RouterSSH

    target_mac = _normalize_mac(mac)

    async def _watch():
        async with RouterSSH() as ssh:
            initial_count = await ssh.run("wc -l < /tmp/syslog.log")
            try:
                last_line = int((initial_count.stdout or "0").strip() or "0")
            except ValueError:
                last_line = 0

            console.print(
                "[bold]Watching live DHCP/auth logs[/bold] "
                f"for {seconds}s. Reconnect one phone now."
            )
            if target_mac:
                console.print(f"[dim]Filter MAC:[/dim] {target_mac}")
            else:
                console.print(
                    "[dim]Tip:[/dim] pass --mac AA:BB:CC:DD:EE:FF for a cleaner trace."
                )

            start = time.monotonic()
            state_by_mac: dict[str, dict] = {}
            while time.monotonic() - start < seconds:
                lines, last_line = await _read_new_syslog_lines(ssh, last_line)
                for line in lines:
                    ev = _parse_live_event(line)
                    if not ev:
                        continue
                    ev_mac = ev["mac"]
                    if target_mac and ev_mac != target_mac:
                        continue

                    if not target_mac:
                        low = line.lower()
                        is_mobile_hint = any(
                            hint in low for hint in ("iphone", "ipad", "watch", "android")
                        )
                        if not is_mobile_hint and ev_mac not in state_by_mac:
                            continue

                    st = state_by_mac.setdefault(
                        ev_mac,
                        {
                            "discover": False,
                            "offer": False,
                            "request": False,
                            "ack": False,
                            "nak": False,
                            "assoc": False,
                            "disassoc_reason": "",
                            "ip": "",
                        },
                    )

                    if ev["kind"] == "dhcp":
                        event = ev["event"]
                        if event == "DHCPDISCOVER":
                            st["discover"] = True
                        elif event == "DHCPOFFER":
                            st["offer"] = True
                        elif event == "DHCPREQUEST":
                            st["request"] = True
                        elif event == "DHCPACK":
                            st["ack"] = True
                            st["ip"] = ev.get("ip") or ""
                        elif event == "DHCPNAK":
                            st["nak"] = True
                        host = f" host={ev['host']}" if ev.get("host") else ""
                        ip = ev.get("ip") or "-"
                        console.print(
                            f"[green]{ev['ts']}[/green] {ev_mac} {event} ip={ip}{host}"
                        )
                    else:
                        event = ev["event"]
                        if event == "Assoc" and "Successful" in ev.get("status", ""):
                            st["assoc"] = True
                        if event in ("Disassoc", "Deauth"):
                            reason = ev.get("reason") or ev.get("status") or "unknown"
                            st["disassoc_reason"] = reason
                        reason_txt = (
                            f", reason={ev['reason']}"
                            if ev.get("reason")
                            else f", status={ev.get('status', '-')}"
                        )
                        console.print(
                            f"[cyan]{ev['ts']}[/cyan] {ev_mac} "
                            f"{event} on {ev['iface']}{reason_txt}"
                        )

                await asyncio.sleep(poll_seconds)

            if not state_by_mac:
                console.print(
                    "[yellow]No matching DHCP/auth events captured. "
                    "Retry with --mac and a longer --seconds window.[/yellow]"
                )
                return

            console.print("\n[bold]Diagnosis Summary[/bold]")
            for ev_mac, state in sorted(state_by_mac.items()):
                level, msg = _diagnose_capture(state)
                color = {"ok": "green", "warn": "yellow", "fail": "red"}.get(level, "white")
                console.print(f"[{color}]{ev_mac}[/{color}] {msg}")

    asyncio.run(_watch())


# --- Merlin: JFFS Scripts ---


@cli.group()
def scripts():
    """Manage JFFS custom scripts (Merlin)."""


@scripts.command("list")
def scripts_list():
    """List scripts in /jffs/scripts/."""
    from asusroutercontrol.merlin.jffs import MERLIN_HOOKS, is_jffs_enabled, list_scripts
    from asusroutercontrol.ssh import RouterSSH

    async def _list():
        async with RouterSSH() as ssh:
            jffs_on = await is_jffs_enabled(ssh)
            if not jffs_on:
                console.print("[red]JFFS is not enabled.[/red] Enable in Administration > System.")
                return

            items = await list_scripts(ssh)
            if not items:
                console.print("[dim]No scripts in /jffs/scripts/[/dim]")
                console.print("\n[bold]Available hooks:[/bold]")
                for hook, desc in list(MERLIN_HOOKS.items())[:8]:
                    console.print(f"  [cyan]{hook}[/cyan] — {desc}")
                return

            table = Table(title=f"JFFS Scripts ({len(items)})")
            table.add_column("Name", style="bold")
            table.add_column("Exec", justify="center")
            table.add_column("Size", justify="right")
            table.add_column("Hook")
            for s in items:
                table.add_row(
                    s.name,
                    "[green]✓[/green]" if s.executable else "[red]✗[/red]",
                    f"{s.size}B",
                    s.hook_description or "-",
                )
            console.print(table)

    asyncio.run(_list())


@scripts.command("show")
@click.argument("name")
def scripts_show(name: str):
    """Show contents of a script."""
    from asusroutercontrol.merlin.jffs import read_script
    from asusroutercontrol.ssh import RouterSSH

    async def _show():
        async with RouterSSH() as ssh:
            info = await read_script(ssh, name)
            if not info:
                console.print(f"[red]Script not found: {name}[/red]")
                return
            status = "[green]executable[/green]" if info.executable else "[red]disabled[/red]"
            console.print(f"[bold]{info.path}[/bold] ({info.size}B, {status})")
            if info.hook_description:
                console.print(f"Hook: {info.hook_description}")
            console.print("")
            from rich.syntax import Syntax
            console.print(Syntax(info.content or "", "bash", theme="monokai"))

    asyncio.run(_show())


@scripts.command("enable")
@click.argument("name")
def scripts_enable(name: str):
    """Enable a script (chmod +x)."""
    from asusroutercontrol.merlin.jffs import enable_script
    from asusroutercontrol.ssh import RouterSSH

    async def _enable():
        async with RouterSSH() as ssh:
            ok = await enable_script(ssh, name)
            if ok:
                console.print(f"[green]Enabled: {name}[/green]")
            else:
                console.print(f"[red]Failed to enable: {name}[/red]")

    asyncio.run(_enable())


@scripts.command("disable")
@click.argument("name")
def scripts_disable(name: str):
    """Disable a script (chmod -x, keeps file)."""
    from asusroutercontrol.merlin.jffs import disable_script
    from asusroutercontrol.ssh import RouterSSH

    async def _disable():
        async with RouterSSH() as ssh:
            ok = await disable_script(ssh, name)
            if ok:
                console.print(f"[yellow]Disabled: {name}[/yellow]")
            else:
                console.print(f"[red]Failed to disable: {name}[/red]")

    asyncio.run(_disable())


@scripts.command("delete")
@click.argument("name")
@click.confirmation_option(prompt="Delete this script?")
def scripts_delete(name: str):
    """Delete a script from /jffs/scripts/."""
    from asusroutercontrol.merlin.jffs import delete_script
    from asusroutercontrol.ssh import RouterSSH

    async def _delete():
        async with RouterSSH() as ssh:
            ok = await delete_script(ssh, name)
            if ok:
                console.print(f"[green]Deleted: {name}[/green]")
            else:
                console.print(f"[red]Script not found: {name}[/red]")

    asyncio.run(_delete())


@scripts.command("hooks")
def scripts_hooks():
    """List all available Merlin script hooks."""
    from asusroutercontrol.merlin.jffs import MERLIN_HOOKS

    table = Table(title="Merlin Script Hooks")
    table.add_column("Hook", style="bold cyan")
    table.add_column("Trigger")
    for hook, desc in MERLIN_HOOKS.items():
        table.add_row(hook, desc)
    console.print(table)


# --- Merlin: Entware ---


@cli.group()
def entware():
    """Manage Entware packages (opkg)."""


@entware.command("status")
def entware_status():
    """Show Entware installation status."""
    from asusroutercontrol.merlin.entware import get_status
    from asusroutercontrol.ssh import RouterSSH

    async def _status():
        async with RouterSSH() as ssh:
            st = await get_status(ssh)
            table = Table(title="Entware Status", show_header=False)
            table.add_column("Key", style="bold cyan")
            table.add_column("Value")
            table.add_row("Installed", "[green]Yes[/green]" if st.installed else "[red]No[/red]")
            usb_str = "[green]Mounted[/green]" if st.usb_mounted else "[red]Not found[/red]"
            table.add_row("USB Storage", usb_str)
            if st.installed:
                table.add_row("Path", st.opt_path or "-")
                table.add_row("Packages", str(st.package_count))
                if st.arch:
                    table.add_row("Architecture", st.arch)
                if st.version:
                    table.add_row("Version", st.version)
            console.print(table)

    asyncio.run(_status())


@entware.command("list")
def entware_list():
    """List installed Entware packages."""
    from asusroutercontrol.merlin.entware import list_installed
    from asusroutercontrol.ssh import RouterSSH

    async def _list():
        async with RouterSSH() as ssh:
            pkgs = await list_installed(ssh)
            if not pkgs:
                console.print("[dim]No Entware packages installed.[/dim]")
                return
            table = Table(title=f"Installed Packages ({len(pkgs)})")
            table.add_column("Package", style="bold")
            table.add_column("Version")
            for p in pkgs:
                table.add_row(p.name, p.version)
            console.print(table)

    asyncio.run(_list())


@entware.command("search")
@click.argument("query")
def entware_search(query: str):
    """Search available Entware packages."""
    from asusroutercontrol.merlin.entware import search_packages
    from asusroutercontrol.ssh import RouterSSH

    async def _search():
        async with RouterSSH() as ssh:
            pkgs = await search_packages(ssh, query)
            if not pkgs:
                console.print(f"[dim]No packages matching '{query}'[/dim]")
                return
            table = Table(title=f"Search: {query} ({len(pkgs)} results)")
            table.add_column("Package", style="bold")
            table.add_column("Version")
            table.add_column("Description")
            for p in pkgs[:25]:
                table.add_row(p.name, p.version, p.description)
            console.print(table)

    asyncio.run(_search())


@entware.command("add")
@click.argument("name")
def entware_add(name: str):
    """Install an Entware package."""
    from asusroutercontrol.merlin.entware import install_package
    from asusroutercontrol.ssh import RouterSSH

    async def _add():
        async with RouterSSH() as ssh:
            console.print(f"Installing {name}...")
            ok = await install_package(ssh, name)
            if ok:
                console.print(f"[green]Installed: {name}[/green]")
            else:
                console.print(f"[red]Failed to install: {name}[/red]")

    asyncio.run(_add())


@entware.command("remove")
@click.argument("name")
@click.confirmation_option(prompt="Remove this package?")
def entware_remove(name: str):
    """Remove an Entware package."""
    from asusroutercontrol.merlin.entware import remove_package
    from asusroutercontrol.ssh import RouterSSH

    async def _remove():
        async with RouterSSH() as ssh:
            ok = await remove_package(ssh, name)
            if ok:
                console.print(f"[green]Removed: {name}[/green]")
            else:
                console.print(f"[red]Failed to remove: {name}[/red]")

    asyncio.run(_remove())


@entware.command("update")
def entware_update():
    """Update opkg package feeds."""
    from asusroutercontrol.merlin.entware import update_feeds
    from asusroutercontrol.ssh import RouterSSH

    async def _update():
        async with RouterSSH() as ssh:
            console.print("Updating package feeds...")
            ok = await update_feeds(ssh)
            if ok:
                console.print("[green]Feeds updated.[/green]")
            else:
                console.print("[red]Update failed.[/red]")

    asyncio.run(_update())


@entware.command("setup")
def entware_setup():
    """Install Entware on the router."""
    from asusroutercontrol.merlin.entware import install_entware
    from asusroutercontrol.ssh import RouterSSH

    async def _setup():
        async with RouterSSH() as ssh:
            result = await install_entware(ssh)
            console.print(result)

    asyncio.run(_setup())


# --- Menubar App ---


@cli.group()
def menubar():
    """Manage the AsusRouterMonitor menubar applet."""


@menubar.command("launch")
def menubar_launch():
    """Launch the menubar app in foreground."""
    try:
        from asusroutercontrol.menubar import main as menubar_main
    except ImportError:
        console.print(
            "[red]menubar dependencies not installed.[/red] "
            "Run: pip install 'asusroutercontrol[menubar]'"
        )
        return
    menubar_main()


@menubar.command("install")
def menubar_install():
    """Install launchd plist — auto-start on login, restart on crash."""
    import subprocess
    from pathlib import Path

    cfg = load_config()
    cfg.ensure_dirs()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.asusroutermonitor.plist"

    project_root = Path(__file__).resolve().parents[2]
    venv_python = project_root / ".venv" / "bin" / "python"
    # PYTHONPATH bypasses the uv UF_HIDDEN flag issue on Homebrew Python 3.11+
    # which silently skips .pth files that carry the hidden filesystem flag.
    src_path = project_root / "src"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.asusroutermonitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>{venv_python}</string>
        <string>-u</string>
        <string>-c</string>
        <string>from asusroutercontrol.menubar import main; main()</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>{src_path}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>ExitTimeOut</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>{cfg.data_dir}/menubar.out.log</string>
    <key>StandardErrorPath</key>
    <string>{cfg.data_dir}/menubar.err.log</string>
    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""
    uid = str(os.getuid())
    # Bootout existing service before writing new plist (avoids "already loaded" error)
    if subprocess.run(
        ["launchctl", "list", "com.asusroutermonitor"],
        capture_output=True,
    ).returncode == 0:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False)
        import time
        time.sleep(0.5)
    plist_path.write_text(plist_content)
    # Use modern bootstrap (load is deprecated on macOS 10.10+)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=False)
    console.print(f"[green]Installed and loaded:[/green] {plist_path}")
    console.print("Menubar app will auto-start on login and restart on crash.")
    console.print("[dim]ThrottleInterval=30s prevents crash-loop death.[/dim]")


@menubar.command("uninstall")
def menubar_uninstall():
    """Remove menubar launchd plist."""
    import subprocess
    from pathlib import Path

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
    if not plist_path.exists():
        console.print("[dim]Not installed.[/dim]")
        return
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False)
    plist_path.unlink()
    console.print(f"[green]Uninstalled:[/green] {plist_path}")


@menubar.command("build")
def menubar_build():
    """Rebuild package and restart the menubar app with latest code."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    venv_python = project_root / ".venv" / "bin" / "python"

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
    was_loaded = plist_path.exists() and subprocess.run(
        ["launchctl", "list", "com.asusroutermonitor"],
        capture_output=True,
    ).returncode == 0

    # 1. Stop launchd agent (bootout replaces deprecated unload)
    uid = str(os.getuid())
    if was_loaded:
        console.print("[dim]Stopping menubar app...[/dim]")
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False)

    # 2. Reinstall package — prefer uv (no pip in uv venvs), fall back to pip
    console.print(f"[dim]Reinstalling from {project_root}...[/dim]")
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "-e", f"{project_root}[menubar]"]
    else:
        python = str(venv_python) if venv_python.exists() else sys.executable
        cmd = [python, "-m", "pip", "install", "-e", f"{project_root}[menubar]"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]install failed:[/red]\n{result.stderr.strip()}")
        return
    # uv sets UF_HIDDEN on venv files; Homebrew Python 3.11 skips hidden .pth files.
    sp = project_root / ".venv" / "lib" / "python3.11" / "site-packages"
    if sp.exists():
        subprocess.run(["chflags", "-R", "nohidden", str(sp)], check=False)
    console.print("[green]Package rebuilt.[/green]")

    # 3. Reload launchd agent (starts fresh app)
    if was_loaded:
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=False)
        console.print("[green]Menubar app restarted.[/green]")
    elif plist_path.exists():
        console.print("[yellow]Plist exists but was not loaded. Load with:[/yellow]")
        console.print(f"  launchctl bootstrap gui/$(id -u) {plist_path}")
    else:
        console.print(
            "[yellow]No launchd plist installed. Run:[/yellow]"
            " asusrouter menubar install"
        )


@menubar.command("status")
def menubar_status():
    """Check if the menubar app is running."""
    import subprocess
    from pathlib import Path

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
    if not plist_path.exists():
        console.print("[dim]Not installed. Run: asusrouter menubar install[/dim]")
        return

    result = subprocess.run(
        ["launchctl", "list", "com.asusroutermonitor"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        console.print("[green]Running[/green]")
        for line in result.stdout.strip().split("\n"):
            console.print(f"  {line}")
    else:
        console.print("[yellow]Installed but not running.[/yellow]")
        console.print(
            "Start with: launchctl load"
            " ~/Library/LaunchAgents/com.asusroutermonitor.plist"
        )


@menubar.command("doctor")
@click.option("--fix", is_flag=True, help="Attempt to re-bootstrap the service if it's unloaded.")
def menubar_doctor(fix: bool):
    """Diagnose and optionally repair menubar launchd service."""
    import subprocess
    from pathlib import Path

    issues = 0
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"

    # 1. Plist exists?
    if not plist_path.exists():
        console.print("[red]\u2717 Plist not found:[/red]", str(plist_path))
        console.print("  Fix: [bold]asusrouter menubar install[/bold]")
        return  # nothing else to check
    console.print("[green]\u2713 Plist exists[/green]")

    # 2. Service loaded?
    svc_result = subprocess.run(
        ["launchctl", "list", "com.asusroutermonitor"],
        capture_output=True, text=True,
    )
    service_loaded = svc_result.returncode == 0
    if service_loaded:
        console.print("[green]\u2713 Service loaded[/green]")
        # Extract PID if present
        for line in svc_result.stdout.splitlines():
            if '"PID"' in line:
                console.print(f"  {line.strip()}")
    else:
        console.print("[red]\u2717 Service NOT loaded[/red] (launchd has abandoned it)")
        issues += 1

    # 3. Venv python exists?
    project_root = Path(__file__).resolve().parents[2]
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        console.print(f"[green]\u2713 Venv python exists:[/green] {venv_python}")
    else:
        console.print(f"[red]\u2717 Venv python missing:[/red] {venv_python}")
        issues += 1

    # 4. Can import objc?
    if venv_python.exists():
        import_check = subprocess.run(
            [str(venv_python), "-c", "import objc; import AppKit; print('ok')"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(project_root / "src")},
        )
        if import_check.returncode == 0 and "ok" in import_check.stdout:
            console.print("[green]\u2713 objc + AppKit importable[/green]")
        else:
            console.print("[red]\u2717 Import failed:[/red]")
            console.print(f"  {import_check.stderr.strip()[-200:]}")
            console.print("  Fix: [bold]asusrouter menubar build[/bold]")
            issues += 1

    # 5. Check stderr log for recent crash patterns
    cfg = load_config()
    err_log = cfg.data_dir / "menubar.err.log"
    if err_log.exists():
        tail = err_log.read_text()[-2000:]
        crash_keywords = ["ImportError", "ModuleNotFoundError", "Load failed", "Traceback"]
        found = [kw for kw in crash_keywords if kw in tail]
        if found:
            console.print(
                f"[yellow]\u26a0 Recent crash signatures in stderr log:[/yellow]"
                f" {', '.join(found)}"
            )
            console.print(f"  Log: {err_log}")
            issues += 1
        else:
            console.print("[green]\u2713 No crash patterns in stderr log[/green]")
    else:
        console.print("[dim]  No stderr log file yet[/dim]")

    # Summary
    if issues == 0:
        console.print("\n[bold green]All checks passed.[/bold green]")
        return

    console.print(f"\n[bold yellow]{issues} issue(s) found.[/bold yellow]")

    # Auto-fix: re-bootstrap if service is unloaded
    if not service_loaded and fix:
        uid = str(os.getuid())
        console.print("[dim]Re-bootstrapping service...[/dim]")
        r = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            console.print("[green]\u2713 Service re-bootstrapped successfully.[/green]")
        else:
            console.print(f"[red]Bootstrap failed:[/red] {r.stderr.strip()}")
            console.print("  Try: [bold]asusrouter menubar install[/bold]")
    elif not service_loaded:
        console.print("Run [bold]asusrouter menubar doctor --fix[/bold] to re-bootstrap.")


# --- Scheduler & Monitoring ---


@cli.group()
def scheduler():
    """Manage the background monitoring scheduler."""


@scheduler.command("start")
def scheduler_start():
    """Run the scheduler in foreground (Ctrl+C to stop)."""
    import logging

    from asusroutercontrol.scheduler import MonitorScheduler

    cfg = load_config()
    cfg.ensure_dirs()

    log_path = cfg.data_dir / "scheduler.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_path)),
        ],
    )

    async def _run():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        sched = MonitorScheduler(store, cfg)
        try:
            console.print(
                f"[bold]Scheduler running[/bold] — "
                f"speed tests at {cfg.speedtest_times}, "
                f"probes every {cfg.probe_interval}s, "
                f"polls every {cfg.poll_interval}s"
            )
            console.print(f"Log: {log_path}")
            await sched.run()
        except KeyboardInterrupt:
            sched.stop()
            console.print("\n[yellow]Scheduler stopped.[/yellow]")
        finally:
            await store.close()

    asyncio.run(_run())


@scheduler.command("install")
def scheduler_install():
    """Install launchd plist for auto-start on login."""
    import shutil
    import subprocess
    from pathlib import Path

    cfg = load_config()
    cfg.ensure_dirs()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.asusroutercontrol.scheduler.plist"

    # Find the asusrouter binary
    exe = shutil.which("asusrouter")
    if not exe:
        console.print("[red]Cannot find 'asusrouter' on PATH.[/red]")
        return

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.asusroutercontrol.scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>scheduler</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{cfg.data_dir}/scheduler.out.log</string>
    <key>StandardErrorPath</key>
    <string>{cfg.data_dir}/scheduler.err.log</string>
    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    console.print(f"[green]Installed and loaded:[/green] {plist_path}")
    console.print("Scheduler will auto-start on login and restart on crash.")


@scheduler.command("uninstall")
def scheduler_uninstall():
    """Remove launchd plist."""
    import subprocess
    from pathlib import Path

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutercontrol.scheduler.plist"
    if not plist_path.exists():
        console.print("[dim]Not installed.[/dim]")
        return
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    plist_path.unlink()
    console.print(f"[green]Uninstalled:[/green] {plist_path}")


@scheduler.command("status")
def scheduler_status():
    """Check if the scheduler is running."""
    import subprocess
    from pathlib import Path

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutercontrol.scheduler.plist"
    if not plist_path.exists():
        console.print("[dim]Not installed. Run: asusrouter scheduler install[/dim]")
        return

    result = subprocess.run(
        ["launchctl", "list", "com.asusroutercontrol.scheduler"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        console.print("[green]Running[/green]")
        for line in result.stdout.strip().split("\n"):
            console.print(f"  {line}")
    else:
        console.print("[yellow]Installed but not running.[/yellow]")
        console.print(
            "Start with: launchctl load"
            " ~/Library/LaunchAgents/com.asusroutercontrol.scheduler.plist"
        )

@cli.group("ssh")
def ssh_group():
    """Manage SSH trust and host key pinning."""


@ssh_group.group()
def trust():
    """Manage pinned SSH host keys."""


@trust.command("show")
def ssh_trust_show():
    """Show pinned SSH host keys and fingerprints."""
    from asusroutercontrol.ssh import RouterSSH

    client = RouterSSH()
    entries = client.list_pinned_hosts()
    console.print(f"[dim]known_hosts: {client.known_hosts_path}[/dim]")
    if not entries:
        console.print("[dim]No pinned SSH host keys.[/dim]")
        return
    table = Table(title=f"Pinned SSH Host Keys ({len(entries)})")
    table.add_column("Host", style="bold")
    table.add_column("Algorithm")
    table.add_column("SHA256")
    table.add_column("MD5")
    for e in entries:
        table.add_row(e.host_token, e.algorithm, e.sha256, e.md5)
    console.print(table)


@trust.command("verify")
@click.option("--host", default=None, help="Router host override")
@click.option("--port", type=int, default=None, help="Router SSH port override")
def ssh_trust_verify(host: str | None, port: int | None):
    """Verify current SSH trust state for a host."""
    from asusroutercontrol.ssh import HostKeyMismatchError, RouterSSH, UnknownHostKeyError

    async def _verify():
        client = RouterSSH(hostname=host, port=port)
        try:
            await client.connect()
            console.print("[green]SSH trust verification passed.[/green]")
        except UnknownHostKeyError as e:
            d = e.details
            console.print("[yellow]Unknown host key (not pinned).[/yellow]")
            console.print(f"Host: {d.host_token}")
            console.print(f"SHA256: {d.sha256}")
            console.print(f"MD5: {d.md5}")
            console.print(
                "Action: run [bold]asusrouter ssh trust rotate[/bold] "
                "to pin after verification."
            )
        except HostKeyMismatchError as e:
            console.print("[red]Host key mismatch detected.[/red]")
            console.print(f"Expected SHA256: {e.expected_sha256}")
            console.print(f"Presented SHA256: {e.presented_sha256}")
            console.print("Action: verify router identity, then rotate/revoke trust entry.")
        finally:
            await client.disconnect()

    asyncio.run(_verify())


@trust.command("rotate")
@click.option("--host", default=None, help="Router host override")
@click.option("--port", type=int, default=None, help="Router SSH port override")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def ssh_trust_rotate(host: str | None, port: int | None, yes: bool):
    """Rotate/replace pinned key with currently presented host key."""
    from asusroutercontrol.ssh import RouterSSH

    if not yes and not click.confirm("Replace pinned SSH host key for this target?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    async def _rotate():
        client = RouterSSH(hostname=host, port=port)
        details = await client.rotate_pinned_host_key(host=host, port=port)
        console.print("[green]Pinned SSH host key updated.[/green]")
        console.print(f"Host: {details.host_token}")
        console.print(f"SHA256: {details.sha256}")

    asyncio.run(_rotate())


@trust.command("revoke")
@click.option("--host", default=None, help="Router host override")
@click.option("--port", type=int, default=None, help="Router SSH port override")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def ssh_trust_revoke(host: str | None, port: int | None, yes: bool):
    """Remove pinned SSH host key entry for a target."""
    from asusroutercontrol.ssh import RouterSSH

    if not yes and not click.confirm("Remove pinned SSH host key for this target?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    client = RouterSSH(hostname=host, port=port)
    removed = client.revoke_pinned_host_key(host=host, port=port)
    if removed:
        console.print("[green]Pinned SSH host key removed.[/green]")
    else:
        console.print("[dim]No matching pinned host entry found.[/dim]")


@cli.command()
@click.option("--no-store", is_flag=True, help="Don't save result to database")
@click.option(
    "--source",
    "-s",
    default=None,
    type=str,
    help=(
        "Run a single provider only "
        "(e.g. ookla, cloudflare, cachefly, cloudfront, fastly, http_download)"
    ),
)
def speedtest(no_store: bool, source: str | None):
    """Run a multi-source speed test."""
    import json as json_mod

    from asusroutercontrol.speedtest import run_speed_test_detailed

    cfg = load_config()
    cfg.ensure_dirs()

    async def _speedtest():
        with console.status("Running speed test..."):
            composite, providers = await run_speed_test_detailed(
                source=source
            )

        if composite.error:
            console.print(f"[red]Speed test failed: {composite.error}[/red]")
            return

        # Per-provider breakdown
        if len(providers) > 1 or (providers and not source):
            pt = Table(title="Provider Results")
            pt.add_column("Provider", style="bold")
            pt.add_column("Download")
            pt.add_column("Upload")
            pt.add_column("Ping")
            pt.add_column("Jitter")
            pt.add_column("POP")
            pt.add_column("Cache")
            pt.add_column("Server")
            for r in providers:
                if r.error:
                    pt.add_row(
                        r.provider, f"[red]{r.error}[/red]",
                        "", "", "", "", "", "",
                    )
                else:
                    dl = f"{r.download_bps / 1e6:.1f}" if r.download_bps else "—"
                    ul = f"{r.upload_bps / 1e6:.1f}" if r.upload_bps else "—"
                    pg = f"{r.ping_ms:.1f}" if r.ping_ms is not None else "—"
                    jt = f"{r.jitter_ms:.1f}" if r.jitter_ms is not None else "—"
                    pop = r.pop_code or "—"
                    cache_status = r.cache_status or "—"
                    pt.add_row(
                        r.provider, f"{dl} Mbps", f"{ul} Mbps",
                        f"{pg} ms", f"{jt} ms",
                        pop, cache_status,
                        r.server_name or "—",
                    )
            console.print(pt)

        # Composite summary
        details = json_mod.loads(composite.provider_details_json)
        conf = details.get("confidence", 0)
        conf_color = "green" if conf >= 80 else "yellow" if conf >= 50 else "red"

        table = Table(title="Composite Result", show_header=False)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        table.add_row(
            "Download",
            f"{(composite.download_bps or 0) / 1e6:.1f} Mbps",
        )
        table.add_row(
            "Upload",
            f"{(composite.upload_bps or 0) / 1e6:.1f} Mbps",
        )
        table.add_row("Ping", f"{composite.ping_ms or 0:.1f} ms")
        if composite.jitter_ms is not None:
            table.add_row("Jitter", f"{composite.jitter_ms:.1f} ms")
        table.add_row(
            "Confidence",
            f"[{conf_color}]{conf}/100[/{conf_color}]",
        )
        table.add_row("Peak Hour", "Yes" if composite.is_peak else "No")
        outliers = details.get("outliers", [])
        if outliers:
            table.add_row(
                "Outliers",
                f"[yellow]{', '.join(outliers)}[/yellow]",
            )
        console.print(table)

        if not no_store:
            store = DataStore(cfg.data_dir / "router.db")
            await store.open()
            await store.insert_speed_test(composite)
            await store.close()
            console.print("[dim]Result saved to database.[/dim]")

    asyncio.run(_speedtest())


# --- Analysis & Optimization ---


@cli.command()
@click.option("--days", "-d", default=30, help="Window in days")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def trends(days: int, as_json: bool):
    """Show performance trends (speed, latency, RAM, WiFi)."""
    import json as json_mod

    from asusroutercontrol.analyzer import analyze_trends

    cfg = load_config()

    async def _trends():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            data = await analyze_trends(store, days=days)
            if as_json:
                console.print(json_mod.dumps(data, indent=2, default=str))
                return

            if not data:
                console.print("[dim]Not enough data for trends yet.[/dim]")
                return

            table = Table(title=f"Performance Trends ({days}d)")
            table.add_column("Metric", style="bold cyan")
            table.add_column("Avg")
            table.add_column("Trend")
            table.add_column("Rate")
            table.add_column("R²", justify="right")

            dl = data.get("download", {})
            if dl:
                table.add_row(
                    "Download",
                    f"{dl.get('avg_mbps', 0):.0f} Mbps",
                    dl.get("arrow", "—"),
                    f"{dl.get('slope_mbps_per_week', 0):+.1f} Mbps/wk",
                    f"{dl.get('r_squared', 0):.2f}",
                )
            ul = data.get("upload", {})
            if ul:
                table.add_row(
                    "Upload",
                    f"{ul.get('avg_mbps', 0):.0f} Mbps",
                    ul.get("arrow", "—"),
                    f"{ul.get('slope_mbps_per_week', 0):+.1f} Mbps/wk",
                    f"{ul.get('r_squared', 0):.2f}",
                )
            lat = data.get("latency", {})
            if lat:
                table.add_row(
                    "Latency",
                    f"{lat.get('avg_ms', 0):.1f} ms",
                    lat.get("arrow", "—"),
                    f"{lat.get('slope_ms_per_week', 0):+.2f} ms/wk",
                    f"{lat.get('r_squared', 0):.2f}",
                )
            ram = data.get("ram", {})
            if ram:
                table.add_row(
                    "RAM",
                    f"{ram.get('avg_pct', 0):.0f}%",
                    ram.get("arrow", "—"),
                    f"{ram.get('slope_pct_per_week', 0):+.1f}%/wk",
                    f"{ram.get('r_squared', 0):.2f}",
                )
            for bk in ("wifi_2.4", "wifi_5"):
                w = data.get(bk, {})
                if w:
                    label = bk.replace("wifi_", "") + "GHz RSSI"
                    table.add_row(
                        label,
                        f"{w.get('avg_db', 0):.0f} dBm",
                        w.get("arrow", "—"),
                        f"{w.get('slope_db_per_week', 0):+.1f} dB/wk",
                        f"{w.get('r_squared', 0):.2f}",
                    )
            console.print(table)

            loss = data.get("packet_loss", {})
            if loss and loss.get("events", 0):
                console.print(
                    f"\n[yellow]Packet loss:[/yellow] {loss['events']} events "
                    f"({loss.get('per_week', 0):.1f}/week)"
                )
        finally:
            await store.close()

    asyncio.run(_trends())


@cli.command()
@click.option("--days", "-d", default=30, help="Window in days")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def analyze(days: int, as_json: bool):
    """Run full analysis: recommendations + SLA + optimization suggestions."""
    import json as json_mod

    from asusroutercontrol.analyzer import analyze_isp_sla
    from asusroutercontrol.optimizer import generate_recommendations, suggest_settings

    cfg = load_config()

    async def _analyze():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            recs = await generate_recommendations(store, days=days)
            sla = await analyze_isp_sla(store, days=days)
            settings = await suggest_settings(store)

            if as_json:
                console.print(json_mod.dumps(
                    {"recommendations": recs, "sla": sla, "settings": settings},
                    indent=2, default=str,
                ))
                return

            # Recommendations
            console.print("[bold]Recommendations[/bold]")
            for r in recs:
                pri = r["priority"]
                pri_colors = {"high": "red", "medium": "yellow", "low": "cyan", "info": "dim"}
                color = pri_colors.get(pri, "white")
                console.print(
                    f"  [{color}][{pri.upper()}][/{color}] {r['description']}"
                )
                if r.get("action") and r["action"] != "No action needed.":
                    console.print(f"    → {r['action']}")

            # SLA
            if sla.get("tests", 0) >= 1:
                console.print(f"\n[bold]ISP SLA Score:[/bold] {sla.get('sla_score', 0):.0f}/100")
                dl_sla = sla.get("download", {})
                if dl_sla:
                    console.print(
                        f"  Download: {dl_sla.get('avg_mbps', 0):.0f} Mbps avg, "
                        f"{dl_sla.get('pct_meeting_plan', 0):.0f}% meeting plan"
                    )

            # Settings suggestions
            if settings and not settings[0].get("message"):
                console.print("\n[bold]Suggested Settings Changes[/bold]")
                for s in settings:
                    console.print(
                        f"  [cyan]{s['key']}[/cyan]: "
                        f"{s['current']} → [green]{s['proposed']}[/green]"
                    )
                    console.print(f"    {s['rationale']}")
        finally:
            await store.close()

    asyncio.run(_analyze())


@cli.command("config-history")
@click.option("--days", "-d", default=90, help="Window in days")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def config_history(days: int, as_json: bool):
    """Show config change history and their performance impact."""
    import json as json_mod

    from asusroutercontrol.optimizer import correlate_config_performance

    cfg = load_config()

    async def _config_history():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            events = await store.get_config_events(days=days)
            corr = await correlate_config_performance(store, days=days)

            if as_json:
                console.print(json_mod.dumps(
                    {"events": events, "correlations": corr},
                    indent=2, default=str,
                ))
                return

            if not events:
                console.print("[dim]No config changes recorded yet.[/dim]")
                return

            table = Table(title=f"Config Changes ({days}d)")
            table.add_column("Time")
            table.add_column("Type")
            table.add_column("Description")
            table.add_column("DL Impact")
            table.add_column("Latency Impact")

            corr_map = {c["timestamp"]: c for c in corr}
            for ev in events:
                c = corr_map.get(ev["timestamp"], {})
                dl_impact = "—"
                if "download_delta_pct" in c:
                    v = c["download_delta_pct"]
                    color = "green" if v > 5 else "red" if v < -5 else "dim"
                    dl_impact = f"[{color}]{v:+.1f}%[/{color}]"
                lat_impact = "—"
                if "latency_delta_ms" in c:
                    v = c["latency_delta_ms"]
                    color = "green" if v < -1 else "red" if v > 1 else "dim"
                    lat_impact = f"[{color}]{v:+.1f}ms[/{color}]"
                table.add_row(
                    ev["timestamp"][:16],
                    ev["event_type"],
                    ev["description"][:60],
                    dl_impact,
                    lat_impact,
                )
            console.print(table)
        finally:
            await store.close()

    asyncio.run(_config_history())


@cli.command("config-snapshot")
@click.option("--show", "show_latest", is_flag=True, help="Show latest snapshot keys")
def config_snapshot(show_latest: bool):
    """Take a config snapshot now, or show latest with --show."""
    import json as json_mod

    from asusroutercontrol.probes import diff_config_snapshots, probe_config
    from asusroutercontrol.ssh import RouterSSH

    cfg = load_config()

    async def _snapshot():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            if show_latest:
                latest = await store.get_latest_config_snapshot()
                if not latest:
                    console.print("[dim]No snapshots yet. Run without --show to take one.[/dim]")
                    return
                data = json_mod.loads(latest["nvram_json"])
                table = Table(title=f"Config Snapshot ({latest['timestamp'][:16]})")
                table.add_column("Key", style="bold cyan")
                table.add_column("Value")
                for k in sorted(data):
                    table.add_row(k, str(data[k])[:80])
                console.print(table)
                if latest.get("diff_summary"):
                    console.print(f"\n[bold]Changes from previous:[/bold] {latest['diff_summary']}")
                return

            # Take new snapshot
            async with RouterSSH() as ssh:
                with console.status("Probing router config..."):
                    snap = await probe_config(ssh, source="cli")
                    # Check for diff
                    prev = await store.get_latest_config_snapshot()
                    if prev:
                        diff = diff_config_snapshots(
                            snap.nvram_json, prev["nvram_json"]
                        )
                        snap.diff_summary = diff
                    await store.insert_config_snapshot(snap)

            console.print("[green]Config snapshot saved.[/green]")
            if snap.diff_summary:
                console.print(f"Changes: {snap.diff_summary}")
            else:
                console.print("[dim]No changes from previous snapshot.[/dim]")
        finally:
            await store.close()

    asyncio.run(_snapshot())


@cli.command()
@click.option("--days", "-d", default=7, help="Report window in days")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--export", "export_path", type=click.Path(), help="Export report to file")
def report(days: int, as_json: bool, export_path: str | None):
    """Generate a network health report."""
    from pathlib import Path

    from asusroutercontrol.reporting import (
        export_report_json,
        generate_report,
        print_report,
    )

    cfg = load_config()

    async def _report():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            data = await generate_report(store, days=days)

            if as_json:
                import json

                console.print(json.dumps(data, indent=2, default=str))
            else:
                print_report(data, console)

            if export_path:
                export_report_json(data, Path(export_path))
                console.print(f"[green]Report exported to {export_path}[/green]")
        finally:
            await store.close()

    asyncio.run(_report())


# --- Optimization ---


@cli.group()
def optimize():
    """Router optimization: audit, suggest, apply, verify."""


@optimize.command("audit")
def optimize_audit():
    """Run service, sysctl, and WiFi channel audit."""
    from asusroutercontrol.probes import probe_services, probe_sysctl, probe_wifi_channels
    from asusroutercontrol.ssh import RouterSSH

    async def _audit():
        async with RouterSSH() as ssh:
            with console.status("Running service audit..."):
                svc = await probe_services(ssh)
            with console.status("Running sysctl audit..."):
                sysctl = await probe_sysctl(ssh)
            with console.status("Running WiFi channel survey..."):
                channels = await probe_wifi_channels(ssh)

        # Services
        bloat = [s for s in svc.services if s.is_bloat]
        if bloat:
            t = Table(title=f"Bloat Services ({len(bloat)} found, ~{svc.bloat_rss_kb}KB)")
            t.add_column("Name", style="bold")
            t.add_column("PID")
            t.add_column("Threads", justify="right")
            t.add_column("VSZ KB", justify="right")
            t.add_column("Reason")
            for s in sorted(bloat, key=lambda x: x.rss_kb, reverse=True):
                t.add_row(s.name, str(s.pid), str(s.threads), str(s.rss_kb), s.bloat_reason)
            console.print(t)
        else:
            console.print("[green]No bloat services detected.[/green]")

        # Sysctl
        suboptimal = [e for e in sysctl.entries if not e.is_optimal]
        if suboptimal:
            t = Table(title=f"Sysctl Tuning ({sysctl.optimal_count}/{sysctl.total_count} optimal)")
            t.add_column("Key", style="bold cyan")
            t.add_column("Current")
            t.add_column("Recommended")
            t.add_column("Note")
            for e in suboptimal:
                t.add_row(e.key, e.current, f"[green]{e.recommended}[/green]", e.note)
            console.print(t)
        else:
            console.print(
                f"[green]All {sysctl.total_count} sysctl values optimal.[/green]"
            )

        # WiFi channels
        for survey in channels:
            if survey.best_channel:
                console.print(
                    f"[yellow]{survey.band}GHz:[/yellow] {survey.best_reason}"
                )
            elif survey.entries:
                console.print(
                    f"[green]{survey.band}GHz:[/green] Current channel "
                    f"{survey.current_channel} looks optimal"
                )
            else:
                console.print(
                    f"[dim]{survey.band}GHz:[/dim] No channel survey data available"
                )

    asyncio.run(_audit())


@optimize.command("suggest")
@click.option("--days", "-d", default=30, help="Analysis window in days")
def optimize_suggest(days: int):
    """Show all optimization suggestions (NVRAM + probes)."""
    from asusroutercontrol.optimizer import generate_recommendations, suggest_settings

    cfg = load_config()

    async def _suggest():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            recs = await generate_recommendations(store, days=days)
            settings = await suggest_settings(store)

            # Recommendations
            actionable = [r for r in recs if r.get("priority") in ("high", "medium")]
            if actionable:
                console.print(f"[bold]Recommendations ({len(actionable)} actionable)[/bold]")
                for r in actionable:
                    pri = r["priority"]
                    color = "red" if pri == "high" else "yellow"
                    console.print(f"  [{color}][{pri.upper()}][/{color}] {r['description']}")
                    if r.get("action") and r["action"] != "No action needed.":
                        console.print(f"    → {r['action']}")
            else:
                console.print("[green]No actionable recommendations.[/green]")

            # Settings
            if settings and not settings[0].get("message"):
                console.print(f"\n[bold]NVRAM Suggestions ({len(settings)})[/bold]")
                t = Table()
                t.add_column("Key", style="bold cyan")
                t.add_column("Current")
                t.add_column("Proposed")
                t.add_column("Risk")
                for s in settings:
                    risk_color = {"low": "green", "medium": "yellow", "high": "red"}
                    rc = risk_color.get(s.get("risk", "low"), "white")
                    t.add_row(
                        s["key"], s["current"],
                        f"[green]{s['proposed']}[/green]",
                        f"[{rc}]{s.get('risk', 'low')}[/{rc}]",
                    )
                console.print(t)
                for s in settings:
                    console.print(f"  [dim]{s['key']}:[/dim] {s['rationale']}")
            else:
                msg = settings[0].get("message", "") if settings else "No suggestions."
                console.print(f"[dim]{msg}[/dim]")
        finally:
            await store.close()

    asyncio.run(_suggest())


@optimize.command("benchmark")
@click.option(
    "--iterations",
    "-n",
    default=25,
    show_default=True,
    help="Iterations per metric for runtime baselining.",
)
@click.option(
    "--days",
    "-d",
    default=7,
    show_default=True,
    help="Lookback window (days) for query metrics.",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON metrics")
def optimize_benchmark(iterations: int, days: int, as_json: bool):
    """Run deterministic local benchmark for datastore query/write timings."""
    from asusroutercontrol.benchmark import run_datastore_benchmark

    cfg = load_config()

    async def _benchmark():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            with console.status("Running optimize benchmark..."):
                payload = await run_datastore_benchmark(
                    store,
                    iterations=iterations,
                    days=days,
                )
        finally:
            await store.close()

        if as_json:
            import json

            console.print(json.dumps(payload, indent=2))
            return

        console.print("[bold]Optimize Benchmark[/bold]")
        console.print(
            f"[dim]Window: {payload['days']}d | Iterations: {payload['iterations']}[/dim]"
        )

        metric_order = [
            "query_speed_tests_ms",
            "query_latency_probes_ms",
            "query_wifi_snapshots_ms",
            "query_devices_ms",
            "write_temp_insert_ms",
            "write_commit_ms",
        ]
        metrics = payload.get("metrics", {})
        t = Table(title="Datastore Runtime Baseline")
        t.add_column("Metric", style="bold cyan")
        t.add_column("Count", justify="right")
        t.add_column("p50 ms", justify="right")
        t.add_column("p95 ms", justify="right")
        t.add_column("Mean ms", justify="right")
        t.add_column("Ops/s", justify="right")
        for metric in metric_order:
            summary = metrics.get(metric, {})
            count = int(summary.get("count", 0))
            p50 = float(summary.get("p50_ms", 0.0))
            p95 = float(summary.get("p95_ms", 0.0))
            mean_ms = float(summary.get("mean_ms", 0.0))
            ops_per_sec = float(summary.get("ops_per_sec", 0.0))
            t.add_row(
                metric,
                str(count),
                f"{p50:.3f}",
                f"{p95:.3f}",
                f"{mean_ms:.3f}",
                f"{ops_per_sec:.3f}",
            )
        console.print(t)

        sample_sizes = payload.get("sample_sizes", {})
        if sample_sizes:
            summary = ", ".join(
                f"{name}={sample_sizes[name]}" for name in sorted(sample_sizes)
            )
            console.print(f"[dim]Sample sizes: {summary}[/dim]")

    asyncio.run(_benchmark())


@optimize.command("apply")
@click.option("--key", "-k", help="Apply a single NVRAM key")
@click.option("--value", "-v", help="Value for --key")
@click.option("--clear", is_flag=True, help="Set --key to empty string.")
@click.option("--all-suggestions", is_flag=True, help="Apply all suggest_settings() results")
@click.option("--dry-run", is_flag=True, help="Show what would change without applying")
def optimize_apply(
    key: str | None,
    value: str | None,
    clear: bool,
    all_suggestions: bool,
    dry_run: bool,
):
    """Apply optimization settings to the router."""
    from asusroutercontrol.executor import apply_nvram_setting, apply_optimization_batch
    from asusroutercontrol.optimizer import suggest_settings
    from asusroutercontrol.ssh import RouterSSH

    if key is not None and value is not None and clear:
        raise click.ClickException("Use either --value or --clear, not both.")

    cfg = load_config()

    async def _apply():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            async with RouterSSH() as ssh:
                has_single_target = key is not None and (value is not None or clear)
                if has_single_target:
                    target_value = "" if clear else (value or "")
                    result = await apply_nvram_setting(
                        ssh, store, key, target_value, dry_run=dry_run,
                    )
                    status = (
                        "[green]OK[/green]"
                        if result.success
                        else f"[red]FAIL: {result.error}[/red]"
                    )
                    console.print(
                        f"{result.key}: {result.old_value!r} → {result.new_value!r} {status}"
                    )
                    if result.service_restarted:
                        console.print(f"  Service: {result.service_restarted}")

                elif all_suggestions:
                    suggestions = await suggest_settings(store)
                    if not suggestions or suggestions[0].get("message"):
                        console.print("[dim]No suggestions to apply.[/dim]")
                        return

                    if not dry_run:
                        console.print(
                            f"[yellow]Applying {len(suggestions)} changes...[/yellow]"
                        )
                    results = await apply_optimization_batch(
                        ssh, store, suggestions, dry_run=dry_run,
                    )
                    for r in results:
                        status = "[green]OK[/green]" if r.success else f"[red]{r.error}[/red]"
                        label = "[DRY RUN] " if dry_run else ""
                        console.print(
                            f"  {label}{r.key}: {r.old_value!r} → {r.new_value!r} {status}"
                        )
                else:
                    console.print(
                        "Usage: --key KEY (--value VALUE | --clear), or --all-suggestions\n"
                        "Add --dry-run to preview without applying."
                    )
        finally:
            await store.close()

    asyncio.run(_apply())


@optimize.command("verify")
def optimize_verify():
    """Verify Deep Dive findings execution status."""
    from asusroutercontrol.executor import verify_deep_dive_findings
    from asusroutercontrol.ssh import RouterSSH

    async def _verify():
        async with RouterSSH() as ssh:
            with console.status("Verifying optimization status..."):
                findings = await verify_deep_dive_findings(ssh)

        t = Table(title="Deep Dive Findings Status")
        t.add_column("#", justify="right")
        t.add_column("Finding")
        t.add_column("Expected")
        t.add_column("Actual")
        t.add_column("Status")
        for f in findings:
            actual_style = "green" if f["passed"] else "red"
            t.add_row(
                str(f["finding"]), f["title"],
                f["expected"], f"[{actual_style}]{f['actual']}[/{actual_style}]",
                f["status"],
            )
        console.print(t)

        passed = sum(1 for f in findings if f["passed"])
        total = len(findings)
        if passed == total:
            console.print(f"[green]All {total} findings verified.[/green]")
        else:
            console.print(
                f"[yellow]{passed}/{total} findings applied. "
                f"Use 'asusrouter optimize apply' to fix remaining.[/yellow]"
            )

    asyncio.run(_verify())

@optimize.group("rollout")
def optimize_rollout():
    """Safe staged rollout of optimization settings with connectivity gates."""


@optimize_rollout.command("plan")
@click.option("--profile", default="last-rollback", show_default=True)
@click.option(
    "--no-disconnect/--allow-disconnect",
    default=True,
    show_default=True,
    help="Require strict no-drop policy (recommended).",
)
@click.option(
    "--allow-disruptive",
    is_flag=True,
    help="Allow restart_wireless/reboot mapped steps even under strict policy.",
)
def optimize_rollout_plan(profile: str, no_disconnect: bool, allow_disruptive: bool):
    """Preview step order, impact, and policy gates for a rollout profile."""
    from asusroutercontrol.rollout import get_rollout_plan_rows

    async def _plan():
        try:
            rows = await get_rollout_plan_rows(
                profile,
                no_disconnect=no_disconnect,
                allow_disruptive=allow_disruptive,
            )
        except Exception as e:
            console.print(f"[red]Failed to build rollout plan: {e}[/red]")
            return

        table = Table(title=f"Rollout Plan: {profile}")
        table.add_column("Key", style="bold cyan")
        table.add_column("Current")
        table.add_column("Target")
        table.add_column("Service")
        table.add_column("Action")
        table.add_column("Policy")
        for row in rows:
            action_color = "yellow" if row["action"] == "skip" else "green"
            policy = "blocked" if row["blocked"] else "allowed"
            table.add_row(
                row["key"],
                row["current"] if row["current"] != "" else "''",
                row["target"],
                row["service"],
                f"[{action_color}]{row['action']}[/{action_color}]",
                policy,
            )
        console.print(table)
        if no_disconnect:
            console.print("[dim]Strict mode: any Wi-Fi/LAN drop triggers rollback + stop.[/dim]")

    asyncio.run(_plan())


@optimize_rollout.command("run")
@click.option("--profile", default="last-rollback", show_default=True)
@click.option("--watch-mac", default=None, help="Optional target phone MAC.")
@click.option(
    "--max-loss",
    default=5.0,
    show_default=True,
    type=click.FloatRange(0.0, 100.0),
    help="Maximum allowed packet loss percentage during hold window.",
)
@click.option(
    "--hold-seconds",
    default=90,
    show_default=True,
    type=click.IntRange(10, 1800),
    help="Post-step hold window for connectivity gate checks.",
)
@click.option(
    "--poll-seconds",
    default=3.0,
    show_default=True,
    type=click.FloatRange(0.5, 30.0),
    help="Gate polling interval.",
)
@click.option(
    "--no-disconnect/--allow-disconnect",
    default=True,
    show_default=True,
    help="Require strict no-drop policy.",
)
@click.option(
    "--allow-disruptive",
    is_flag=True,
    help="Allow restart_wireless/reboot mapped steps.",
)
@click.option("--dry-run", is_flag=True, help="Evaluate flow without writing settings.")
def optimize_rollout_run(
    profile: str,
    watch_mac: str | None,
    max_loss: float,
    hold_seconds: int,
    poll_seconds: float,
    no_disconnect: bool,
    allow_disruptive: bool,
    dry_run: bool,
):
    """Run staged rollout with hard connectivity gates and rollback-on-drop."""
    from asusroutercontrol.rollout import run_rollout_profile

    cfg = load_config()

    async def _run():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            result = await run_rollout_profile(
                store,
                profile=profile,
                max_loss_pct=max_loss,
                hold_seconds=hold_seconds,
                poll_seconds=poll_seconds,
                watch_mac=watch_mac,
                no_disconnect=no_disconnect,
                allow_disruptive=allow_disruptive,
                dry_run=dry_run,
            )
        except Exception as e:
            console.print(f"[red]Rollout failed to execute: {e}[/red]")
            return
        finally:
            await store.close()

        table = Table(title=f"Rollout Results: {profile}")
        table.add_column("Key", style="bold cyan")
        table.add_column("Target")
        table.add_column("Service")
        table.add_column("Status")
        table.add_column("Reason")
        for step in result.step_results:
            color = {
                "pass": "green",
                "skipped": "yellow",
                "rolled_back": "yellow",
                "fail": "red",
            }.get(step.status, "white")
            table.add_row(
                step.key,
                step.target,
                step.service or "-",
                f"[{color}]{step.status}[/{color}]",
                step.reason[:140],
            )
        console.print(table)
        if result.completed:
            console.print("[green]Rollout completed successfully.[/green]")
        else:
            console.print(f"[red]Rollout stopped: {result.aborted_reason or 'unknown'}[/red]")

    asyncio.run(_run())


@optimize_rollout.command("status")
@click.option("--profile", default="last-rollback", show_default=True)
@click.option("--days", default=30, show_default=True, type=click.IntRange(1, 365))
def optimize_rollout_status(profile: str, days: int):
    """Show last known rollout phase per step for a profile."""
    from asusroutercontrol.rollout import rollout_status

    cfg = load_config()

    async def _status():
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            state = await rollout_status(store, profile=profile, days=days)
        finally:
            await store.close()

        table = Table(title=f"Rollout Status: {profile}")
        table.add_column("Key", style="bold cyan")
        table.add_column("Target")
        table.add_column("Phase")
        table.add_column("Timestamp")
        for row in state["rows"]:
            phase = row["phase"]
            color = (
                "green" if phase in {"pass", "dry_run_pass"} else
                "yellow" if phase.startswith("skip") or phase == "not_started" else
                "red" if "fail" in phase or "abort" in phase else
                "white"
            )
            table.add_row(
                row["key"],
                row["target"],
                f"[{color}]{phase}[/{color}]",
                row["timestamp"][:19] if row["timestamp"] else "-",
            )
        console.print(table)
        console.print(
            f"Run phase: [bold]{state['run_phase']}[/bold] "
            f"({state['run_timestamp'][:19] if state['run_timestamp'] else '-'})"
        )

    asyncio.run(_status())


@optimize.command("init-start")
@click.option("--tcp/--no-tcp", default=True, help="Include TCP tuning")
@click.option(
    "--kill", "-k", multiple=True,
    help="Service names to kill at boot (e.g. mastiff, cfg_server)",
)
@click.option("--deploy", is_flag=True, help="Deploy to router (otherwise just preview)")
def optimize_init_start(tcp: bool, kill: tuple[str, ...], deploy: bool):
    """Build and optionally deploy init-start optimization script."""
    from asusroutercontrol.merlin.jffs import build_init_start, deploy_init_start
    from asusroutercontrol.ssh import RouterSSH

    content = build_init_start(
        tcp_tuning=tcp,
        kill_services=list(kill) if kill else None,
    )

    if not deploy:
        console.print("[bold]Preview:[/bold]")
        from rich.syntax import Syntax
        console.print(Syntax(content, "bash", theme="monokai"))
        console.print("[dim]Add --deploy to write to router.[/dim]")
        return

    async def _deploy():
        async with RouterSSH() as ssh:
            ok = await deploy_init_start(ssh, content)
            if ok:
                console.print("[green]init-start deployed to router.[/green]")
            else:
                console.print("[red]Failed to deploy init-start.[/red]")

    asyncio.run(_deploy())


if __name__ == "__main__":
    cli()
