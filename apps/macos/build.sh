#!/bin/bash
# Build Pinlet.app into apps/macos/build/ (ad-hoc signed).
#   ./build.sh            release build
#   ./build.sh --install  also copy it to ~/Applications
#   ./build.sh --setup-signing   once: a local signing identity, so macOS
#                                permissions survive rebuilds
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--setup-signing" ]]; then
  # A self-signed code signing certificate in your login keychain. It never
  # leaves this Mac and is only used to sign your own Pinlet.app builds.
  TMP="$(mktemp -d)"
  cat > "$TMP/cert.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = Pinlet Local Signing
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/cert.cnf" \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" >/dev/null 2>&1
  PASS="pinlet-$RANDOM$RANDOM"
  openssl pkcs12 -export -legacy -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" \
    -passout "pass:$PASS" >/dev/null 2>&1 \
    || openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" -passout "pass:$PASS"
  security import "$TMP/id.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P "$PASS" -T /usr/bin/codesign
  # Trust it for code signing only (macOS asks for your password once).
  security add-trusted-cert -p codeSign -k "$HOME/Library/Keychains/login.keychain-db" "$TMP/cert.pem"
  rm -rf "$TMP"
  echo "Created the \"Pinlet Local Signing\" identity. Run ./build.sh --install again;"
  echo "grant Screen Recording and Accessibility to Pinlet once more, and they will stick."
  exit 0
fi
APP=build/Pinlet.app
rm -rf "$APP"
mkdir -p build
# With XcodeGen and Xcode, build a real app target: only that runs the App
# Intents metadata step (Spotlight / Shortcuts actions). PINLET_SWIFTPM=1 forces
# the plain SwiftPM build.
if [[ -z "${PINLET_SWIFTPM:-}" ]] && command -v xcodegen >/dev/null \
   && xcodebuild -version >/dev/null 2>&1; then
  xcodegen generate --quiet
  xcodebuild -project Pinlet.xcodeproj -scheme Pinlet -configuration Release \
    -derivedDataPath .build/xcode -quiet build
  cp -R .build/xcode/Build/Products/Release/Pinlet.app "$APP"
  BIN="$APP/Contents/MacOS/Pinlet"
  [[ -d "$APP/Contents/Resources/Metadata.appintents" ]] \
    && echo "App Intents registered (Spotlight, Shortcuts, Siri)" \
    || echo "note: no Metadata.appintents in the build; App Intents will not show up"
else
  command -v xcodegen >/dev/null || echo "note: brew install xcodegen for App Intents (Spotlight, Shortcuts)"
  swift build -c release
  BIN="$(swift build -c release --show-bin-path)/Pinlet"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp "$BIN" "$APP/Contents/MacOS/Pinlet"
  cp Resources/Info.plist "$APP/Contents/Info.plist"
fi
mkdir -p "$APP/Contents/Resources"
# Pinlet's engine travels inside the app: the installer and this checkout's
# source (tracked and new files, as they are on disk). On first launch, or when
# this source differs from the installed one, the app runs the installer, which
# sets up ~/Library/Application Support/Pinlet with uv. No venv in the checkout needed.
ROOT="$(cd ../.. && pwd)"
RES="$APP/Contents/Resources"
cp "$ROOT/scripts/install.sh" "$RES/install.sh"
(cd "$ROOT" && git ls-files --cached --others --exclude-standard \
  | while IFS= read -r f; do [ -e "$f" ] && printf '%s\n' "$f"; done \
  | tar -czf "$OLDPWD/$RES/pinlet-src.tar.gz" -T -)
gzip -dc "$RES/pinlet-src.tar.gz" | shasum -a 256 | cut -c1-12 > "$RES/pinlet-src.id"
echo "Bundled Pinlet source $(cat "$RES/pinlet-src.id") ($(du -h "$RES/pinlet-src.tar.gz" | cut -f1))"
ICONSET="$(mktemp -d)/Pinlet.iconset"
"$BIN" --icon "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Pinlet.icns"
# macOS ties Screen Recording and Accessibility to the signature. An ad-hoc
# signature changes with every build, so permissions would reset each time;
# a fixed local identity ("Pinlet Local Signing", made once by
# ./build.sh --setup-signing) keeps them. PINLET_SIGN_IDENTITY overrides.
LOCAL_ID="Pinlet Local Signing"
if [[ -n "${PINLET_SIGN_IDENTITY:-}" ]]; then
  IDENTITY="$PINLET_SIGN_IDENTITY"
elif security find-identity -p codesigning 2>/dev/null | grep -q "\"$LOCAL_ID\"" \
     || security find-certificate -c "$LOCAL_ID" >/dev/null 2>&1; then
  IDENTITY="$LOCAL_ID"
else
  IDENTITY="-"
  echo "note: ad-hoc signing; run ./build.sh --setup-signing once so permissions survive rebuilds"
fi
codesign --force --deep --sign "$IDENTITY" "$APP"
echo "Built $APP"
if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Pinlet.app"
  cp -R "$APP" "$HOME/Applications/Pinlet.app"
  echo "Installed ~/Applications/Pinlet.app"
  # Only the installed copy should exist for macOS: a second Pinlet.app (this
  # build folder) shows up in Spotlight and can catch the permission grants.
  LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  rm -rf "$APP"
  [[ -x "$LSREG" ]] && "$LSREG" -u "$PWD/$APP" >/dev/null 2>&1 || true
  # A running Pinlet is still the old build (and the old signature): restart it.
  if pgrep -f "Pinlet.app/Contents/MacOS/Pinlet" >/dev/null; then
    osascript -e 'quit app "Pinlet"' >/dev/null 2>&1 || true
    for _ in $(seq 1 150); do pgrep -f "Pinlet.app/Contents/MacOS/Pinlet" >/dev/null || break; sleep 0.1; done
    pkill -f "Pinlet.app/Contents/MacOS/Pinlet" 2>/dev/null || true  # still quitting after 15 s
    # Right after a quit, Launch Services can refuse with error -600: try again.
    started=""
    for _ in 1 2 3 4 5; do
      sleep 1
      if open "$HOME/Applications/Pinlet.app" 2>/dev/null; then started=1; break; fi
    done
    if [[ -n "$started" ]]; then echo "Restarted Pinlet"; else echo "Could not restart Pinlet: open ~/Applications/Pinlet.app" >&2; fi
  fi
fi
