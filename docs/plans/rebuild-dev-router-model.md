# Rebuild DEV.app after router model display fix (Mac)

Dave: the menubar was hardcoding **RT-AC68U**. It now reads the live model
from the router (HTTP firmware payload and/or `nvram get productid`). You do
**not** need to delete `~/.asusroutercontrol.dev/router.db` for this bug —
that DB never stored the model string.

## Mac steps (no zsh `#` comments)

```bash
pkill -f 'ASUSRouterControl' || true
pkill -f 'asusroutercontrol.menubar' || true
cd ~/ASUSRouterControl
git fetch origin
git checkout cursor/customer-router-connect-451c
git pull origin cursor/customer-router-connect-451c
make setup
make build-dev-app
make verify-dev-app
open "testbuilds/ASUSRouterControl DEV.app"
```

In the menubar menu:

1. Confirm **About:** shows `DEV v…` with a fresh `BUILD_STAMP` (timestamp-gitsha), not a stale `0.1.0` / `1`.
2. Choose **Connect Router…**, authenticate (Bitwarden default).
3. Confirm the Router line shows **RT-BE92U** (or your live `productid`), not RT-AC68U.

Optional only if Connect still points at an old host/identity in `.env`:

```bash
ls -la ~/.asusroutercontrol.dev/
cat ~/.asusroutercontrol.dev/.env
```

Do not wipe `router.db` unless you also want a clean telemetry slate; model
display no longer depends on it.
