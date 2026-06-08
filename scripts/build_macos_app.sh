#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/build_macos_app.sh --mode <test|prod>

Modes:
  test  Build ASUSRouterControl TEST.app into ./testbuilds with a red TEST icon.
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

if [[ "$MODE" != "test" && "$MODE" != "prod" ]]; then
  echo "Missing or invalid --mode. Expected test or prod." >&2
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_BUILDS_DIR="${PROJECT_ROOT}/testbuilds"
BUILD_ROOT="${PROJECT_ROOT}/build/macos-app"
STAGE_DIR="${BUILD_ROOT}/stage-test"
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

build_test_app() {
  local app_name="ASUSRouterControl TEST.app"
  local bundle_id="dev.mediawavetech.asusroutercontrol.test"
  local display_name="ASUSRouterControl TEST"
  local icon_icns="${BUILD_ROOT}/Icon-Test.icns"
  local app_dir="${STAGE_DIR}/${app_name}"
  local contents_dir="${app_dir}/Contents"
  local macos_dir="${contents_dir}/MacOS"
  local resources_dir="${contents_dir}/Resources"
  local launcher="${macos_dir}/asusroutercontrol-launcher"
  local plist_path="${contents_dir}/Info.plist"
  local dest_app="${TEST_BUILDS_DIR}/${app_name}"

  rm -rf "${app_dir}"
  mkdir -p "${macos_dir}" "${resources_dir}"

  cat > "${launcher}" <<EOF
#!/usr/bin/env bash
PROJECT_ROOT="${PROJECT_ROOT}"
VENV_PY="\${PROJECT_ROOT}/.venv/bin/python"
SELF_CONTAINED_EXE="\${PROJECT_ROOT}/dist/ASUSRouterControl.app/Contents/MacOS/ASUSRouterControl"

# Prefer the self-contained binary when available. LaunchServices may apply
# system-policy restrictions to interpreter paths on external volumes.
if [[ -x "\${SELF_CONTAINED_EXE}" ]]; then
  exec "\${SELF_CONTAINED_EXE}"
fi

if [[ ! -x "\${VENV_PY}" ]]; then
  /usr/bin/osascript -e 'display alert "ASUSRouterControl cannot start" message "Expected Python runtime not found in project .venv. Run make setup in the project folder."'
  exit 1
fi

export PYTHONPATH="\${PROJECT_ROOT}/src:\${PYTHONPATH:-}"
exec "\${VENV_PY}" -m asusroutercontrol.menubar
EOF
  chmod +x "${launcher}"

  "${VENV_PY}" "${SCRIPT_DIR}/generate_test_icon.py" --output "${icon_icns}"
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
  touch "${dest_app}/Contents/Resources/TEST_BUILD"

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

if [[ "${MODE}" == "test" ]]; then
  build_test_app
else
  build_prod_dmg
fi
