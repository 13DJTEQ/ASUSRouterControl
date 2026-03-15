# ASUSRouterControl User Guide

CLI management and analysis tool for the ASUS RT-AC68U running AsusWRT-Merlin.

---

## Installation

```bash
# Clone and install (editable mode)
cd /path/to/ASUSRouterControl
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional extras:

```bash
pip install -e ".[menubar]"   # macOS menubar applet (requires rumps)
pip install -e ".[dev]"       # lint + test tooling
```

Verify: `asusrouter --version`

---

## Initial Setup

### 1. Store credentials in macOS Keychain

```bash
asusrouter setup
```

Prompts for router username (default: `admin`) and password. Credentials are stored securely under Keychain service `com.asusroutercontrol.*`. Secrets are **never** written to disk or `.env`.

### 2. Configure environment

Copy `.env.example` to `.env` and adjust as needed:

**Router connection**

| Variable | Default | Description |
|---|---|---|
| `ROUTER_HOST` | `router.asus.com` | Router hostname or IP |
| `ROUTER_PORT` | `80` | HTTP port |
| `USE_SSL` | `false` | Enable HTTPS (`true`/`false`) |
| `POLLING_INTERVAL` | `60` | `monitor` poll interval (seconds) |
| `DATA_DIR` | `~/.asusroutercontrol` | SQLite DB and data storage root |
| `SOUNDSHIELD_EXPORT_PATH` | `~/.asusroutercontrol/soundshield_network.json` | SoundShield JSON export |

**SSH (Merlin features)**

| Variable | Default | Description |
|---|---|---|
| `SSH_PORT` | `1313` | SSH port on the router |
| `SSH_TRUST_MODE` | `tofu_confirm` | `strict` / `tofu_confirm` / `tofu_auto` |
| `SSH_HOST_KEY_FINGERPRINT` | _(none)_ | Expected SHA256 fingerprint for strict mode |
| `SSH_KNOWN_HOSTS_PATH` | _(none)_ | Path to a custom known_hosts file |

**Scheduler**

| Variable | Default | Description |
|---|---|---|
| `SPEEDTEST_TIMES` | `hourly (all local hours, 0-23)` | Hours (local) to run scheduled speed tests |
| `PEAK_START` | `18` | Start of peak-hour window |
| `PEAK_END` | `23` | End of peak-hour window |
| `PROBE_INTERVAL` | `1800` | Config probe interval (seconds, default 30 min) |
| `POLL_INTERVAL` | `300` | Scheduler router poll interval (seconds, default 5 min) |

---

## CLI Commands

### `asusrouter setup`
Interactive credential storage. Saves username and password to macOS Keychain.

---

### `asusrouter status`
Router system info: model, firmware version, uptime, CPU (with temperature), RAM, WAN status, WAN IP, gateway, DNS.

---

### `asusrouter devices [--json]`
Lists all connected devices: hostname, IP, MAC, connection type (wired / 2.4GHz / 5GHz), signal strength (RSSI).

`--json` outputs machine-readable JSON.

---

### `asusrouter dhcp ...`
Manage DHCP reservations safely with dry-run-first behavior.

| Command | Description |
|---|---|
| `asusrouter dhcp show` | Show parsed DHCP reservations and reservation state (`dhcp_static_x`) |
| `asusrouter dhcp health` | Assert required core reservation mappings (Mac Pro Wi-Fi/LAN + Denon) |
| `asusrouter dhcp set --mac MAC --ip IP [--hostname NAME] [--apply]` | Create/update reservation (defaults to dry run; `--apply` writes) |
| `asusrouter dhcp remove --mac MAC [--apply]` | Remove reservation by MAC (defaults to dry run) |
| `asusrouter dhcp reserve-macpro [--ip IP] [--mac MAC] [--hostname NAME] [--apply]` | Reserve Mac Pro using profile defaults with optional overrides |
| `asusrouter dhcp reserve-denon-second-port [--ip IP] [--mac MAC] [--hostname NAME] [--apply]` | Optional reserve flow for second ethernet endpoint (Denon150) |
| `asusrouter dhcp unreserve-denon-second-port [--mac MAC] [--apply]` | Optional remove flow for second ethernet endpoint reservation |

Examples:

```bash
asusrouter dhcp set --mac 74:1B:B2:F1:C4:31 --ip 192.168.1.240 --hostname MacPro12Core
asusrouter dhcp reserve-macpro --apply --yes
asusrouter dhcp reserve-denon-second-port --dry-run
```

---

### `asusrouter monitor [-i SECONDS]`
Continuous polling loop. Each cycle:
- Records device presence and traffic snapshots to SQLite (`router.db`)
- Exports SoundShield JSON
- Prints new-device alerts in real time

Default interval: 60 s. Stop with `Ctrl+C`.

```bash
asusrouter monitor -i 30
```

---

### `asusrouter traffic [-h HOURS]`
Bandwidth summary over a window (default: 24 h): total download/upload, average and peak rates, anomaly spikes.

Requires prior `monitor` data.

---

### `asusrouter history [MAC]`
- **No argument**: all known devices with first/last seen timestamps and known-device flag.
- **With MAC**: up to 20 session records for that device (IP, connection type, RSSI per observation).

---

### `asusrouter reboot`
Sends a reboot command. Prompts for confirmation; use `--yes` to skip.

---

### `asusrouter wifi on|off [2.4|5|all]`
Toggles WiFi radios. Defaults to `all` bands.

```bash
asusrouter wifi off 5       # Disable 5 GHz only
asusrouter wifi on all      # Enable both radios
```

---

### `asusrouter ports`
All port forwarding rules: name, protocol, source port, destination IP/port, enabled status.

---

### `asusrouter security`
Security posture check:
- **Firmware status**: current / outdated / vulnerable / unknown
- **Port forwarding audit**: active rule count, flags risky ports (21, 23, 3389, …)
- **Recommendations**: actionable items if issues are found

---
### `asusrouter live-dhcp-auth [--mac MAC] [-s SECONDS] [--poll SECONDS]`
Live troubleshooting mode for phone reconnect issues.
- Tails Wi-Fi auth/disassoc and DHCP handshake events from `/tmp/syslog.log`
- Prints events as they happen while you reconnect a phone
- Ends with a per-MAC diagnosis (e.g., no OFFER, no ACK, disassociation reason)

```bash
asusrouter live-dhcp-auth --mac 80:B9:89:D9:FD:04 -s 120
```

---

### `asusrouter speedtest [--no-store] [-s PROVIDER]`
Multi-source speed test (Ookla, Cloudflare, HTTP download). Displays per-provider breakdown and a composite result with confidence score and peak-hour flag.

Results are saved to the database by default; use `--no-store` to skip.

```bash
asusrouter speedtest                    # All providers
asusrouter speedtest -s ookla           # Ookla only
asusrouter speedtest -s cloudflare --no-store
```

---

### `asusrouter trends [-d DAYS] [--json]`
Performance trends over a window (default: 30 d). Shows avg, trend direction (↑/↓/→), weekly rate of change, and R² for:
- Download / Upload (Mbps)
- Latency (ms)
- RAM usage (%)
- 2.4 GHz / 5 GHz RSSI (dBm)
- Packet loss events

Requires accumulated speed test and scheduler data.

---

### `asusrouter analyze [-d DAYS] [--json]`
Full analysis combining:
- **Recommendations** (HIGH / MEDIUM / LOW / INFO) with action steps
- **ISP SLA score** (0–100) and % of tests meeting your plan speed
- **Suggested router settings changes** with rationale

---

### `asusrouter report [-d DAYS] [--json] [--export PATH]`
Network health report for the specified window (default: 7 d).

```bash
asusrouter report --export ~/Desktop/weekly.json
```

---

### `asusrouter config-snapshot [--show]`
- **Default**: takes a new NVRAM config snapshot via SSH and saves it to the database. Reports any diff from the previous snapshot.
- `--show`: displays the latest stored snapshot (all key/value pairs and diff summary).

---

### `asusrouter config-history [-d DAYS] [--json]`
Config change timeline (default: 90 d) with correlated performance impact: download delta (%) and latency delta (ms) after each change.

---

## Merlin: JFFS Scripts (`asusrouter scripts`)

Requires SSH access. JFFS must be enabled: **Router UI → Administration → System → Enable JFFS custom scripts**.

| Command | Description |
|---|---|
| `asusrouter scripts list` | List scripts in `/jffs/scripts/` with executable status, size, and hook |
| `asusrouter scripts hooks` | Show all available Merlin hook names and their triggers |
| `asusrouter scripts show NAME` | Print script contents with syntax highlighting |
| `asusrouter scripts enable NAME` | `chmod +x` the script |
| `asusrouter scripts disable NAME` | `chmod -x` the script (file kept) |
| `asusrouter scripts delete NAME` | Delete script (prompts confirmation) |

---

## Merlin: Entware (`asusrouter entware`)

Requires SSH access and USB storage mounted on the router.

| Command | Description |
|---|---|
| `asusrouter entware status` | Installation status, USB mount, path, package count, architecture |
| `asusrouter entware setup` | Install Entware on the router |
| `asusrouter entware update` | Update `opkg` package feeds |
| `asusrouter entware list` | List installed packages |
| `asusrouter entware search QUERY` | Search available packages (top 25 results) |
| `asusrouter entware add NAME` | Install a package |
| `asusrouter entware remove NAME` | Remove a package (prompts confirmation) |

---

## SSH Trust Management (`asusrouter ssh trust`)

Host key pinning to protect against MITM on the router SSH connection.

| Command | Description |
|---|---|
| `asusrouter ssh trust show` | Show all pinned host keys and fingerprints |
| `asusrouter ssh trust verify [--host] [--port]` | Verify trust state; reports unknown or mismatched keys |
| `asusrouter ssh trust rotate [--host] [--port] [--yes]` | Replace pinned key with the currently presented one |
| `asusrouter ssh trust revoke [--host] [--port] [--yes]` | Remove a pinned host entry |

**Trust modes** (set via `SSH_TRUST_MODE` in `.env`):
- `strict` — connection refused if key is unknown or doesn't match `SSH_HOST_KEY_FINGERPRINT`
- `tofu_confirm` *(default)* — prompts on first connection, then pins the key
- `tofu_auto` — pins on first connection silently

---

## Menubar Applet (`asusrouter menubar`)

Requires `rumps`: `pip install 'asusroutercontrol[menubar]'`

| Command | Description |
|---|---|
| `asusrouter menubar launch` | Launch the menubar applet in the foreground |
| `asusrouter menubar install` | Install launchd plist — auto-starts on login, restarts on crash |
| `asusrouter menubar uninstall` | Remove the launchd plist |
| `asusrouter menubar status` | Check if the applet is running |
| `asusrouter menubar build` | Reinstall the package and restart the running applet |

Logs: `~/.asusroutercontrol/menubar.out.log` / `menubar.err.log`

---

## Background Scheduler (`asusrouter scheduler`)

Persistent process combining continuous polling, scheduled speed tests, and config probes.

| Command | Description |
|---|---|
| `asusrouter scheduler start` | Run scheduler in foreground (Ctrl+C to stop) |
| `asusrouter scheduler install` | Install launchd plist — auto-starts on login, restarts on crash |
| `asusrouter scheduler uninstall` | Remove the launchd plist |
| `asusrouter scheduler status` | Check if the scheduler is running |

Default schedule (override in `.env`):
- Speed tests hourly (every local clock hour, 00:00-23:00)
- Router polls every 5 min (`POLL_INTERVAL`)
- Config probes every 30 min (`PROBE_INTERVAL`)

Log: `~/.asusroutercontrol/scheduler.log`

---

## Data Storage

All data lives in `~/.asusroutercontrol/` (configurable via `DATA_DIR`).

**SQLite: `router.db`**

| Table | Contents |
|---|---|
| `devices` | Known device registry (MAC, hostname, first/last seen, is_known) |
| `device_sessions` | Per-poll snapshots (IP, connection type, RSSI) |
| `traffic_snapshots` | Bandwidth samples (rx/tx bytes and rates) |
| `speed_tests` | Speed test results (download, upload, ping, jitter, confidence) |
| `config_snapshots` | NVRAM snapshots with diff summaries |
| `config_events` | Config change log |

**SoundShield export:** `soundshield_network.json` — updated each monitor/scheduler cycle for network-aware audio device discovery.

---

## Credential Management

| Operation | Method |
|---|---|
| Store | `asusrouter setup` |
| Retrieve | Keychain first; falls back to env vars `ROUTER_USERNAME` / `ROUTER_PASSWORD` |
| View / delete | **Keychain Access.app** → search `com.asusroutercontrol` |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Router credentials not configured` | Run `asusrouter setup` |
| Connection refused | Check `ROUTER_HOST` and `ROUTER_PORT`; confirm the router web UI is reachable |
| No traffic / history data | Run `asusrouter monitor` or `asusrouter scheduler start` first |
| SSL errors | Set `USE_SSL=true` and `ROUTER_PORT=8443` |
| SSH: unknown host key | Run `asusrouter ssh trust rotate` after verifying router identity |
| JFFS commands fail | Enable JFFS: Router UI → Administration → System → Enable JFFS custom scripts |
| Entware commands fail | Ensure USB is mounted and Entware is installed (`asusrouter entware setup`) |
| Menubar won't launch | Install extra: `pip install 'asusroutercontrol[menubar]'` |
| No trend / analyze data | Requires days of scheduler data; run `asusrouter scheduler install` |
