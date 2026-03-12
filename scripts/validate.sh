#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

missing=()
python3 -c "import pytest" >/dev/null 2>&1 || missing+=("pytest")
python3 -c "import ruff" >/dev/null 2>&1 || missing+=("ruff")

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing required dev tools: ${missing[*]}"
  echo "Install with: pip install -e \".[dev]\""
  exit 1
fi

python3 -m ruff check src tests
python3 -m pytest
python3 -m compileall src tests
