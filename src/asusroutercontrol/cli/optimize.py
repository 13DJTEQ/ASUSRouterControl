"""Router optimization commands: audit, suggest, apply, verify, rollout."""

from __future__ import annotations

import click
from rich.table import Table

from asusroutercontrol.cli import async_command, console, get_datastore

__all__ = ["optimize_group"]


@click.group("optimize")
def optimize_group():
    """Router optimization: audit, suggest, apply, verify."""


@optimize_group.command("audit")
@async_command
async def optimize_audit():
    """Run service, sysctl, and WiFi channel audit."""
    from asusroutercontrol.probes import probe_services, probe_sysctl, probe_wifi_channels
    from asusroutercontrol.ssh import RouterSSH

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
        console.print(f"[green]All {sysctl.total_count} sysctl values optimal.[/green]")

    # WiFi channels
    for survey in channels:
        if survey.best_channel:
            console.print(f"[yellow]{survey.band}GHz:[/yellow] {survey.best_reason}")
        elif survey.entries:
            console.print(
                f"[green]{survey.band}GHz:[/green] Current channel "
                f"{survey.current_channel} looks optimal"
            )
        else:
            console.print(f"[dim]{survey.band}GHz:[/dim] No channel survey data available")


@optimize_group.command("suggest")
@click.option("--days", "-d", default=30, help="Analysis window in days")
@async_command
async def optimize_suggest(days: int):
    """Show all optimization suggestions (NVRAM + probes)."""
    from asusroutercontrol.optimizer import generate_recommendations, suggest_settings

    store = await get_datastore()
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
                    s["key"],
                    s["current"],
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


@optimize_group.command("benchmark")
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
@async_command
async def optimize_benchmark(iterations: int, days: int, as_json: bool):
    """Run deterministic local benchmark for datastore query/write timings."""
    from asusroutercontrol.benchmark import run_datastore_benchmark

    store = await get_datastore()
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
    console.print(f"[dim]Window: {payload['days']}d | Iterations: {payload['iterations']}[/dim]")

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
        summary = ", ".join(f"{name}={sample_sizes[name]}" for name in sorted(sample_sizes))
        console.print(f"[dim]Sample sizes: {summary}[/dim]")


@optimize_group.command("apply")
@click.option("--key", "-k", help="Apply a single NVRAM key")
@click.option("--value", "-v", help="Value for --key")
@click.option("--clear", is_flag=True, help="Set --key to empty string.")
@click.option("--all-suggestions", is_flag=True, help="Apply all suggest_settings() results")
@click.option("--dry-run", is_flag=True, help="Show what would change without applying")
@async_command
async def optimize_apply(
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

    store = await get_datastore()
    try:
        async with RouterSSH() as ssh:
            has_single_target = key is not None and (value is not None or clear)
            if has_single_target:
                target_value = "" if clear else (value or "")
                result = await apply_nvram_setting(
                    ssh,
                    store,
                    key,
                    target_value,
                    dry_run=dry_run,
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
                    console.print(f"[yellow]Applying {len(suggestions)} changes...[/yellow]")
                results = await apply_optimization_batch(
                    ssh,
                    store,
                    suggestions,
                    dry_run=dry_run,
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


@optimize_group.command("verify")
@async_command
async def optimize_verify():
    """Verify Deep Dive findings execution status."""
    from asusroutercontrol.executor import verify_deep_dive_findings
    from asusroutercontrol.ssh import RouterSSH

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
            str(f["finding"]),
            f["title"],
            f["expected"],
            f"[{actual_style}]{f['actual']}[/{actual_style}]",
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


# --- Rollout subgroup ---


@optimize_group.group("rollout")
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
@async_command
async def optimize_rollout_plan(profile: str, no_disconnect: bool, allow_disruptive: bool):
    """Preview step order, impact, and policy gates for a rollout profile."""
    from asusroutercontrol.rollout import get_rollout_plan_rows

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
@async_command
async def optimize_rollout_run(
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

    store = await get_datastore()
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


@optimize_rollout.command("status")
@click.option("--profile", default="last-rollback", show_default=True)
@click.option("--days", default=30, show_default=True, type=click.IntRange(1, 365))
@async_command
async def optimize_rollout_status(profile: str, days: int):
    """Show last known rollout phase per step for a profile."""
    from asusroutercontrol.rollout import rollout_status

    store = await get_datastore()
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
            "green"
            if phase in {"pass", "dry_run_pass"}
            else "yellow"
            if phase.startswith("skip") or phase == "not_started"
            else "red"
            if "fail" in phase or "abort" in phase
            else "white"
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


@optimize_group.command("init-start")
@click.option("--tcp/--no-tcp", default=True, help="Include TCP tuning")
@click.option(
    "--kill",
    "-k",
    multiple=True,
    help="Service names to kill at boot (e.g. mastiff, cfg_server)",
)
@click.option("--deploy", is_flag=True, help="Deploy to router (otherwise just preview)")
@async_command
async def optimize_init_start(tcp: bool, kill: tuple[str, ...], deploy: bool):
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

    async with RouterSSH() as ssh:
        ok = await deploy_init_start(ssh, content)
        if ok:
            console.print("[green]init-start deployed to router.[/green]")
        else:
            console.print("[red]Failed to deploy init-start.[/red]")
