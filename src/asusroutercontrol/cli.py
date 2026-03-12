"""CLI interface for ASUSRouterControl."""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import get_router_credentials, store_credential
from asusroutercontrol.datastore import DataStore

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
            "[red]rumps not installed.[/red] Run: pip install 'asusroutercontrol[menubar]'"
        )
        return
    menubar_main()


@menubar.command("install")
def menubar_install():
    """Install launchd plist — auto-start on login, restart on crash."""
    import shutil
    import subprocess
    from pathlib import Path

    cfg = load_config()
    cfg.ensure_dirs()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.asusroutermonitor.plist"

    exe = shutil.which("asusroutermonitor")
    if not exe:
        # Fall back to running via the asusrouter CLI
        exe = shutil.which("asusrouter")
        if not exe:
            console.print("[red]Cannot find 'asusroutermonitor' or 'asusrouter' on PATH.[/red]")
            return
        program_args = f"""    <array>
        <string>{exe}</string>
        <string>menubar</string>
        <string>launch</string>
    </array>"""
    else:
        program_args = f"""    <array>
        <string>{exe}</string>
    </array>"""

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.asusroutermonitor</string>
    <key>ProgramArguments</key>
{program_args}
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
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
    plist_path.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    console.print(f"[green]Installed and loaded:[/green] {plist_path}")
    console.print("Menubar app will auto-start on login and restart on crash.")


@menubar.command("uninstall")
def menubar_uninstall():
    """Remove menubar launchd plist."""
    import subprocess
    from pathlib import Path

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
    if not plist_path.exists():
        console.print("[dim]Not installed.[/dim]")
        return
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    plist_path.unlink()
    console.print(f"[green]Uninstalled:[/green] {plist_path}")


@menubar.command("build")
def menubar_build():
    """Rebuild package and restart the menubar app with latest code."""
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    venv_pip = project_root / ".venv" / "bin" / "pip"

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
    was_loaded = plist_path.exists() and subprocess.run(
        ["launchctl", "list", "com.asusroutermonitor"],
        capture_output=True,
    ).returncode == 0

    # 1. Unload launchd agent (stops running app)
    if was_loaded:
        console.print("[dim]Stopping menubar app...[/dim]")
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)

    # 2. Reinstall package (picks up new deps / entry points)
    console.print(f"[dim]Reinstalling from {project_root}...[/dim]")
    result = subprocess.run(
        [str(venv_pip) if venv_pip.exists() else sys.executable, "-m", "pip",
         "install", "-e", str(project_root)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]pip install failed:[/red]\n{result.stderr.strip()}")
        return
    console.print("[green]Package rebuilt.[/green]")

    # 3. Reload launchd agent (starts fresh app)
    if was_loaded:
        subprocess.run(["launchctl", "load", str(plist_path)], check=False)
        console.print("[green]Menubar app restarted.[/green]")
    elif plist_path.exists():
        console.print("[yellow]Plist exists but was not loaded. Load with:[/yellow]")
        console.print(f"  launchctl load {plist_path}")
    else:
        console.print("[yellow]No launchd plist installed. Run:[/yellow] asusrouter menubar install")


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
    "--source", "-s", default=None,
    type=click.Choice(["ookla", "cloudflare", "http_download"]),
    help="Run a single provider only",
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
            pt.add_column("Server")
            for r in providers:
                if r.error:
                    pt.add_row(
                        r.provider, f"[red]{r.error}[/red]",
                        "", "", "", "",
                    )
                else:
                    dl = f"{r.download_bps / 1e6:.1f}" if r.download_bps else "—"
                    ul = f"{r.upload_bps / 1e6:.1f}" if r.upload_bps else "—"
                    pg = f"{r.ping_ms:.1f}" if r.ping_ms is not None else "—"
                    jt = f"{r.jitter_ms:.1f}" if r.jitter_ms is not None else "—"
                    pt.add_row(
                        r.provider, f"{dl} Mbps", f"{ul} Mbps",
                        f"{pg} ms", f"{jt} ms",
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


if __name__ == "__main__":
    cli()
