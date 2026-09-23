#!/usr/bin/env bash
# install-to-desktop.sh — place hermes-desktop-ops on ~/Desktop for both Macs.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/Desktop/hermes-desktop-ops"

mkdir -p "${HOME}/Desktop"
mkdir -p "${DEST}"

# Copy ops drop onto Desktop (do not delete existing incident zips on Desktop).
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '*.zip' \
    --exclude 'hermes-incident-*' \
    "${SRC}/" "${DEST}/"
else
  # Portable fallback
  find "${DEST}" -mindepth 1 -maxdepth 1 ! -name 'hermes-incident-*' -exec rm -rf {} + 2>/dev/null || true
  cp -R "${SRC}/." "${DEST}/"
fi

chmod +x "${DEST}/hermes-desktop-archive.sh" "${DEST}/install-to-desktop.sh" 2>/dev/null || true
[[ -f "${DEST}/hermes-desktop-repair.sh" ]] && chmod +x "${DEST}/hermes-desktop-repair.sh" || true

printf '[install] hermes-desktop-ops → %s\n' "${DEST}"
printf '[install] next: cd ~/Desktop/hermes-desktop-ops && ./hermes-desktop-archive.sh\n'
printf '%s\n' "${DEST}"
