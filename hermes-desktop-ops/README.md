# Hermes Desktop Ops

Portable collector for Hermes Desktop incidents on macOS Apple Silicon.
Lives on the **Desktop**. Separate from ASUSRouterControl app code.

## Install and run on a Mac (recommended)

Paste this **as one line** in Terminal (bash or zsh). Do not paste comment lines that contain parentheses.

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh
```

That will:

1. Download `hermes-desktop-ops` onto `~/Desktop/hermes-desktop-ops`
2. Run the collector
3. Write evidence onto the Desktop

### Outputs on Desktop

- `hermes-incident-<host>-<ts>.zip`
- `hermes-incident-<host>-<ts>.report.md` — paste into the MOE plan
- `hermes-incident-<host>-<ts>.report.json` — machine-readable plan feed
- `hermes-incident-<host>-<ts>.manifest.json`

### Re-run later

```bash
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh
```

### Install tools only (no collect)

```bash
/bin/bash ~/Desktop/bootstrap-desktop-collector.sh --skip-collect
```

## If you already have the repo checked out

```bash
cd /path/to/ASUSRouterControl/hermes-desktop-ops
/bin/bash ./install-to-desktop.sh
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh
```

## Pipe report into the plan

See `PLAN_PIPE.md`. Paste each Mac's `*.report.md` under **Findings from collectors**.

## Requirements

- macOS Apple Silicon preferred
- `curl`, `tar`, `python3`, `bash`
- `hermes` on PATH when possible (filesystem evidence still collected if missing)
- `zip` preferred (`tar.gz` fallback)
