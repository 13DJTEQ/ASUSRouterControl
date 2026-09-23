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
echo "1) Check the » overflow chevron on the right of the menu bar."
echo "2) System Settings → Control Center → scroll to Menu Bar Only / Status items."
echo "3) Reset Control Center (safe):  killall ControlCenter"
echo "4) Reload PROD launchd:  launchctl kickstart -k \"gui/$(id -u)/com.asusroutermonitor\""
echo "5) Or reinstall:  asusrouter menubar install"
echo "=== done ==="
