# Local DEV app build (Mac)

`ASUSRouterControl DEV.app` is a macOS menubar bundle. The launcher embeds **absolute paths** to this checkout’s `.venv`, so the app must be built on the same Mac you will use for testing. Linux/cloud agents cannot produce a runnable copy.

## Build on your Mac (recommended)

From **any** directory (including `~`), run the bootstrap script. It moves a broken/non-git `~/ASUSRouterControl` aside, clones, checks out the branch, builds, and opens the DEV app:

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/phase1-docs-agent-cleanup-451c/scripts/bootstrap_local_dev_app_mac.sh | bash
```

Or download then run:

```bash
curl -fsSL -o /tmp/bootstrap_local_dev_app_mac.sh \
  https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/phase1-docs-agent-cleanup-451c/scripts/bootstrap_local_dev_app_mac.sh
bash /tmp/bootstrap_local_dev_app_mac.sh
```

Optional env overrides: `ASUSROUTERCONTROL_DIR`, `ASUSROUTERCONTROL_BRANCH`, `ASUSROUTERCONTROL_OPEN_APP=0`.

Optional full smoke (rebuild + relaunch + runtime checks):

```bash
make verify-dev-app
```

## Notes

- Uses isolated DEV runtime data (`ASUSROUTERCONTROL_RUNTIME_ENV=dev`).
- Red DEV icon distinguishes it from production.
- Requires Python 3.11+, Xcode CLT, and menubar extras (PyObjC).
- Primary HITL target after launch: **RT-BE92U** stock AsusWRT.
