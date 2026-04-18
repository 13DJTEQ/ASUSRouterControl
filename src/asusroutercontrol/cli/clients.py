"""CLI commands for listing WiFi and LAN clients."""

from __future__ import annotations

import click
from rich.table import Table

from asusroutercontrol.cli import async_command, console, run_with_backend


@click.group(name="clients")
def clients_group():
    """List WiFi and LAN clients."""


@clients_group.command(name="wifi")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@async_command
async def wifi_clients(as_json: bool):
    """List WiFi clients (2.4GHz, 5GHz, 6GHz)."""

    async def _wifi(backend):
        with console.status("Fetching WiFi clients..."):
            clients = await backend.get_wifi_clients()

        if as_json:
            import json

            console.print(
                json.dumps([c.model_dump(mode="json") for c in clients], indent=2)
            )
            return

        table = Table(title=f"WiFi Clients ({len(clients)})")
        table.add_column("Hostname", style="bold")
        table.add_column("IP")
        table.add_column("MAC", style="dim")
        table.add_column("Band")
        table.add_column("RSSI", justify="right")

        for c in sorted(clients, key=lambda x: x.hostname or x.mac):
            rssi = f"{c.rssi} dBm" if c.rssi is not None else "-"
            table.add_row(
                c.hostname or "(unknown)",
                c.ip or "-",
                c.mac,
                c.band or "-",
                rssi,
            )

        console.print(table)

    await run_with_backend(_wifi)


@clients_group.command(name="lan")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@async_command
async def lan_clients(as_json: bool):
    """List wired LAN clients (Ethernet)."""

    async def _lan(backend):
        with console.status("Fetching LAN clients..."):
            clients = await backend.get_lan_clients()

        if as_json:
            import json

            console.print(
                json.dumps([c.model_dump(mode="json") for c in clients], indent=2)
            )
            return

        table = Table(title=f"LAN Clients ({len(clients)})")
        table.add_column("Hostname", style="bold")
        table.add_column("IP")
        table.add_column("MAC", style="dim")

        for c in sorted(clients, key=lambda x: x.hostname or x.mac):
            table.add_row(
                c.hostname or "(unknown)",
                c.ip or "-",
                c.mac,
            )

        console.print(table)

    await run_with_backend(_lan)


@clients_group.command(name="all")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@async_command
async def all_clients(as_json: bool):
    """List all clients (WiFi + LAN)."""

    async def _all(backend):
        with console.status("Fetching all clients..."):
            wifi = await backend.get_wifi_clients()
            lan = await backend.get_lan_clients()

        if as_json:
            import json

            data = {
                "wifi": [c.model_dump(mode="json") for c in wifi],
                "lan": [c.model_dump(mode="json") for c in lan],
            }
            console.print(json.dumps(data, indent=2))
            return

        console.print(f"[bold]WiFi Clients:[/bold] {len(wifi)}")
        console.print(f"[bold]LAN Clients:[/bold] {len(lan)}")
        console.print(f"[bold]Total:[/bold] {len(wifi) + len(lan)}")

    await run_with_backend(_all)
