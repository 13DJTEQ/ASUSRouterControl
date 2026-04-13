# ASUSRouterControl

Management and analysis tool for ASUS RT-AC68U routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in macOS Keychain.
Set `ROUTER_BACKEND=merlin` (default) or `ROUTER_BACKEND=freshtomato` in `.env` to select firmware backend.

## Router Requirements

For full functionality, configure your ASUS router:

### Required: Web Admin Access
- Router must be accessible at `ROUTER_HOST` (default: `router.asus.com`)
- Admin credentials must match those stored via `asusrouter setup`

### Required for Client Traffic Telemetry: SSH
Enable SSH in router admin UI:
1. Navigate to **Administration → System**
2. Set **Enable SSH** to **Yes**
3. Set **SSH Port** to **22** (or configure `SSH_PORT` in `.env`)
4. Set **Allow Password Login** to **Yes**
5. Click **Apply**

Without SSH, the menubar app shows device presence but not per-client tx/rx rates.

### Configuration
Copy `.env.example` to `~/.asusroutercontrol/.env` and adjust as needed:
```bash
cp .env.example ~/.asusroutercontrol/.env
```

Key settings:
- `SSH_PORT=22` — SSH daemon port (default 22, some setups use 1313)
- `SSH_TRUST_MODE=tofu_confirm` — Host key verification mode
- `ROUTER_HOST=router.asus.com` — Router hostname or IP

## Developer validation

```bash
pip install -e ".[dev]"
bash scripts/validate.sh
```

The validation script runs lint (`ruff`), tests (`pytest`), and syntax checks (`compileall`).
If required dev tools are missing, it exits with an actionable install command.

## Usage

```bash
asusrouter status     # Router system info
asusrouter devices    # Connected devices
asusrouter dhcp show  # Current DHCP reservations
asusrouter dhcp health  # Assert required reservation mappings
asusrouter dhcp reserve-macpro --dry-run
asusrouter dhcp reserve-denon-second-port --dry-run
asusrouter monitor    # Continuous monitoring (Phase 2)
asusrouter live-dhcp-auth --mac AA:BB:CC:DD:EE:FF -s 120   # Live phone reconnect diagnosis
```

## Architecture

- **Firmware-agnostic**: Backend abstraction supports Merlin now, FreshTomato later
- **Backend selection**: `ROUTER_BACKEND` switches between Merlin and FreshTomato implementations
- **Secure credentials**: macOS Keychain via `keyring` — no plaintext secrets
- **SoundShield integration**: JSON export for network-aware audio device discovery
