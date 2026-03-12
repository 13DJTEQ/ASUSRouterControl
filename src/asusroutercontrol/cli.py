"""CLI interface for ASUSRouterControl."""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import get_router_credentials, store_credential

console = Console()


def _get_backend():
    """Create and return a configured MerlinBackend instance."""
    from asusroutercontrol.backends.merlin import MerlinBackend

    cfg = load_config()
    username, password = get_router_credentials()
    if not username or not password:
        console.print(
            "[red]Router credentials not configured.[/red] Run: [bold]asusrouter setup[/bold]"
        )
        sys.exit(1)
    return MerlinBackend(
        hostname=cfg.router_host,
        username=username,
        password=password,
        use_ssl=cfg.use_ssl,
        port=cfg.router_port,
    )


async def _run_with_backend(coro_factory):
    """Connect, run coroutine, disconnect."""
    backend = _get_backend()
    try:
        await backend.connect()
        return await coro_factory(backend)
    finally:
        await backend.disconnect()


@click.group()
@click.version_option(package_name="asusroutercontrol")
def cli():
    """ASUSRouterControl — manage your ASUS router."""


@cli.command()
def setup():
    """Store router credentials in macOS Keychain."""
    console.print("[bold]ASUSRouterControl Setup[/bold]\n")

    username = click.prompt("Router username", default="admin")
    password = click.prompt("Router password", hide_input=True)

    ok_user = store_credential("router.username", username)
    ok_pass = store_credential("router.password", password)

    if ok_user and ok_pass:
        console.print("\n[green]Credentials stored in macOS Keychain.[/green]")
        console.print("Config file: copy .env.example to .env and adjust ROUTER_HOST if needed.")
    else:
        console.print("\n[red]Failed to store credentials.[/red]")


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
            hours = sys_info.uptime_seconds // 3600
            table.add_row("Uptime", f"{hours}h {(sys_info.uptime_seconds % 3600) // 60}m")
        if sys_info.cpu_usage_percent is not None:
            table.add_row("CPU", f"{sys_info.cpu_usage_percent:.1f}%")
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


if __name__ == "__main__":
    cli()
