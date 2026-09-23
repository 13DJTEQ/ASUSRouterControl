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
    --exclude 'LIVE.log' \
    --exclude 'LIVE.status' \
    "${SRC}/" "${DEST}/"
else
  # Portable fallback — keep live progress + incident outputs
  find "${DEST}" -mindepth 1 -maxdepth 1 \
    ! -name 'hermes-incident-*' \
    ! -name 'LIVE.log' \
    ! -name 'LIVE.status' \
    -exec rm -rf {} + 2>/dev/null || true
  cp -R "${SRC}/." "${DEST}/"
fi

chmod +x \
  "${DEST}/hermes-desktop-archive.sh" \
  "${DEST}/install-to-desktop.sh" \
  "${DEST}/bootstrap-desktop-collector.sh" \
  "${DEST}/hermes-desktop-monitor.sh" \
  "${DEST}/hermes-desktop-repair.sh" 2>/dev/null || true

# Convenience copy of monitor on Desktop root for quick curl-less check-ins
cp "${DEST}/hermes-desktop-monitor.sh" "${HOME}/Desktop/hermes-desktop-monitor.sh" 2>/dev/null || true
chmod +x "${HOME}/Desktop/hermes-desktop-monitor.sh" 2>/dev/null || true

printf '[install] hermes-desktop-ops → %s\n' "${DEST}"
printf '[install] next: /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh\n'
printf '[install] monitor: /bin/bash ~/Desktop/hermes-desktop-monitor.sh\n'
printf '[install] repair (dry-run): /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh\n'
printf '%s\n' "${DEST}"
