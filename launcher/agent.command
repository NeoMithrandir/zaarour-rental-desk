#!/bin/bash
# Double-click once on the office Mac when the desk is online (cloud/README.md), or run with "remove" to take it off.
# Starts the Mac's background job at login and keeps it running: it reads the boards whose last read is stale
# (every scan.auto_refresh_minutes, and whenever someone clicks Check boards online), sends the new ads to
# rentals.zaarour.ca and brings the team's decisions back. It replaces the 2:30/12:00 schedule (schedule.command),
# which it turns off so two reads never run at once. The Mac has to be on and awake for the boards to be read.
set -e
label="ca.zaarour.desk.agent"
plist="$HOME/Library/LaunchAgents/$label.plist"
here="$(cd "$(dirname "$0")" && pwd)"
desk="$(cd "$here/.." && pwd)"
py="$(command -v python3)"

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
if [ "$1" = "remove" ]; then
  rm -f "$plist"
  echo "Background job removed. To read the boards on a schedule again, double-click schedule.command."
  exit 0
fi

if ! "$py" -c "import sys; sys.path.insert(0, '$desk'); from desk import cloud, config; sys.exit(0 if cloud.enabled(config.load_settings()) else 1)"; then
  echo "The online desk isn't set up on this Mac yet: fill in \"cloud\" in config/settings.json (cloud/README.md, step 6)."
  exit 1
fi

# The scheduled reads are now the agent's job.
launchctl bootout "gui/$(id -u)/ca.zaarour.desk.scan" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/ca.zaarour.desk.scan.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$desk/data/logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$py</string><string>-u</string><string>-m</string><string>desk</string><string>cloud</string><string>agent</string></array>
  <key>WorkingDirectory</key><string>$desk</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$desk/data/logs/agent.log</string>
  <key>StandardErrorPath</key><string>$desk/data/logs/agent.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Running: this Mac reads the boards for the online desk. Log: $desk/data/logs/agent.log"
