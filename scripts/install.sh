#!/bin/sh
# Install Pinlet into a folder it owns, so nothing depends on where a checkout lives.
#
#   sh scripts/install.sh                 from a checkout: install a copy of it
#   curl -fsSL https://raw.githubusercontent.com/mayurkubavat/Pinlet/master/scripts/install.sh | sh
#                                         download the latest master and install it
#
# Options:
#   --source <dir|file.tar.gz>   install from this checkout or source archive
#   --ref <branch|tag>           download this ref instead of master (no --source)
#   --dev                        install the checkout itself, editable (for working on Pinlet)
#   --no-setup                   do not run `pinlet setup` for the AI tools afterwards
#   --progress                   print "::step N/M text" lines (Pinlet.app reads them)
#
# What it makes (macOS: ~/Library/Application Support/Pinlet, Linux: ~/.local/share/pinlet;
# PINLET_DATA_DIR overrides):
#   bin/uv        uv, when it is not installed already
#   python/       the Python uv downloads (macOS; Linux uses the system Python for PyGObject)
#   src/<id>/     the Pinlet source, one folder per installed version (the last two are kept)
#   runtime/      the virtual environment Pinlet runs in
# and ~/.pinlet/bin/pinlet, the command every AI tool runs (Linux also links ~/.local/bin/pinlet).
# Running it again updates Pinlet in place.
set -eu

REPO="${PINLET_REPO:-mayurkubavat/Pinlet}"
REF="master"
SOURCE=""
DEV=0
SETUP=1
PROGRESS=0
STEP=0
STEPS=6

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --dev) DEV=1; shift ;;
    --no-setup) SETUP=0; shift ;;
    --progress) PROGRESS=1; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "pinlet install: unknown option $1" >&2; exit 2 ;;
  esac
done

step() {
  STEP=$((STEP + 1))
  if [ "$PROGRESS" = 1 ]; then echo "::step $STEP/$STEPS $*"; else echo "==> $*"; fi
}
fail() {
  if [ "$PROGRESS" = 1 ]; then echo "::error $*"; fi
  echo "pinlet install: $*" >&2
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }
fetch() {  # fetch URL FILE
  if have curl; then curl -fsSL --retry 3 -o "$2" "$1"
  elif have wget; then wget -q -O "$2" "$1"
  else fail "needs curl or wget to download $1"; fi
}
sha() {  # a short id for a source archive: the hash of its unpacked tar stream
  if have shasum; then gzip -dc "$1" | shasum -a 256 | cut -c1-12
  else gzip -dc "$1" | sha256sum | cut -c1-12; fi
}

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)
    [ "$ARCH" = arm64 ] || fail "Pinlet supports Apple silicon Macs only (this one is $ARCH)"
    DATA="${PINLET_DATA_DIR:-$HOME/Library/Application Support/Pinlet}"
    TRIPLE="aarch64-apple-darwin"
    EXTRAS="macos,ocr" ;;
  Linux)
    DATA="${PINLET_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pinlet}"
    case "$ARCH" in
      x86_64|amd64) TRIPLE="x86_64-unknown-linux-gnu" ;;
      aarch64|arm64) TRIPLE="aarch64-unknown-linux-gnu" ;;
      *) fail "no uv build for Linux on $ARCH" ;;
    esac
    EXTRAS="linux,ocr" ;;
  *) fail "Pinlet runs on macOS and Linux (this is $OS)" ;;
esac
mkdir -p "$DATA"
chmod 700 "$DATA" 2>/dev/null || true
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

# ---------------------------------------------------------------- 1. uv
step "Getting uv"
if [ -x "$DATA/bin/uv" ]; then UV="$DATA/bin/uv"
elif have uv; then UV="$(command -v uv)"
else
  fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$TRIPLE.tar.gz" "$TMP/uv.tgz" \
    || fail "could not download uv"
  tar -xzf "$TMP/uv.tgz" -C "$TMP"
  mkdir -p "$DATA/bin"
  mv "$TMP/uv-$TRIPLE/uv" "$DATA/bin/uv"
  UV="$DATA/bin/uv"
fi
export UV_PYTHON_INSTALL_DIR="$DATA/python"

# ---------------------------------------------------------------- 2. source
step "Getting the Pinlet source"
HERE="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
if [ -z "$SOURCE" ] && [ -f "$HERE/pyproject.toml" ] && grep -q '^name = "Pinlet"' "$HERE/pyproject.toml"; then
  SOURCE="$HERE"
fi
if [ "$DEV" = 1 ]; then
  [ -d "$SOURCE" ] || fail "--dev needs a checkout (run it from one, or pass --source <dir>)"
  SRC="$(cd "$SOURCE" && pwd)"
