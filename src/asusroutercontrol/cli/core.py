"""CLI interface for ASUSRouterControl."""

from __future__ import annotations

import asyncio
import re
import sys

import click
from rich.console import Console
from rich.table import Table

from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import (
    get_router_credentials,
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

_dhcp_profiles_cache: dict | None = None

def _get_dhcp_profiles() -> dict:
    """Lazy-load DHCP profiles from TOML (user file or packaged defaults)."""
    global _dhcp_profiles_cache
    if _dhcp_profiles_cache is None:
        from asusroutercontrol.dhcp_profiles import load_dhcp_profiles

        cfg = load_config()
        _dhcp_profiles_cache = load_dhcp_profiles(cfg.data_dir)
    return _dhcp_profiles_cache


def _get_profiles_for_display() -> dict:
    profiles = _get_dhcp_profiles()
    if profiles:
        return profiles
    return {
        key: type(
            "ProfileView",
            (),
            {
                "key": key,
                "label": value["label"],
                "mac": value["mac"],
                "default_ip": value["default_ip"],
                "hostname": value["hostname"],
            },
        )()
        for key, value in _DHCP_RESERVATION_PROFILES.items()
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
    """Fetch a field from a named DHCP profile with static fallback."""
    from asusroutercontrol.dhcp_profiles import DhcpProfile

    profile = _get_dhcp_profiles().get(profile_key)
    if isinstance(profile, DhcpProfile):
        attr_map = {
            "mac": "mac",
            "hostname": "hostname",
            "default_ip": "default_ip",
            "label": "label",
            "triggered_by_reserve": "triggered_by_reserve",
            "triggered_by_unreserve": "triggered_by_unreserve",
        }
        attr = attr_map.get(field)
        if attr is None:
            raise click.ClickException(f"Unknown profile field: {field!r}")
        return getattr(profile, attr)

    if profile is not None:
        value = profile.get(field)  # type: ignore[union-attr]
        if value is not None:
            return value

    static_profile = _DHCP_RESERVATION_PROFILES.get(profile_key)
    if not static_profile:
        raise click.ClickException(f"Unknown DHCP reservation profile: {profile_key!r}")
    value = static_profile.get(field)
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

