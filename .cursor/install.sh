#!/usr/bin/env bash
# Cloud Agent environment bootstrap for ASUSRouterControl.
# Idempotent: safe to re-run against cached/partial state.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# uv provides the pinned CPython 3.11 (see .python-version) even when the base
# image only ships a newer interpreter. Install it once if missing.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Create the Python 3.11 virtualenv if it does not already exist.
if [ ! -x ".venv/bin/python" ]; then
  uv venv --python 3.11
fi

# Editable install with dev extras (ruff + pytest), matching CI.
# The macOS-only "menubar" extra (pyobjc) is intentionally excluded on Linux.
uv pip install -e ".[dev]"
