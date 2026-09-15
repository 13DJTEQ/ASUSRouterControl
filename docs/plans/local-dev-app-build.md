# Local DEV app build (Mac)

`ASUSRouterControl DEV.app` is a macOS menubar bundle. The launcher embeds **absolute paths** to this checkout’s `.venv`, so the app must be built on the same Mac you will use for testing. Linux/cloud agents cannot produce a runnable copy.

## Build on your Mac

Run these from a clone of the repo (not from `~`). First-time setup:

```bash
cd ~
git clone https://github.com/13DJTEQ/ASUSRouterControl.git
cd ASUSRouterControl
git fetch origin
git checkout cursor/phase1-docs-agent-cleanup-451c
git pull --ff-only
make local-dev-app
open "testbuilds/ASUSRouterControl DEV.app"
```

If you already have a clone:

```bash
cd /path/to/ASUSRouterControl   # must contain Makefile and .git
git fetch origin
git checkout cursor/phase1-docs-agent-cleanup-451c
git pull --ff-only
make local-dev-app
open "testbuilds/ASUSRouterControl DEV.app"
```

Optional full smoke (rebuild + relaunch + runtime checks):

```bash
make verify-dev-app
```

## Notes

- Uses isolated DEV runtime data (`ASUSROUTERCONTROL_RUNTIME_ENV=dev`).
- Red DEV icon distinguishes it from production.
- Requires Python 3.11+, Xcode CLT, and menubar extras (PyObjC).
- Primary HITL target after launch: **RT-BE92U** stock AsusWRT.
