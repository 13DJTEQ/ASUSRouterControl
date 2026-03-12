# ASUSRouterControl User Guide

CLI management and analysis tool for the ASUS RT-AC68U router.

## Installation

```bash
# Clone and install (editable mode)
cd /path/to/ASUSRouterControl
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Verify: `asusrouter --version`

## Initial Setup

### 1. Store credentials in macOS Keychain

```bash
asusrouter setup
```

Prompts for router username (default: `admin`) and password. Credentials are stored securely under the Keychain service `com.asusroutercontrol.*`.

### 2. Configure environment

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Description |
|---|---|---|
| `ROUTER_HOST` | `router.asus.com` | Router hostname or IP |
| `ROUTER_PORT` | `80` | HTTP port |
| `USE_SSL` | `false` | Enable HTTPS (`true`/`false`) |
| `POLLING_INTERVAL` | `60` | Monitor poll interval (seconds) |
| `DATA_DIR` | `~/.asusroutercontrol` | SQLite DB and data storage |
| `SOUNDSHIELD_EXPORT_PATH` | `~/.asusroutercontrol/soundshield_network.json` | SoundShield JSON export path |

Secrets are **never** stored in `.env` — only in Keychain.

## CLI Commands

### `asusrouter setup`
Interactive credential storage. Saves username and password to macOS Keychain.

### `asusrouter status`
Displays router system info: model, firmware version, uptime, CPU/RAM usage, WAN status, IP, gateway, DNS.

### `asusrouter devices [--json]`
Lists all connected devices with hostname, IP, MAC, connection type (wired/2.4GHz/5GHz), and signal strength (RSSI). Use `--json` for machine-readable output.

### `asusrouter monitor [-i SECONDS]`
Continuous polling loop. Connects to the router, records device presence and traffic snapshots to SQLite (`~/.asusroutercontrol/router.db`), and exports SoundShield JSON on each cycle. Prints new device alerts in real time.

- Default interval: 60 seconds (override with `-i`)
- Stop with `Ctrl+C`

### `asusrouter traffic [-h HOURS]`
Summarizes bandwidth usage over the specified window (default 24h): total download/upload, average and peak rates, and any detected anomalies (spikes relative to baseline).

Requires prior `monitor` data.

### `asusrouter history [MAC]`
- **No argument**: lists all known devices with first/last seen timestamps and known-device flag
- **With MAC**: shows up to 20 session records for that specific device (IP, connection type, RSSI per observation)

### `asusrouter reboot`
Sends a reboot command to the router. Requires confirmation (`--yes` to skip prompt).

### `asusrouter wifi on|off [2.4|5|all]`
Toggles WiFi radios. Defaults to `all` bands.

Examples:
```bash
asusrouter wifi off 5       # Disable 5GHz only
asusrouter wifi on all      # Enable both radios
```

### `asusrouter ports`
Displays all port forwarding rules: name, protocol, source port, destination IP/port, and enabled status.

### `asusrouter security`
Runs a security posture check:
- **Firmware status**: current / outdated / vulnerable
- **Port forwarding audit**: counts active rules, flags risky ports (21, 23, 3389, etc.)
- **Recommendations**: actionable items if issues are found

## Data Storage

- **SQLite database**: `~/.asusroutercontrol/router.db`
  - `devices` — known device registry (MAC, hostname, first/last seen)
  - `device_sessions` — per-poll device snapshots (IP, connection, RSSI)
  - `traffic_snapshots` — bandwidth samples (rx/tx bytes and rates)
- **SoundShield export**: `~/.asusroutercontrol/soundshield_network.json` (updated each poll cycle)

## Credential Management

| Operation | Method |
|---|---|
| Store | `asusrouter setup` or `keyring.set_password("com.asusroutercontrol.router.username", "default", "admin")` |
| Retrieve | Keychain first, falls back to env vars `ROUTER_USERNAME` / `ROUTER_PASSWORD` |
| Delete | `keyring.delete_password("com.asusroutercontrol.router.username", "default")` |

Keychain items can also be viewed/managed in **Keychain Access.app** under `com.asusroutercontrol.*`.

## Troubleshooting

- **"Credentials not configured"**: Run `asusrouter setup` to store credentials.
- **Connection refused**: Verify `ROUTER_HOST` and `ROUTER_PORT` in `.env`. Ensure the router's web UI is accessible from this machine.
- **No traffic/history data**: Run `asusrouter monitor` first to populate the database.
- **SSL errors**: Set `USE_SSL=true` and `ROUTER_PORT=8443` if your router uses HTTPS.
