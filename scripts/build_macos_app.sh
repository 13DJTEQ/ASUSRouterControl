#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/build_macos_app.sh --mode <dev|prod>

Modes:
  dev   Build ASUSRouterControl DEV.app into ./testbuilds with a red DEV icon.
  prod  Build a self-contained ASUSRouterControl.app and package it as ./dist/ASUSRouterControl.dmg.
EOF
}

MODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$MODE" != "dev" && "$MODE" != "prod" ]]; then
  echo "Missing or invalid --mode. Expected dev or prod." >&2
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_BUILDS_DIR="${PROJECT_ROOT}/testbuilds"
BUILD_ROOT="${PROJECT_ROOT}/build/macos-app"
STAGE_DIR="${BUILD_ROOT}/stage-dev"
PROD_BUILD_DIR="${BUILD_ROOT}/prod"
PROD_WORK_DIR="${PROD_BUILD_DIR}/pyinstaller-work"
PROD_SPEC_DIR="${PROD_BUILD_DIR}/spec"
PROD_DMG_STAGE_DIR="${PROD_BUILD_DIR}/dmg-stage"
PROD_DIST_DIR="${PROJECT_ROOT}/dist"
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "Missing virtualenv python at ${VENV_PY}. Run 'make setup' first." >&2
  exit 1
fi

