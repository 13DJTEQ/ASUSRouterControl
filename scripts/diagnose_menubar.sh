#!/usr/bin/env bash
# Diagnose why ASUSRouterControl menubar icons may be missing on macOS.
set -euo pipefail

echo "=== ASUSRouterControl menubar diagnose ==="
echo "date: $(date)"
echo

echo "--- processes (ps) ---"
ps eww -Ao pid,etime,command 2>/dev/null | grep -iE 'asusrouter|DevRuntime' | grep -v grep || echo "(none)"
echo

echo "--- launchd agents ---"
for label in com.asusroutermonitor com.asusroutermonitor.dev; do
  if launchctl list "$label" >/dev/null 2>&1; then
    echo "LOADED: $label"
    launchctl list "$label" 2>/dev/null || true
  else
    echo "not loaded: $label"
  fi
  plist="$HOME/Library/LaunchAgents/${label}.plist"
  if [[ -f "$plist" ]]; then
    echo "  plist: $plist"
    # Show ProgramArguments so we can spot bare python -m (Sequoia icon risk).
    /usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$plist" 2>/dev/null || true
  else
    echo "  plist missing: $plist"
  fi
done
echo

echo "--- recent PROD log ---"
if [[ -f "$HOME/.asusroutercontrol/scheduler.log" ]]; then
  tail -n 15 "$HOME/.asusroutercontrol/scheduler.log"
else
  echo "(no ~/.asusroutercontrol/scheduler.log)"
fi
echo

echo "--- recent DEV log ---"
if [[ -f "$HOME/.asusroutercontrol.dev/scheduler.log" ]]; then
  tail -n 15 "$HOME/.asusroutercontrol.dev/scheduler.log"
else
  echo "(no ~/.asusroutercontrol.dev/scheduler.log)"
fi
echo

echo "--- DEV launcher log ---"
if [[ -f "$HOME/.asusroutercontrol.dev/launcher.log" ]]; then
  tail -n 20 "$HOME/.asusroutercontrol.dev/launcher.log"
else
  echo "(no launcher.log)"
fi
echo

echo "--- tips ---"
echo "1) Check the menu-bar overflow chevron on the right."
echo "2) System Settings -> Control Center -> Menu Bar Only / Status items."
echo "3) Reset Control Center (safe):  killall ControlCenter"
echo "4) Prefer launchd via a real .app Mach-O (Sequoia often hides python -m icons):"
echo "     asusrouter menubar install"
echo "     # DEV: make build-dev-app && asusrouter menubar install --environment=dev"
echo "5) Confirm plist ProgramArguments point at Contents/MacOS/... not bare python:"
echo "     plutil -p \"\$HOME/Library/LaunchAgents/com.asusroutermonitor.plist\""
echo "     plutil -p \"\$HOME/Library/LaunchAgents/com.asusroutermonitor.dev.plist\" 2>/dev/null || true"
echo "6) Reload PROD launchd:  launchctl kickstart -k \"gui/\$(id -u)/com.asusroutermonitor\""
echo "=== done ==="
