# ASUSRouterControl

Management and analysis tool for ASUS RT-AC68U routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in macOS Keychain.

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
- **Secure credentials**: macOS Keychain via `keyring` — no plaintext secrets
- **SoundShield integration**: JSON export for network-aware audio device discovery
