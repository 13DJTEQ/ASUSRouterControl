# Hermes Desktop Ops

Portable collector for Hermes Desktop incidents on macOS Apple Silicon.
Lives on the **Desktop**. Separate from ASUSRouterControl app code.

## Install and run on a Mac

Paste this **as one line** in Terminal. Do not paste comment lines that contain parentheses — zsh can treat them as glob qualifiers.

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh
```

That will:

1. Download `hermes-desktop-ops` onto `~/Desktop/hermes-desktop-ops`
2. Run the collector
3. Write evidence onto the Desktop
4. Stream progress into `LIVE.log` / `hermes-collect.live.log`
5. Any step longer than **20 seconds** prints live heartbeats every 5s

### Live progress

Collector and bootstrap write:

- `~/Desktop/hermes-desktop-ops/LIVE.log`
- `~/Desktop/hermes-collect.live.log`
- `~/Desktop/hermes-desktop-ops/LIVE.status` — current `phase=` / `elapsed_s=`

Override thresholds if needed:

```bash
export HERMES_PROGRESS_AFTER_SECS=20
export HERMES_PROGRESS_EVERY_SECS=5
```

### Outputs on Desktop

- `hermes-incident-<host>-<ts>.zip`
- `hermes-incident-<host>-<ts>.report.md` — paste into the MOE plan
- `hermes-incident-<host>-<ts>.report.json` — machine-readable plan feed
- `hermes-incident-<host>-<ts>.manifest.json`
- `hermes-desktop-ops/LIVE.log` — timestamped collector progress
- `hermes-collect.live.log` — same live log at Desktop root
- `hermes-desktop-ops/LIVE.status` — `phase=...` key=value check-in file

### Monitor while the collector runs

Open a **second** Terminal window. Paste as one line.

One-shot check-in:

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/hermes-desktop-monitor.sh -o ~/Desktop/hermes-desktop-monitor.sh && chmod +x ~/Desktop/hermes-desktop-monitor.sh && /bin/bash ~/Desktop/hermes-desktop-monitor.sh --once
```

Follow the live log:

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/hermes-desktop-monitor.sh -o ~/Desktop/hermes-desktop-monitor.sh && chmod +x ~/Desktop/hermes-desktop-monitor.sh && /bin/bash ~/Desktop/hermes-desktop-monitor.sh --tail
```

If ops tools are already installed:

```bash
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --once
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --tail
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-monitor.sh --watch
```

### Re-run collector later

```bash
/bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh
```

### Install tools only — no collect

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
- `hermes` on PATH when possible — filesystem evidence still collected if missing
- `zip` preferred — `tar.gz` fallback
