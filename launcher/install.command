#!/bin/bash
# Double-click once per Mac. Builds "Zaarour Desk.app" in ~/Applications so the boot loader page's
# zaarour:// buttons can start the desk. Remove it any time by deleting that app.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
desk="$(cd "$here/.." && pwd)"
py="$(command -v python3)"
app="$HOME/Applications/Zaarour Desk.app"
mkdir -p "$HOME/Applications"
src="$(mktemp -t zaarour-desk).applescript"
sed -e "s|__DESK_DIR__|$desk|" -e "s|__PYTHON__|$py|" "$here/Zaarour Desk.applescript" > "$src"
rm -rf "$app"
osacompile -o "$app" "$src"
rm -f "$src"
plist="$app/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "$1" "$plist"; }
pb "Add :CFBundleIdentifier string ca.zaarour.desk" 2>/dev/null || pb "Set :CFBundleIdentifier ca.zaarour.desk"
pb "Add :LSUIElement bool true"
pb "Add :CFBundleURLTypes array"
pb "Add :CFBundleURLTypes:0 dict"
pb "Add :CFBundleURLTypes:0:CFBundleURLName string Zaarour Desk"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes array"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string zaarour"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f "$app"
echo
echo "Installed: $app"
echo "Desk folder: $desk"
echo "The boot loader page's buttons now start the desk. You can close this window."
