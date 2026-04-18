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
3. Set **SSH Port** to **1313** (non-default port reduces automated scan exposure)
4. Set **Allow Password Login** to **Yes**
5. Set **Allow SSH Access** to **LAN only** (recommended)
6. Click **Apply**

Without SSH, the menubar app shows device presence but not per-client tx/rx rates.

#### SSH Security

**Port Selection:**
- Default port is **1313** (non-standard port reduces exposure to automated scans)
- Standard port **22** can be used if needed — set `SSH_PORT=22` in `.env`

**Host Key Trust Modes:**
- `tofu_confirm` (default): Prompt on first connection, then pin key
- `strict`: Require pre-pinned fingerprint, reject unknown keys
- `tofu_auto`: Auto-accept and pin on first connection (automation only)

**Host Key Pinning Workflow:**
```bash
# Verify current host key
asusrouter ssh trust verify

# Pin/update host key after visual fingerprint verification
asusrouter ssh trust rotate --yes

# Show all pinned keys
asusrouter ssh trust show

# Revoke a pinned key
asusrouter ssh trust revoke
```

**Recommended Hardening:**
1. **Router-side:**
   - Disable SSH access from WAN (LAN-only)
   - Use non-default port (1313 or similar)
   - Consider disabling password auth after setting up key-based auth
2. **Client-side:**
   - Use `SSH_TRUST_MODE=strict` in production with pre-pinned fingerprint
   - Optionally restrict ciphers/MACs via `SSH_ENCRYPTION_ALGS` and `SSH_MAC_ALGS` (see `.env.example`)

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