build_dev_app() {
  local app_name="ASUSRouterControl DEV.app"
  local bundle_id="dev.mediawavetech.asusroutercontrol.dev"
  local display_name="ASUSRouterControl DEV"
  local icon_icns="${BUILD_ROOT}/Icon-Dev.icns"
  local dev_runtime_name="ASUSRouterControlDevRuntime"
  local dev_runtime_dir="${BUILD_ROOT}/dev-runtime"
  local dev_runtime_work_dir="${dev_runtime_dir}/pyinstaller-work"
  local dev_runtime_spec_dir="${dev_runtime_dir}/spec"
  local dev_runtime_entrypoint="${dev_runtime_dir}/dev_runtime_entrypoint.py"
  local dev_runtime_app="${dev_runtime_dir}/${dev_runtime_name}.app"
  local dev_runtime_exe="${dev_runtime_app}/Contents/MacOS/${dev_runtime_name}"
  local app_dir="${STAGE_DIR}/${app_name}"
  local contents_dir="${app_dir}/Contents"
  local macos_dir="${contents_dir}/MacOS"
  local resources_dir="${contents_dir}/Resources"
  local launcher="${macos_dir}/asusroutercontrol-launcher"
  local plist_path="${contents_dir}/Info.plist"
  local dest_app="${TEST_BUILDS_DIR}/${app_name}"

  rm -rf "${app_dir}"
  mkdir -p "${macos_dir}" "${resources_dir}"

  # Build a DEV-specific self-contained runtime when PyInstaller is available.
  # This keeps fallback launches aligned with current source behavior.
  if "${VENV_PY}" -c "import PyInstaller" >/dev/null 2>&1; then
    rm -rf "${dev_runtime_work_dir}" "${dev_runtime_spec_dir}" "${dev_runtime_app}"
    mkdir -p "${dev_runtime_dir}" "${dev_runtime_work_dir}" "${dev_runtime_spec_dir}"
    cat > "${dev_runtime_entrypoint}" <<'EOF'
from asusroutercontrol.menubar import main

if __name__ == "__main__":
    main()
EOF
    "${VENV_PY}" -m PyInstaller \
      --noconfirm \
      --clean \
      --windowed \
      --name "${dev_runtime_name}" \
      --hidden-import AppKit \
      --hidden-import Foundation \
      --distpath "${dev_runtime_dir}" \
      --workpath "${dev_runtime_work_dir}" \
      --specpath "${dev_runtime_spec_dir}" \
      --osx-bundle-identifier "dev.mediawavetech.asusroutercontrol.devruntime" \
      "${dev_runtime_entrypoint}"

    # Ensure inner runtime also declares LSUIElement so macOS treats it as a status-bar app.
    # Without this, the inner runtime fails scene activation when another LSUIElement app is running.
    if [[ -f "${dev_runtime_app}/Contents/Info.plist" ]]; then
      plutil -replace LSUIElement -bool true "${dev_runtime_app}/Contents/Info.plist"
    fi
  fi

  cat > "${launcher}" <<EOF
#!/usr/bin/env bash
PROJECT_ROOT="${PROJECT_ROOT}"
VENV_PY="\${PROJECT_ROOT}/.venv/bin/python"
DEV_RUNTIME_EXE="${dev_runtime_exe}"
SELF_CONTAINED_EXE="\${PROJECT_ROOT}/dist/ASUSRouterControl.app/Contents/MacOS/ASUSRouterControl"
SELF_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
LAUNCH_LOG="\${HOME}/.asusroutercontrol.dev/launcher.log"
mkdir -p "\${HOME}/.asusroutercontrol.dev" 2>/dev/null || true
_log() {
  printf '%s %s\n' "\$(date '+%Y-%m-%dT%H:%M:%S%z')" "\$*" >> "\${LAUNCH_LOG}" 2>/dev/null || true
}
_fail_alert() {
  local msg="\$1"
  _log "FAIL: \${msg}"
  /usr/bin/osascript -e "display alert \\"ASUSRouterControl DEV cannot start\\" message \\"\${msg}\\"" 2>/dev/null || true
}
export ASUSROUTERCONTROL_RUNTIME_ENV="dev"
# Absolute .app path so Restart can relaunch when not under launchd.
export ASUSROUTERCONTROL_APP_BUNDLE="\$(cd "\${SELF_DIR}/../.." && pwd)"
# Load project .env even when Finder launches the app with cwd=/
if [[ -f "\${PROJECT_ROOT}/.env" ]]; then
  export ASUSROUTERCONTROL_ENV_FILE="\${PROJECT_ROOT}/.env"
fi
export ASUSROUTERCONTROL_PROJECT_ROOT="\${PROJECT_ROOT}"
# Force DEV-scoped data dirs before python-dotenv loads a shared project .env
# that often pins production DATA_DIR=~/.asusroutercontrol (dotenv does not
# override pre-set variables).
export DATA_DIR="\${HOME}/.asusroutercontrol.dev"
export SOUNDSHIELD_EXPORT_PATH="\${HOME}/.asusroutercontrol.dev/soundshield_network.json"
_log "launch start bundle=\${ASUSROUTERCONTROL_APP_BUNDLE} env_file=\${ASUSROUTERCONTROL_ENV_FILE:-none}"
# Prefer source-tree runtime for dev builds so the process remains associated
# with the DEV app bundle identity in the menubar.
if [[ -x "\${VENV_PY}" ]]; then
  export PYTHONPATH="\${PROJECT_ROOT}/src:\${PYTHONPATH:-}"
  _log "trying venv: \${VENV_PY}"
  "\${VENV_PY}" -m asusroutercontrol.menubar >>"\${LAUNCH_LOG}" 2>&1
  venv_exit=\$?
  if [[ \${venv_exit} -eq 0 ]]; then
    exit 0
  fi
  _log "venv exit=\${venv_exit}"
fi
# Fallback to DEV-specific self-contained runtime built from current source.
if [[ -x "\${DEV_RUNTIME_EXE}" ]]; then
  _log "trying DEV runtime: \${DEV_RUNTIME_EXE}"
  "\${DEV_RUNTIME_EXE}" >>"\${LAUNCH_LOG}" 2>&1
  rt_exit=\$?
  if [[ \${rt_exit} -eq 0 ]]; then
    exit 0
  fi
  _log "DEV runtime exit=\${rt_exit}"
fi
# Final fallback to shared dist runtime.
if [[ -x "\${SELF_CONTAINED_EXE}" ]]; then
  _log "trying dist runtime: \${SELF_CONTAINED_EXE}"
  "\${SELF_CONTAINED_EXE}" >>"\${LAUNCH_LOG}" 2>&1
  dist_exit=\$?
  if [[ \${dist_exit} -eq 0 ]]; then
    exit 0
  fi
  _log "dist runtime exit=\${dist_exit}"
fi

_fail_alert "No usable runtime found (see ~/.asusroutercontrol.dev/launcher.log). Run make setup then make build-dev-app in the project folder."
exit 1
EOF
  chmod +x "${launcher}"

  "${VENV_PY}" "${SCRIPT_DIR}/generate_dev_icon.py" --output "${icon_icns}"
  cp "${icon_icns}" "${resources_dir}/Icon.icns"

  cat > "${plist_path}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>${display_name}</string>
  <key>CFBundleExecutable</key>
  <string>asusroutercontrol-launcher</string>
  <key>CFBundleIdentifier</key>
  <string>${bundle_id}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${display_name}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleIconFile</key>
  <string>Icon</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
EOF

  mkdir -p "${TEST_BUILDS_DIR}"
  rm -rf "${dest_app}"
  cp -R "${app_dir}" "${dest_app}"
  touch "${dest_app}/Contents/Resources/DEV_BUILD"

  echo "Built ${app_name}"
  echo "Output: ${dest_app}"
}

