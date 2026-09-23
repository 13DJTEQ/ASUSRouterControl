#!/usr/bin/env bash
# bootstrap-desktop-collector.sh
# One-shot: download hermes-desktop-ops onto ~/Desktop and run the collector.
# Safe for zsh/bash. No prior checkout required.
#
# Mac one-liner (paste as ONE line — do not paste the comment lines):
#   curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && ~/Desktop/bootstrap-desktop-collector.sh
set -euo pipefail

REPO="${HERMES_OPS_REPO:-13DJTEQ/ASUSRouterControl}"
REF="${HERMES_OPS_REF:-cursor/hermes-desktop-ops-cfe4}"
DESKTOP="${HOME}/Desktop"
DEST="${DESKTOP}/hermes-desktop-ops"
TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"
RUN_COLLECT=1
SKIP_COLLECT=0

usage() {
  cat <<'EOF'
Usage: bootstrap-desktop-collector.sh [--skip-collect] [--ref BRANCH] [--repo OWNER/REPO]

Downloads hermes-desktop-ops onto ~/Desktop/hermes-desktop-ops and runs
hermes-desktop-archive.sh unless --skip-collect is set.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-collect) SKIP_COLLECT=1; RUN_COLLECT=0; shift ;;
    --ref) REF="$2"; TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"; shift 2 ;;
    --repo) REPO="$2"; TARBALL_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

note() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

mkdir -p "${DESKTOP}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-ops-bootstrap.XXXXXX")"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

note "fetching ${TARBALL_URL}"
if ! curl -fsSL "${TARBALL_URL}" -o "${TMP}/src.tgz"; then
  die "failed to download branch tarball. Check network / that branch ${REF} exists on ${REPO}."
fi

note "extracting"
tar -xzf "${TMP}/src.tgz" -C "${TMP}"
SRC_DIR="$(find "${TMP}" -maxdepth 2 -type d -name 'hermes-desktop-ops' | head -n 1 || true)"
[[ -n "${SRC_DIR}" && -d "${SRC_DIR}" ]] || die "hermes-desktop-ops not found inside tarball"

note "installing to ${DEST}"
mkdir -p "${DEST}"
# Prefer rsync; fall back to cp
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '*.zip' \
    --exclude 'hermes-incident-*' \
    "${SRC_DIR}/" "${DEST}/"
else
  # Remove previous tool files but keep any incident outputs that landed inside DEST by mistake
  find "${DEST}" -mindepth 1 -maxdepth 1 ! -name 'hermes-incident-*' -exec rm -rf {} + 2>/dev/null || true
  cp -R "${SRC_DIR}/." "${DEST}/"
fi

chmod +x "${DEST}/hermes-desktop-archive.sh" \
  "${DEST}/install-to-desktop.sh" \
  "${DEST}/bootstrap-desktop-collector.sh" 2>/dev/null || true
[[ -f "${DEST}/hermes-desktop-repair.sh" ]] && chmod +x "${DEST}/hermes-desktop-repair.sh" || true
[[ -f "${DEST}/lib/build_report.py" ]] || die "missing lib/build_report.py after install"

# Also leave a launcher on Desktop for Finder double-click / Terminal drag
cp "${DEST}/bootstrap-desktop-collector.sh" "${DESKTOP}/bootstrap-desktop-collector.sh" 2>/dev/null || true
chmod +x "${DESKTOP}/bootstrap-desktop-collector.sh" 2>/dev/null || true

note "installed OK: ${DEST}"
ls -la "${DEST}"

if [[ "${RUN_COLLECT}" -eq 1 && "${SKIP_COLLECT}" -eq 0 ]]; then
  note "running collector"
  # Prefer bash explicitly so zsh users are fine
  /bin/bash "${DEST}/hermes-desktop-archive.sh"
  note "collector finished — check Desktop for hermes-incident-* files"
else
  note "skip-collect set; run: /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-archive.sh"
fi
