# ASUSRouterControl

Management and analysis tool for ASUS RT-AC68U routers running stock AsusWRT or AsusWRT-Merlin firmware.

## Setup

```bash
pip install -e .
asusrouter setup
```

The `setup` command stores router credentials securely in macOS Keychain.

## Usage

```bash
asusrouter status     # Router system info
asusrouter devices    # Connected devices
asusrouter monitor    # Continuous monitoring (Phase 2)
```

## Architecture

- **Firmware-agnostic**: Backend abstraction supports Merlin now, FreshTomato later
- **Secure credentials**: macOS Keychain via `keyring` — no plaintext secrets
- **SoundShield integration**: JSON export for network-aware audio device discovery
