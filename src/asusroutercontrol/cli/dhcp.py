"""DHCP reservation management commands."""

from __future__ import annotations

import asyncio

import click
from rich.table import Table

from asusroutercontrol.cli import (
    async_command,
    console,
    get_datastore,
    run_with_backend,
)
from asusroutercontrol.config import load_config
from asusroutercontrol.dhcp_profiles import (
    BUILTIN_PROFILES,
    get_profile_field,
    install_user_profiles,
    load_dhcp_profiles,
)

__all__ = ["dhcp_group"]


def _normalize_mac(mac: str | None) -> str | None:
    """Normalize a MAC address to lowercase colon-separated format."""
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


def _render_dhcp_apply_result(result) -> None:
    """Render the result of a DHCP reservation operation."""
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


def _render_device_row(device) -> str:
    """Render a single device row for display."""
    return (
        f"{device.mac} ip={device.ip or '-'} host={device.hostname or '-'} "
        f"conn={device.connection.value} online={device.is_online}"
    )


async def _collect_device_match_rows(
    target_mac: str,
    target_hostname: str | None,
) -> tuple[list[str], list[str]]:
    """Collect device match rows for a given MAC and hostname."""
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

    return await run_with_backend(_collect)


def _print_profile_device_match_summary(
    profile_label: str,
    target_mac: str,
    target_hostname: str | None,
) -> None:
    """Print device match summary for a profile."""
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


def _profile_target(
    profile_key: str,
    *,
    mac: str | None,
    hostname: str | None,
) -> tuple[str, str]:
    """Get normalized MAC and hostname for a profile."""
    target_mac = _normalize_mac(mac or get_profile_field(profile_key, "mac"))
    target_hostname = hostname or get_profile_field(profile_key, "hostname")
    return target_mac, target_hostname


