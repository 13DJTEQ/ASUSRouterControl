#!/usr/bin/env bash
# Bootstrap ASUSRouterControl DEV.app on this Mac.
# Safe to paste into Terminal from ~ (does not require an existing git clone).
set -euo pipefail

REPO_URL="${ASUSROUTERCONTROL_REPO_URL:-https://github.com/13DJTEQ/ASUSRouterControl.git}"
BRANCH="${ASUSROUTERCONTROL_BRANCH:-cursor/phase1-docs-agent-cleanup-451c}"
CLONE_DIR="${ASUSROUTERCONTROL_DIR:-$HOME/ASUSRouterControl}"
OPEN_APP="${ASUSROUTERCONTROL_OPEN_APP:-1}"

log() { printf '[local-dev-app] %s\n' "$*"; }
die() { printf '[local-dev-app] ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "This script must run on macOS (found $(uname -s))."
fi

command -v git >/dev/null 2>&1 || die "git is required. Install Xcode Command Line Tools: xcode-select --install"
command -v make >/dev/null 2>&1 || die "make is required. Install Xcode Command Line Tools: xcode-select --install"

# Prefer uv if present; otherwise make setup will attempt uv and fail clearly.
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found; installing via official installer"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck disable=SC1091
  [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv install failed; install from https://docs.astral.sh/uv/ and re-run"
fi

prepare_clone_dir() {
  if [[ ! -e "$CLONE_DIR" ]]; then
    return 0
  fi
  if [[ -d "$CLONE_DIR/.git" ]]; then
    log "Using existing git clone at $CLONE_DIR"
    return 0
  fi
  local backup="${CLONE_DIR}.bak-$(date +%Y%m%d-%H%M%S)"
  log "Found non-git directory at $CLONE_DIR — moving aside to $backup"
  mv "$CLONE_DIR" "$backup"
}

prepare_clone_dir

if [[ ! -d "$CLONE_DIR/.git" ]]; then
  log "Cloning $REPO_URL -> $CLONE_DIR"
  git clone "$REPO_URL" "$CLONE_DIR"
fi

cd "$CLONE_DIR"
log "Working directory: $(pwd)"

log "Fetching and checking out $BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

[[ -f Makefile ]] || die "Makefile missing in $CLONE_DIR — clone looks incomplete"
[[ -f scripts/prepare_local_dev_app.sh ]] || die "scripts/prepare_local_dev_app.sh missing on branch $BRANCH"

log "Building DEV app (make local-dev-app)"
make local-dev-app

DEV_APP="$CLONE_DIR/testbuilds/ASUSRouterControl DEV.app"
[[ -d "$DEV_APP" ]] || die "DEV app was not created at: $DEV_APP"

log "Built: $DEV_APP"
if [[ "$OPEN_APP" == "1" ]]; then
  log "Opening DEV app"
  open "$DEV_APP"
fi

cat <<EOF

Done.
  App:     $DEV_APP
  Repo:    $CLONE_DIR
  Branch:  $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)
  Verify:  cd "$CLONE_DIR" && make verify-dev-app
EOF
