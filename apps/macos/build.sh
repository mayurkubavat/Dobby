#!/bin/bash
# Build Dobby.app into apps/macos/build/ (ad-hoc signed).
#   ./build.sh            release build
#   ./build.sh --install  also copy it to ~/Applications
#   ./build.sh --setup-signing   once: a local signing identity, so macOS
#                                permissions survive rebuilds
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--setup-signing" ]]; then
  # A self-signed code signing certificate in your login keychain. It never
  # leaves this Mac and is only used to sign your own Dobby.app builds.
  TMP="$(mktemp -d)"
  cat > "$TMP/cert.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = Dobby Local Signing
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/cert.cnf" \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" >/dev/null 2>&1
  PASS="dobby-$RANDOM$RANDOM"
  openssl pkcs12 -export -legacy -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" \
    -passout "pass:$PASS" >/dev/null 2>&1 \
    || openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" -passout "pass:$PASS"
  security import "$TMP/id.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P "$PASS" -T /usr/bin/codesign
  # Trust it for code signing only (macOS asks for your password once).
  security add-trusted-cert -p codeSign -k "$HOME/Library/Keychains/login.keychain-db" "$TMP/cert.pem"
  rm -rf "$TMP"
  echo "Created the \"Dobby Local Signing\" identity. Run ./build.sh --install again;"
  echo "grant Screen Recording and Accessibility to Dobby once more, and they will stick."
  exit 0
fi
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
# macOS ties Screen Recording and Accessibility to the signature. An ad-hoc
# signature changes with every build, so permissions would reset each time;
# a fixed local identity ("Dobby Local Signing", made once by
# ./build.sh --setup-signing) keeps them. DOBBY_SIGN_IDENTITY overrides.
LOCAL_ID="Dobby Local Signing"
if [[ -n "${DOBBY_SIGN_IDENTITY:-}" ]]; then
  IDENTITY="$DOBBY_SIGN_IDENTITY"
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
  rm -rf "$HOME/Applications/Dobby.app"
  cp -R "$APP" "$HOME/Applications/Dobby.app"
  echo "Installed ~/Applications/Dobby.app"
  # Only the installed copy should exist for macOS: a second Dobby.app (this
  # build folder) shows up in Spotlight and can catch the permission grants.
  LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  rm -rf "$APP"
  [[ -x "$LSREG" ]] && "$LSREG" -u "$PWD/$APP" >/dev/null 2>&1 || true
  # A running Dobby is still the old build (and the old signature): restart it.
  if pgrep -f "Dobby.app/Contents/MacOS/Dobby" >/dev/null; then
    osascript -e 'quit app "Dobby"' >/dev/null 2>&1 || true
    for _ in $(seq 1 50); do pgrep -f "Dobby.app/Contents/MacOS/Dobby" >/dev/null || break; sleep 0.1; done
    open "$HOME/Applications/Dobby.app"
    echo "Restarted Dobby"
  fi
fi
