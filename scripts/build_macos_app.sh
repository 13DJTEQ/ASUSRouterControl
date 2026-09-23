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
  local bundle_executable="asusroutercontrol-launcher"
  local home_dir="${HOME}"
  local env_file_path=""

  rm -rf "${app_dir}"
  mkdir -p "${macos_dir}" "${resources_dir}"

  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    env_file_path="${PROJECT_ROOT}/.env"
  fi

  # Sequoia ControlCenter will not reliably show NSStatusItem for a bash
  # CFBundleExecutable that exec's CPython. Embed a real Mach-O GUI binary
  # from PyInstaller as the app executable instead.
  if ! "${VENV_PY}" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller is required for DEV.app (menubar icon on modern macOS)." >&2
    echo "Run: ${VENV_PY} -m pip install pyinstaller" >&2
    exit 1
  fi

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
    --osx-bundle-identifier "${bundle_id}" \
    "${dev_runtime_entrypoint}"

  if [[ ! -x "${dev_runtime_exe}" ]]; then
    echo "PyInstaller did not produce ${dev_runtime_exe}" >&2
    exit 1
  fi

  # Copy Mach-O + support files into our DEV.app bundle.
  # Keep the binary name as CFBundleExecutable so dyld finds _internal/Frameworks.
  bundle_executable="${dev_runtime_name}"
  # shellcheck disable=SC2045
  for entry in "${dev_runtime_app}/Contents/MacOS/"*; do
    cp -R "${entry}" "${macos_dir}/"
  done
  if [[ -d "${dev_runtime_app}/Contents/Frameworks" ]]; then
    rm -rf "${contents_dir}/Frameworks"
    cp -R "${dev_runtime_app}/Contents/Frameworks" "${contents_dir}/Frameworks"
  fi
  if [[ -d "${dev_runtime_app}/Contents/Resources" ]]; then
    # Merge PyInstaller resources; our Icon.icns is written after this.
    cp -R "${dev_runtime_app}/Contents/Resources/." "${resources_dir}/"
  fi

  # Optional escape hatch: bash launcher that prefers live venv source.
  # Not the CFBundleExecutable — only for manual/debug use.
  cat > "${launcher}" <<EOF
#!/usr/bin/env bash
# Debug launcher (not used by Finder). Prefer:
#   open "testbuilds/ASUSRouterControl DEV.app"
PROJECT_ROOT="${PROJECT_ROOT}"
VENV_PY="\${PROJECT_ROOT}/.venv/bin/python"
SELF_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
BUNDLED_EXE="\${SELF_DIR}/${dev_runtime_name}"
LAUNCH_LOG="\${HOME}/.asusroutercontrol.dev/launcher.log"
mkdir -p "\${HOME}/.asusroutercontrol.dev" 2>/dev/null || true
_log() {
  printf '%s %s\n' "\$(date '+%Y-%m-%dT%H:%M:%S%z')" "\$*" >> "\${LAUNCH_LOG}" 2>/dev/null || true
}
export ASUSROUTERCONTROL_RUNTIME_ENV="dev"
export ASUSROUTERCONTROL_APP_BUNDLE="\$(cd "\${SELF_DIR}/../.." && pwd)"
export ASUSROUTERCONTROL_PROJECT_ROOT="\${PROJECT_ROOT}"
if [[ -f "\${PROJECT_ROOT}/.env" ]]; then
  export ASUSROUTERCONTROL_ENV_FILE="\${PROJECT_ROOT}/.env"
fi
export DATA_DIR="\${HOME}/.asusroutercontrol.dev"
export SOUNDSHIELD_EXPORT_PATH="\${HOME}/.asusroutercontrol.dev/soundshield_network.json"
_log "debug launcher start bundle=\${ASUSROUTERCONTROL_APP_BUNDLE}"
if [[ "\${ASUSROUTERCONTROL_DEV_USE_VENV:-}" == "1" && -x "\${VENV_PY}" ]]; then
  export PYTHONPATH="\${PROJECT_ROOT}/src:\${PYTHONPATH:-}"
  _log "exec venv (ASUSROUTERCONTROL_DEV_USE_VENV=1)"
  exec "\${VENV_PY}" -m asusroutercontrol.menubar >>"\${LAUNCH_LOG}" 2>&1
fi
if [[ -x "\${BUNDLED_EXE}" ]]; then
  _log "exec bundled Mach-O: \${BUNDLED_EXE}"
  exec "\${BUNDLED_EXE}"
fi
_log "FAIL: no runtime"
exit 1
EOF
  chmod +x "${launcher}"
  chmod +x "${macos_dir}/${dev_runtime_name}"

  "${VENV_PY}" "${SCRIPT_DIR}/generate_dev_icon.py" --output "${icon_icns}"
  cp "${icon_icns}" "${resources_dir}/Icon.icns"

  # LSEnvironment injects env into the Mach-O GUI process (no bash wrapper).
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
  <string>${bundle_executable}</string>
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
  <key>LSEnvironment</key>
  <dict>
    <key>ASUSROUTERCONTROL_RUNTIME_ENV</key>
    <string>dev</string>
    <key>ASUSROUTERCONTROL_PROJECT_ROOT</key>
    <string>${PROJECT_ROOT}</string>
    <key>ASUSROUTERCONTROL_APP_BUNDLE</key>
    <string>${dest_app}</string>
    <key>DATA_DIR</key>
    <string>${home_dir}/.asusroutercontrol.dev</string>
    <key>SOUNDSHIELD_EXPORT_PATH</key>
    <string>${home_dir}/.asusroutercontrol.dev/soundshield_network.json</string>
EOF
  if [[ -n "${env_file_path}" ]]; then
    cat >> "${plist_path}" <<EOF
    <key>ASUSROUTERCONTROL_ENV_FILE</key>
    <string>${env_file_path}</string>
EOF
  fi
  cat >> "${plist_path}" <<EOF
  </dict>
</dict>
</plist>
EOF

  mkdir -p "${TEST_BUILDS_DIR}"
  rm -rf "${dest_app}"
  cp -R "${app_dir}" "${dest_app}"
  touch "${dest_app}/Contents/Resources/DEV_BUILD"
  # Refresh Launch Services registration for the new executable name.
  if command -v /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister >/dev/null 2>&1; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "${dest_app}" >/dev/null 2>&1 || true
  fi

  echo "Built ${app_name}"
  echo "Output: ${dest_app}"
  echo "Executable: ${bundle_executable} (Mach-O GUI — required for Sequoia menubar icon)"
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
