# Hermes Desktop Ops (portable drop)

Separate from ASUSRouterControl. Copy this folder to **both Macs** and keep it on the local Desktop.

## Place on Desktop

From this folder (AirDrop / USB / git checkout):

```bash
chmod +x install-to-desktop.sh hermes-desktop-archive.sh
./install-to-desktop.sh
```

That installs to:

`~/Desktop/hermes-desktop-ops/`

## Collect incident evidence (run on each Mac)

```bash
cd ~/Desktop/hermes-desktop-ops
./hermes-desktop-archive.sh
```

### Outputs (all on Desktop)

| File | Purpose |
|------|---------|
| `~/Desktop/hermes-incident-<host>-<ts>.zip` | Full evidence bundle |
| `~/Desktop/hermes-incident-<host>-<ts>.report.md` | **Paste into MOE plan** (Findings from collectors) |
| `~/Desktop/hermes-incident-<host>-<ts>.report.json` | Machine-readable plan feed (`hermes-desktop-ops/report-v1`) |
| `~/Desktop/hermes-incident-<host>-<ts>.manifest.json` | Pointers to the above |

Secrets are redacted. Auth dump is fingerprint-only (no raw tokens).

## Pipe report into the plan

After both Macs have run the collector:

1. Open each `*.report.md` on Desktop.
2. Paste under **Findings from collectors** in the Hermes Desktop MOE RCA plan.
3. Merge `hypotheses_ranked` from each `*.report.json` into the expert table evidence columns.
4. Then run repair (when available): `./hermes-desktop-repair.sh` then `--apply`.

## Requirements

- macOS Apple Silicon (primary)
- `bash`, `python3`
- `hermes` on PATH when possible (filesystem evidence still collected if missing)
- `zip` preferred (`tar.gz` fallback)
