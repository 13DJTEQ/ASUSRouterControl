# Changelog

## 0.2.0 — 2026-09-15

Hotspot remediation across backends, CLI ownership, config, and model hygiene.

### Added
- `AsusWrtBackend` with `flavor=stock|merlin` and capability gates (`jffs`, `entware`, `ssh_probes`)
- Factory aliases: `ROUTER_BACKEND=stock` / `asuswrt` / `merlin`
- Config-driven ISP plan speeds: `PLAN_DOWNLOAD_MBPS` / `PLAN_UPLOAD_MBPS` (defaults 300/35)
- Stable `ClientLoad.health` tokens: `ok` | `warn` | `critical` (+ `health_to_emoji()` for UI)
- `BackendDeferredError` when FreshTomato is selected

### Changed
- Menubar reboot uses `create_backend` (no direct Merlin construction)
- Scheduler/analysis persist health tokens instead of emoji
- Analyzer/optimizer/reporting/menubar read plan speeds from config
- Prefer `utcnow()` from `_time` over `datetime.utcnow()`

### Removed
- Duplicate CLI monoliths `cli.py` and `_cli_legacy.py` (package `cli/` is the sole entrypoint)

### Deferred
- FreshTomato remains an in-tree stub; selecting it hard-fails until a later experimental pass
