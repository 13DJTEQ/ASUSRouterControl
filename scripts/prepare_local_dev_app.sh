#!/usr/bin/env bash
# Build ASUSRouterControl DEV.app on the machine where it will be tested.
# The DEV launcher embeds absolute checkout paths, so this must run on macOS
# in the clone you will open — it cannot be produced on Linux CI agents.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  cat >&2 <<'EOF'
ERROR: ASUSRouterControl DEV.app is a macOS menubar bundle (AppKit / PyObjC).

This environment cannot produce a runnable app for your Mac.
On your Mac, from a clone of this repo (not from ~):

  cd ~
  git clone https://github.com/13DJTEQ/ASUSRouterControl.git   # skip if already cloned
  cd ASUSRouterControl
  git fetch origin
  git checkout cursor/phase1-docs-agent-cleanup-451c
  git pull --ff-only
  make local-dev-app
  open "testbuilds/ASUSRouterControl DEV.app"
EOF
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "[local-dev-app] Creating venv + installing package (dev+menubar)"
  make setup
fi

echo "[local-dev-app] Ensuring editable install with menubar extras"
# pyinstaller is optional but preferred so the DEV fallback runtime is self-contained
if command -v uv >/dev/null 2>&1; then
  uv pip install -e '.[dev,menubar]' pyinstaller
else
  .venv/bin/python -m pip install -e '.[dev,menubar]' pyinstaller
fi

echo "[local-dev-app] Building DEV app bundle"
make build-dev-app

DEV_APP="${PROJECT_ROOT}/testbuilds/ASUSRouterControl DEV.app"
if [[ ! -d "${DEV_APP}" ]]; then
  echo "Expected DEV app missing: ${DEV_APP}" >&2
  exit 1
fi

echo
echo "Built: ${DEV_APP}"
echo "Launch:  open \"${DEV_APP}\""
echo "Verify:  make verify-dev-app"
echo "Runtime: ASUSROUTERCONTROL_RUNTIME_ENV=dev (isolated from production data)"
