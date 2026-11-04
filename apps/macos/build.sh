#!/bin/bash
# Build Dobby.app into apps/macos/build/ (ad-hoc signed).
#   ./build.sh            release build
#   ./build.sh --install  also copy it to ~/Applications
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/Dobby"
APP=build/Dobby.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Dobby"
cp Resources/Info.plist "$APP/Contents/Info.plist"
ICONSET="$(mktemp -d)/Dobby.iconset"
"$BIN" --icon "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Dobby.icns"
# A stable identity keeps macOS permissions across rebuilds; ad-hoc is the
# fallback (then Screen Recording may need re-granting after a rebuild).
IDENTITY="${DOBBY_SIGN_IDENTITY:--}"
codesign --force --deep --sign "$IDENTITY" "$APP"
echo "Built $APP"
if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Dobby.app"
  cp -R "$APP" "$HOME/Applications/Dobby.app"
  echo "Installed ~/Applications/Dobby.app"
fi