build_prod_dmg() {
  local app_name="ASUSRouterControl.app"
  local dmg_name="ASUSRouterControl.dmg"
  local entrypoint="${PROD_BUILD_DIR}/prod_entrypoint.py"
  local prod_app="${PROD_DIST_DIR}/${app_name}"
  local prod_dmg="${PROD_DIST_DIR}/${dmg_name}"

  if ! "${VENV_PY}" -c "import asusroutercontrol.menubar" >/dev/null 2>&1; then
    echo "Project package is not importable in .venv." >&2
    echo "Run: ${VENV_PY} -m pip install -e '.[menubar]'" >&2
    exit 1
  fi

  if ! "${VENV_PY}" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller is required for production packaging." >&2
    echo "Run: ${VENV_PY} -m pip install pyinstaller" >&2
    exit 1
  fi

  rm -rf "${PROD_BUILD_DIR}"
  mkdir -p "${PROD_BUILD_DIR}" "${PROD_WORK_DIR}" "${PROD_SPEC_DIR}" "${PROD_DIST_DIR}"

  cat > "${entrypoint}" <<'EOF'
from asusroutercontrol.menubar import main

if __name__ == "__main__":
    main()
EOF

  rm -rf "${prod_app}" "${prod_dmg}"
  "${VENV_PY}" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "ASUSRouterControl" \
    --hidden-import AppKit \
    --hidden-import Foundation \
    --distpath "${PROD_DIST_DIR}" \
    --workpath "${PROD_WORK_DIR}" \
    --specpath "${PROD_SPEC_DIR}" \
    "${entrypoint}"

  if [[ ! -d "${prod_app}" ]]; then
    echo "Expected app bundle missing after PyInstaller build: ${prod_app}" >&2
    exit 1
  fi

  rm -rf "${PROD_DMG_STAGE_DIR}"
  mkdir -p "${PROD_DMG_STAGE_DIR}"
  cp -R "${prod_app}" "${PROD_DMG_STAGE_DIR}/${app_name}"
  ln -s /Applications "${PROD_DMG_STAGE_DIR}/Applications"

  hdiutil create \
    -volname "ASUSRouterControl" \
    -srcfolder "${PROD_DMG_STAGE_DIR}" \
    -ov \
    -format UDZO \
    "${prod_dmg}" >/dev/null

  echo "Built self-contained app: ${prod_app}"
  echo "Built production DMG: ${prod_dmg}"
}

if [[ "${MODE}" == "dev" ]]; then
  build_dev_app
else
  build_prod_dmg
fi