def _run_profile_reservation(
    *,
    profile_key: str,
    ip: str,
    mac: str | None,
    hostname: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Execute a profile-based reservation."""
    from asusroutercontrol.dhcp_reservations import upsert_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac, target_hostname = _profile_target(
        profile_key,
        mac=mac,
        hostname=hostname,
    )
    profile_label = get_profile_field(profile_key, "label")

    if not dry_run:
        _print_profile_device_match_summary(profile_label, target_mac, target_hostname)
        if not yes and not click.confirm(
            f"Apply {profile_label} reservation {target_mac} -> {ip}?"
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    async def _reserve():
        store = await get_datastore()
        try:
            async with RouterSSH() as ssh:
                return await upsert_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    ip=ip,
                    hostname=target_hostname,
                    dry_run=dry_run,
                    triggered_by=get_profile_field(profile_key, "triggered_by_reserve"),
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
    """Execute a profile-based unreservation."""
    from asusroutercontrol.dhcp_reservations import remove_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac, target_hostname = _profile_target(
        profile_key,
        mac=mac,
        hostname=None,
    )
    profile_label = get_profile_field(profile_key, "label")

    if not dry_run:
        _print_profile_device_match_summary(profile_label, target_mac, target_hostname)
        if not yes and not click.confirm(
            f"Remove {profile_label} reservation for {target_mac}?"
        ):
            console.print("[dim]Cancelled.[/dim]")
            return

    async def _remove():
        store = await get_datastore()
        try:
            async with RouterSSH() as ssh:
                return await remove_reservation(
                    ssh=ssh,
                    store=store,
                    mac=target_mac,
                    dry_run=dry_run,
                    triggered_by=get_profile_field(profile_key, "triggered_by_unreserve"),
                )
        finally:
            await store.close()

    result = asyncio.run(_remove())
    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group("dhcp")
def dhcp_group():
    """Manage DHCP reservations."""


@dhcp_group.command("show")
@async_command
async def dhcp_show():
    """Show parsed DHCP reservations and raw NVRAM keys."""
    from asusroutercontrol.dhcp_reservations import get_reservations, read_dhcp_nvram
    from asusroutercontrol.ssh import RouterSSH

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


@dhcp_group.command("health")
@async_command
async def dhcp_health():
    """Assert required MAC→IP reservation mappings in one check."""
    from asusroutercontrol.dhcp_reservations import get_reservations
    from asusroutercontrol.ssh import RouterSSH

    required_profiles = (
        "macpro_primary",
        "denon_second_port",
        "macpro_lan2",
        "macpro_lan1",
    )

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
        label = get_profile_field(profile_key, "label")
        mac = get_profile_field(profile_key, "mac").lower()
        expected_ip = get_profile_field(profile_key, "default_ip")
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
@async_command
async def dhcp_set(mac: str, ip: str, hostname: str | None, dry_run: bool, yes: bool):
    """Create or update a DHCP reservation."""
    from asusroutercontrol.dhcp_reservations import upsert_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac = _normalize_mac(mac)
    if not dry_run and not yes:
        if not click.confirm(f"Apply DHCP reservation {target_mac} -> {ip}?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    store = await get_datastore()
    try:
        async with RouterSSH() as ssh:
            result = await upsert_reservation(
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
@async_command
async def dhcp_remove(mac: str, dry_run: bool, yes: bool):
    """Remove a DHCP reservation by MAC."""
    from asusroutercontrol.dhcp_reservations import remove_reservation
    from asusroutercontrol.ssh import RouterSSH

    target_mac = _normalize_mac(mac)
    if not dry_run and not yes:
        if not click.confirm(f"Remove DHCP reservation for {target_mac}?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    store = await get_datastore()
    try:
        async with RouterSSH() as ssh:
            result = await remove_reservation(
                ssh=ssh,
                store=store,
                mac=target_mac,
                dry_run=dry_run,
                triggered_by="dhcp:remove",
            )
    finally:
        await store.close()

    _render_dhcp_apply_result(result)
    if not result.success:
        raise click.ClickException(result.message)


# Profile-specific reservation commands
@dhcp_group.command("reserve-macpro")
@click.option(
    "--ip",
    default=BUILTIN_PROFILES["macpro_primary"].default_ip,
    show_default=True,
    help="Reserved IP for MacPro12Core.",
)
@click.option(
    "--mac",
    default=BUILTIN_PROFILES["macpro_primary"].mac,
    show_default=True,
    help="MAC override for Mac Pro profile.",
)
@click.option(
    "--hostname",
    default=BUILTIN_PROFILES["macpro_primary"].hostname,
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
    default=BUILTIN_PROFILES["denon_second_port"].default_ip,
    show_default=True,
    help="Reserved IP for Denon second ethernet endpoint.",
)
@click.option(
    "--mac",
    default=BUILTIN_PROFILES["denon_second_port"].mac,
    show_default=True,
    help="MAC override for Denon second-port profile.",
)
@click.option(
    "--hostname",
    default=BUILTIN_PROFILES["denon_second_port"].hostname,
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
    default=BUILTIN_PROFILES["denon_second_port"].mac,
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


# ---------------------------------------------------------------------------
# DHCP profile subgroup (device shortcut TOML management)
# Restored from archived Claude variant; adapted to V2 dhcp_profiles API.
# ---------------------------------------------------------------------------

@dhcp_group.group("profile")
def dhcp_profile_group():
    """Manage DHCP reservation profiles (device shortcuts)."""


@dhcp_profile_group.command("list")
def dhcp_profile_list():
    """List all available DHCP reservation profiles."""
    cfg = load_config()
    profiles = load_dhcp_profiles(cfg.data_dir)
    if not profiles:
        console.print(
            "[yellow]No profiles found. Run 'asusrouter dhcp profile install' "
            "to create a profiles file.[/yellow]"
        )
        return

    table = Table(
        title="DHCP Reservation Profiles",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Key", style="bold")
    table.add_column("Label")
    table.add_column("MAC")
    table.add_column("Default IP")
    table.add_column("Hostname")
    for key, profile in sorted(profiles.items()):
        table.add_row(
            key,
            profile.label,
            profile.mac,
            profile.default_ip,
            profile.hostname,
        )
    console.print(table)


@dhcp_profile_group.command("show")
@click.argument("key")
def dhcp_profile_show(key: str):
    """Show details for a single profile by KEY."""
    cfg = load_config()
    profile = load_dhcp_profiles(cfg.data_dir).get(key)
    if not profile:
        raise click.ClickException(
            f"Profile not found: {key!r}. Run 'asusrouter dhcp profile list'."
        )
    console.print(f"[bold]Profile:[/bold] {profile.key}")
    console.print(f"  Label:      {profile.label}")
    console.print(f"  MAC:        {profile.mac}")
    console.print(f"  Default IP: {profile.default_ip}")
    console.print(f"  Hostname:   {profile.hostname}")


@dhcp_profile_group.command("install")
@click.option(
    "--overwrite",
    is_flag=True,
    help="Overwrite existing user profiles file.",
)
def dhcp_profile_install(overwrite: bool):
    """Install the example profiles file to the user config directory."""
    cfg = load_config()
    path = install_user_profiles(cfg.data_dir, overwrite=overwrite)
    console.print(f"[green]Profiles file installed:[/green] {path}")
    console.print(
        "Edit this file to customise your device shortcuts, "
        "then run 'asusrouter dhcp profile list'."
    )
