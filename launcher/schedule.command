#!/bin/bash
# Double-click once per Mac (or run with "remove" to take it off). Reads the boards on a schedule, even when
# the desk isn't open: every night at 2:30 and again at noon, Mac local time. Each run is
# "python3 -m desk fetch", so the scan cache still applies: a board read recently is reused, not read again.
# The Mac has to be awake at 2:30; see SCANNER-LAUNCH.md, Schedule, for the wake setting.
set -e
label="ca.zaarour.desk.scan"
plist="$HOME/Library/LaunchAgents/$label.plist"
here="$(cd "$(dirname "$0")" && pwd)"
desk="$(cd "$here/.." && pwd)"
py="$(command -v python3)"

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
if [ "$1" = "remove" ]; then
  rm -f "$plist"
  echo "Schedule removed."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$desk/data/logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$py</string><string>-m</string><string>desk</string><string>fetch</string></array>
  <key>WorkingDirectory</key><string>$desk</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$desk/data/logs/scan.log</string>
  <key>StandardErrorPath</key><string>$desk/data/logs/scan.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Scheduled: boards are read at 2:30 and 12:00 every day. Log: $desk/data/logs/scan.log"
