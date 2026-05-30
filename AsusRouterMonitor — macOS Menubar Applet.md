# AsusRouterMonitor — macOS Menubar Applet
## Problem
The ASUSRouterControl platform currently only has a CLI interface and a headless launchd scheduler. There's no persistent visual indicator of router health, and interacting with the platform requires opening a terminal. A macOS menubar applet would provide at-a-glance status and one-click access to common actions.

Additional Research suggestions: Router management and monitoring applications typically feature intuitive UIs focused on real-time dashboards, device discovery, performance metrics, and alerts to streamline network oversight.
Key Applications
PRTG Network Monitor offers a sensor-based UI with customizable maps, graphs for bandwidth/traffic, and health sensors for CPU, memory, and errors—ideal for visualizing router status at a glance. ManageEngine OpManager provides auto-discovery workflows, interface utilization charts, WAN link latency maps, and report generators for trends like packet errors and throughput. Checkmk delivers protocol-agnostic monitoring (SNMP, etc.) via dynamic dashboards showing traffic anomalies, uptime, and security threats across multi-vendor routers.
Common UI Patterns
Dashboards centralize widgets for metrics like bandwidth usage, latency, CPU load, and temperature, often with drill-down graphs and heatmaps. Topology maps and device lists enable quick navigation, with color-coded status (green/yellow/red) for availability and alerts via email/SMS. Reports and historical trends use tables/charts for capacity planning, error rates, and SLA compliance.
Structuring Your App
Prioritize a responsive dashboard with modular panels (e.g., traffic overview, hardware health, alerts) using frameworks like React for interactivity.
Incorporate auto-discovery flows and customizable views, mimicking PRTG’s sensor trees or OpManager’s hop-by-hop WAN visuals for intuitive hierarchy. Add proactive elements like threshold-based notifications and exportable reports to match enterprise needs.
## Current State
* **Scheduler** (`scheduler.py`): `MonitorScheduler` class runs 4 async loops (speed tests, probes, device polls, pruning) via `asyncio.gather`. Currently launched by a launchd plist (`com.asusroutercontrol.scheduler.plist`) pointing to `asusrouter scheduler start`.
* **DataStore** (`datastore.py`): Async SQLite at `~/.asusroutercontrol/router.db` — 7 tables (devices, sessions, traffic, speed_tests, latency, system, wifi).
* **Probes** (`probes.py`): Latency (ping 3 targets), system (CPU/RAM/temp/conntrack), WiFi (per-band client count + RSSI + noise). **Known bug**: `cat /proc/dmu/temperature` returns binary data (byte `0xf8`) that crashes asyncssh's UTF-8 decode, killing the SSH connection and all subsequent probes in that cycle.
* **Reporting** (`reporting.py`): `generate_report()` returns a structured dict with health score, bandwidth, latency, devices, WiFi, system, anomalies, recommendations.
* **CLI** (`cli.py`): 23 commands including `speedtest`, `report`, `scheduler start|install|uninstall|status`, `reboot`, `wifi on|off`, `devices`, `status`.
* **Config** (`config.py`): Dataclass loaded from env vars / `.env`.
* **Venv**: `.venv` at project root, Python 3.14, editable install.
## Proposed Changes
### Phase 1: Fix Temperature Probe Bug
In `probes.py` `probe_system()`, the temperature command `cat /proc/dmu/temperature` returns binary output on the RT-AC68U. This crashes asyncssh when it tries to decode the output as UTF-8, killing the entire SSH connection.
**Fix**: Pipe through `tr -cd '[:print:]\n'` to strip non-printable bytes before the output reaches asyncssh. Also add a targeted try/except around just the temperature block so a failure there can't affect subsequent probes.
### Phase 2: Menubar App Core — `menubar.py`
New file: `src/asusroutercontrol/menubar.py`
**Library**: `rumps` — lightweight Python macOS statusbar library. Uses PyObjC internally, supports `@rumps.timer()` for periodic callbacks and `@rumps.clicked()` for menu actions. Works with venv (confirmed compatible, unlike virtualenv).
**Threading model**:
* rumps runs on the **main thread** (AppKit event loop) — required by macOS
* `MonitorScheduler` runs in a **daemon thread** via `threading.Thread(target=lambda: asyncio.run(scheduler.run()), daemon=True)`
* A `rumps.Timer` (every 60s) queries the DataStore from a thread to refresh the menu with latest data
* One-shot actions (speed test, report) dispatch to a background thread, then post a `rumps.notification()` on completion
**Menu structure**:
```warp-runnable-command
📡 92/100                          <- title = icon + health score
─────────────────────────────
  RT-AC68U | Merlin 386.14_2       <- router model (static on startup)
  Uptime: 4d 12h                   <- from latest system snapshot
  CPU: 12.3%  RAM: 45.2%  87°C    <- from latest system snapshot
─────────────────────────────
  ↓ 312.5 Mbps  ↑ 36.8 Mbps       <- last speed test
  Ping: 8.2 ms  Loss: 0.0%        <- last latency probe (gateway)
─────────────────────────────
  Devices: 8 connected             <- from device count
  WiFi 2.4G: 3 clients (ch 6)     <- from latest wifi snapshot
  WiFi 5G: 5 clients (ch 48)      <- from latest wifi snapshot
─────────────────────────────
  ▶ Run Speed Test                 <- triggers speedtest in bg thread
  📊 Generate Report               <- triggers report, saves + notifies
  🔄 Reboot Router...              <- confirmation alert, then reboot
─────────────────────────────
  Scheduler: ● Running             <- green dot if scheduler thread alive
  Open Log File                    <- opens scheduler.log in Console.app
─────────────────────────────
  Quit AsusRouterMonitor
```
**Notifications** (via `rumps.notification()`):
* Speed test complete (with results)
* New unknown device detected
* Speed drop >30% below plan
* Temperature >85°C
* Packet loss >5%
### Phase 3: CLI Entry Point + Dependencies
* Add `rumps` to `pyproject.toml` optional deps: `menubar = ["rumps"]`
* Add CLI command: `asusrouter menubar` to launch the app
* Add `menubar` entry to `[project.scripts]` as `asusroutermonitor = "asusroutercontrol.menubar:main"` for direct invocation
### Phase 4: launchd Plist for Auto-Start + Crash Recovery
New plist: `com.asusroutermonitor.plist`
* `RunAtLoad: true` — starts on login
* `KeepAlive: true` — restarts on crash
* Replaces (or coexists with) the existing scheduler plist since the menubar app embeds the scheduler
* CLI commands: `asusrouter menubar install|uninstall|status`
### Phase 5: Lint + Smoke Test
* `ruff check` passes
* App launches, shows in menubar, displays status
* Scheduler thread starts and runs probes
* Speed test action triggers and shows notification
* Quit cleanly stops scheduler and exits
