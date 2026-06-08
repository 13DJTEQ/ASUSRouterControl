#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[pre-push-check] Running canonical validation..."
bash "$ROOT_DIR/scripts/validate.sh"
echo "[pre-push-check] Validation passed."
