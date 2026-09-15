#!/usr/bin/env bash
# Dump recent menubar Connect diagnostics from the Mac log files.
# Run on the Mac (DEV or PROD data dir).
set -euo pipefail

candidates=(
  "${HOME}/.asusroutercontrol.dev/scheduler.log"
  "${HOME}/.asusroutercontrol/scheduler.log"
)

found=0
for log in "${candidates[@]}"; do
  if [[ -f "$log" ]]; then
    found=1
    echo "======== $log (last 80 matching lines) ========"
    grep -E "Connect defaults|HTTP login attempt|HTTP probe failed|HTTP admin preflight|Stopping transport|Connect router failed|Bitwarden|rejected login|captcha|Cannot access|Unable to Connect" "$log" \
      | tail -n 80 || true
    echo
    echo "-------- raw tail (40 lines) --------"
    tail -n 40 "$log"
    echo
  fi
done

if [[ "$found" -eq 0 ]]; then
  echo "No scheduler.log found under ~/.asusroutercontrol[.dev]"
  echo "Open the menubar app once, try Connect, then re-run this script."
  exit 1
fi