else
  ARCHIVE="$TMP/pinlet-src.tar.gz"
  if [ -z "$SOURCE" ]; then
    fetch "https://github.com/$REPO/archive/$REF.tar.gz" "$ARCHIVE" \
      || fail "could not download $REPO@$REF"
  elif [ -d "$SOURCE" ]; then
    # The checkout as it is on disk, tracked files only (so no venvs or build output).
    if [ -d "$SOURCE/.git" ] && have git; then
      (cd "$SOURCE" && git ls-files --cached --others --exclude-standard \
        | while IFS= read -r f; do [ -e "$f" ] && printf '%s\n' "$f"; done \
        | tar -czf "$ARCHIVE" -T -) || fail "could not pack $SOURCE"
    else
      tar -czf "$ARCHIVE" -C "$SOURCE" --exclude .venv --exclude .git --exclude build . \
        || fail "could not pack $SOURCE"
    fi
  else
    [ -f "$SOURCE" ] || fail "no such source: $SOURCE"
    cp "$SOURCE" "$ARCHIVE"
  fi
  ID="$(sha "$ARCHIVE")"
  SRC="$DATA/src/$ID"
  if [ ! -f "$SRC/pyproject.toml" ]; then
    mkdir -p "$TMP/unpack"
    tar -xzf "$ARCHIVE" -C "$TMP/unpack"
    ROOT="$TMP/unpack"
    # GitHub archives hold one top folder (Pinlet-master/); local ones do not.
    if [ ! -f "$ROOT/pyproject.toml" ]; then ROOT="$(dirname "$(find "$TMP/unpack" -maxdepth 2 -name pyproject.toml | head -n 1)")"; fi
    [ -f "$ROOT/pyproject.toml" ] || fail "the source has no pyproject.toml"
    mkdir -p "$DATA/src"
    rm -rf "$SRC.partial"
    mv "$ROOT" "$SRC.partial"
    mv "$SRC.partial" "$SRC"
  fi
fi

# ---------------------------------------------------------------- 3. Python + venv
step "Setting up Python"
RUNTIME="$DATA/runtime"
PY="$RUNTIME/bin/python"
if [ ! -x "$PY" ] || ! "$PY" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
  rm -rf "$RUNTIME"
  if [ "$OS" = Linux ] && [ -x /usr/bin/python3 ] \
     && /usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    # The system Python carries PyGObject (overlay, accessibility tree), which pip cannot
    # install without compilers; it also stays where it is across upgrades.
    "$UV" venv -q --python /usr/bin/python3 --system-site-packages "$RUNTIME" \
      || fail "could not create the runtime"
  else
    "$UV" venv -q --python 3.12 --python-preference only-managed "$RUNTIME" \
      || fail "could not create the runtime"
  fi
fi

# ---------------------------------------------------------------- 4. Pinlet
step "Installing Pinlet and its packages (the first time takes a minute)"
"$UV" pip install -q --python "$PY" -e "$SRC[$EXTRAS]" || fail "installing Pinlet failed"
echo "$SRC" > "$RUNTIME/.pinlet-source"
# Keep this version and the one before it (a running Pinlet may still use that one).
if [ -d "$DATA/src" ]; then
  # shellcheck disable=SC2012  # names are our own hex ids
  ls -t "$DATA/src" | tail -n +3 | while read -r old; do rm -rf "$DATA/src/$old"; done
fi

# ---------------------------------------------------------------- 5. the pinlet command
step "Writing the pinlet command"
LAUNCHER="$("$PY" -c 'from pinlet import runtime; print(runtime.write_launcher())')" \
  || fail "could not write the launcher"
if [ "$OS" = Linux ]; then
  mkdir -p "$HOME/.local/bin"
  if [ ! -e "$HOME/.local/bin/pinlet" ] || [ -L "$HOME/.local/bin/pinlet" ]; then
    ln -sf "$LAUNCHER" "$HOME/.local/bin/pinlet"
  fi
fi

# ---------------------------------------------------------------- 6. AI tools
if [ "$SETUP" = 1 ]; then
  step "Connecting your AI tools"
  "$LAUNCHER" setup || echo "Some AI tools could not be set up; see above." >&2
else
  step "Done"
fi
"$LAUNCHER" doctor >"$TMP/doctor.txt" 2>&1 || {
  echo "Pinlet is installed, but some checks failed:" >&2
  cat "$TMP/doctor.txt" >&2
}
if [ "$PROGRESS" = 1 ]; then echo "::done $LAUNCHER"; fi
echo "Pinlet is installed: $LAUNCHER"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) [ "$OS" = Linux ] && echo "Add ~/.local/bin to your PATH to run pinlet from a terminal." ;;
esac
exit 0
