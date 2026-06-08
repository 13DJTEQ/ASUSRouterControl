#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".githooks/pre-push" ]]; then
  echo "Missing hook file: .githooks/pre-push"
  exit 1
fi

chmod +x ".githooks/pre-push" "scripts/pre-push-check.sh"
git config core.hooksPath .githooks

echo "Installed git hooks path: $(git config --get core.hooksPath)"
echo "Pre-push hook entrypoint: .githooks/pre-push"
