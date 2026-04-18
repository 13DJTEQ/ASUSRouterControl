"""Analysis CLI commands (dashboard, reporting).

Restored from the archived ASUSRouterControl Claude variant and adapted to the
cli/ subpackage pattern. The original lived in `cli.py` (monolith); this
extraction advances the in-progress CLI modularization.
"""

from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from asusroutercontrol.cli import console, get_datastore

__all__ = ["dashboard"]


def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.1f}{suffix}"


@click.command()
@click.option(
    "--hours",
    "-H",
    default=24,
    show_default=True,
    type=click.IntRange(min=1),
    help="Shared lookback window in hours.",
)
@click.option(
    "--clients",
    default=10,
    show_default=True,
    type=click.IntRange(min=1),
    help="Max client rows to include.",
)
@click.option(
    "--timeline-points",
    default=6,
    show_default=True,
    type=click.IntRange(min=1),
    help="Recent speed-test anchors to include in timeline context.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    help="Export dashboard JSON to file",
)
def dashboard(
    hours: int,
    clients: int,
    timeline_points: int,
    as_json: bool,
    export_path: str | None,
) -> None:
    """Show combined ISP and client speed/load dashboard."""
    from asusroutercontrol.analysis.dashboard import build_isp_client_dashboard

    async def _dashboard() -> dict:
        store = await get_datastore()
        try:
            return await build_isp_client_dashboard(
                store,
                hours=hours,
                clients=clients,
                timeline_points=timeline_points,
            )
        finally:
            await store.close()

    data = asyncio.run(_dashboard())

    if as_json:
        console.print(_json.dumps(data, indent=2, default=str))
    else:
        window = data.get("window", {})
        isp = data.get("isp_performance", {})
        client_panel = data.get("client_speed_load", {})
        timeline = data.get("isp_client_timeline", [])
        quality_counts = isp.get("quality_counts", {})

        summary = Table(show_header=False)
        summary.add_column("Metric", style="bold cyan")
        summary.add_column("Value")
        summary.add_row("Window", f"{window.get('hours', '?')}h")
        summary.add_row("Tests", str(isp.get("tests_total", 0)))
        summary.add_row(
            "Quality",
            (
                f"ok={quality_counts.get('ok', 0)} "
                f"suspect={quality_counts.get('suspect', 0)} "
                f"error={quality_counts.get('error', 0)}"
            ),
        )
        summary.add_row("Avg Download", _fmt(isp.get("avg_download_mbps"), " Mbps"))
        summary.add_row("Avg Upload", _fmt(isp.get("avg_upload_mbps"), " Mbps"))
        summary.add_row("Avg Ping", _fmt(isp.get("avg_ping_ms"), " ms"))
        summary.add_row("Avg Jitter", _fmt(isp.get("avg_jitter_ms"), " ms"))
        summary.add_row("Avg Confidence", _fmt(isp.get("avg_confidence"), "/100"))
        latest = isp.get("latest_test") or {}
        if latest:
            summary.add_row("Latest Test", str(latest.get("timestamp") or "—"))
        console.print(Panel(summary, title="ISP Performance", border_style="cyan"))

        client_rows = client_panel.get("top_clients", [])
        if client_rows:
            table = Table(title=f"Client Speed/Load ({len(client_rows)} shown)")
            table.add_column("Host")
            table.add_column("MAC", style="dim")
            table.add_column("Band")
            table.add_column("Avg Load")
            table.add_column("Peak Load")
            table.add_column("Signal")
            table.add_column("Samples")
            table.add_column("Latest Seen")
            for row in client_rows:
                table.add_row(
                    str(row.get("hostname") or "—"),
                    str(row.get("mac") or "—"),
                    str(row.get("band") or "—"),
                    _fmt(row.get("avg_load_pct"), "%"),
                    _fmt(row.get("peak_load_pct"), "%"),
                    "yes" if row.get("has_signal") else "no",
                    (
                        f"{row.get('signal_samples', 0)}/"
                        f"{row.get('sample_count', 0)}"
                    ),
                    str(row.get("timestamp") or "—"),
                )
            console.print(table)
        else:
            console.print(
                Panel(
                    "[dim]No client samples in lookback window.[/dim]",
                    title="Client Speed/Load",
                    border_style="yellow",
                )
            )

        if timeline:
            t_table = Table(title=f"ISP ↔ Client Context Timeline ({len(timeline)})")
            t_table.add_column("Speed Test Time")
            t_table.add_column("ISP (DL/UL)")
            t_table.add_column("Quality")
            t_table.add_column("Top Client")
            t_table.add_column("Client Avg Load")
            t_table.add_column("Signal Clients")
            for point in timeline:
                top_client = point.get("top_client") or {}
                top_label = top_client.get("hostname") or top_client.get("mac") or "—"
                t_table.add_row(
                    str(point.get("speed_test_timestamp") or "—"),
                    (
                        f"{_fmt(point.get('download_mbps'), ' Mbps')} / "
                        f"{_fmt(point.get('upload_mbps'), ' Mbps')}"
                    ),
                    str(point.get("quality") or "—"),
                    str(top_label),
                    _fmt(top_client.get("avg_load_pct"), "%"),
                    (
                        f"{point.get('clients_with_signal', 0)}/"
                        f"{point.get('clients_seen', 0)}"
                    ),
                )
            console.print(t_table)
        else:
            console.print(
                "[dim]No speed tests in lookback window for timeline context.[/dim]"
            )

    if export_path:
        export_file = Path(export_path)
        export_file.parent.mkdir(parents=True, exist_ok=True)
        export_file.write_text(_json.dumps(data, indent=2, default=str))
        console.print(f"[green]Dashboard exported to {export_path}[/green]")
