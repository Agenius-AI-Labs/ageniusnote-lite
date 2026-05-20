#!/usr/bin/env bash
# Build AgeniusNote Lite macOS .app + .dmg.
#
# Usage:
#     ./packaging/build.sh                # version from packaging/VERSION
#     ./packaging/build.sh 1.0.0          # override version
#     SKIP_BUILD=1 ./packaging/build.sh   # skip PyInstaller, only repackage .dmg
#
# Requires:
#     - macOS 11+
#     - Python 3.11+ with project deps (see requirements.txt)
#     - create-dmg  (brew install create-dmg)  -- optional, falls back to hdiutil
#     - iconutil + sips (preinstalled on macOS)

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ $# -gt 0 ]]; then
    VERSION="$1"
else
    VERSION="$(tr -d '[:space:]' < packaging/VERSION)"
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
DIST="dist-build-mac-${STAMP}"
WORK="build-build-mac-${STAMP}"

echo ">> Building AgeniusNote Lite v${VERSION}"
echo ">> Output: ${DIST}"

# --- 0. Generate .icns from .png (only if missing) ---
ICNS="assets/icon.icns"
PNG="assets/icon.png"
if [[ ! -f "$ICNS" ]]; then
    echo ">> Generating icon.icns from icon.png..."
    if [[ ! -f "$PNG" ]]; then
        echo "ERROR: $PNG missing. Run scripts/build_icon.py first." >&2
        exit 1
    fi
    ICONSET="$(mktemp -d)/icon.iconset"
    mkdir -p "$ICONSET"
    sips -z 16   16   "$PNG" --out "$ICONSET/icon_16x16.png"     > /dev/null
    sips -z 32   32   "$PNG" --out "$ICONSET/icon_16x16@2x.png"  > /dev/null
    sips -z 32   32   "$PNG" --out "$ICONSET/icon_32x32.png"     > /dev/null
    sips -z 64   64   "$PNG" --out "$ICONSET/icon_32x32@2x.png"  > /dev/null
    sips -z 128  128  "$PNG" --out "$ICONSET/icon_128x128.png"   > /dev/null
    sips -z 256  256  "$PNG" --out "$ICONSET/icon_128x128@2x.png" > /dev/null
    sips -z 256  256  "$PNG" --out "$ICONSET/icon_256x256.png"   > /dev/null
    sips -z 512  512  "$PNG" --out "$ICONSET/icon_256x256@2x.png" > /dev/null
    sips -z 512  512  "$PNG" --out "$ICONSET/icon_512x512.png"   > /dev/null
    cp "$PNG" "$ICONSET/icon_512x512@2x.png"
    iconutil -c icns "$ICONSET" -o "$ICNS"
    echo ">> Wrote $ICNS"
fi

# --- 1. PyInstaller ---
PYTHON="${PYTHON:-python3}"
if [[ -z "${SKIP_BUILD:-}" ]]; then
    echo ">> Running PyInstaller (interpreter: $PYTHON)..."
    "$PYTHON" -m pip install --quiet --upgrade pyinstaller
    "$PYTHON" -m PyInstaller packaging/ageniusnote_lite.spec \
        --noconfirm --clean \
        --distpath "$DIST" \
        --workpath "$WORK"
else
    echo ">> Skipping PyInstaller (SKIP_BUILD=1)"
fi

APP="${DIST}/AgeniusNote Lite.app"
if [[ ! -d "$APP" ]]; then
    echo "ERROR: $APP not found after PyInstaller." >&2
    exit 1
fi

# --- 2. Package .dmg ---
# ARCH suffix lets us ship separate Apple Silicon and Intel DMGs to the same release.
# Set ARCH=arm64 or ARCH=x86_64 via env; defaults to the host arch.
ARCH="${ARCH:-$(uname -m)}"
if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
    ARCH_LABEL="arm64"
elif [[ "$ARCH" == "x86_64" || "$ARCH" == "amd64" ]]; then
    ARCH_LABEL="x86_64"
else
    ARCH_LABEL="$ARCH"
fi
DMG_OUT="${DIST}/AgeniusNoteLite-Setup-${VERSION}-${ARCH_LABEL}.dmg"
echo ">> Packaging $DMG_OUT (arch=${ARCH_LABEL})"

if command -v create-dmg > /dev/null 2>&1; then
    create-dmg \
        --volname "AgeniusNote Lite ${VERSION}" \
        --window-pos 200 200 \
        --window-size 600 380 \
        --icon-size 100 \
        --icon "AgeniusNote Lite.app" 175 180 \
        --hide-extension "AgeniusNote Lite.app" \
        --app-drop-link 425 180 \
        --no-internet-enable \
        "$DMG_OUT" \
        "$APP" || true
else
    echo ">> create-dmg not installed; using hdiutil fallback"
    STAGING="${DIST}/dmg-staging"
    mkdir -p "$STAGING"
    cp -R "$APP" "$STAGING/"
    ln -sf /Applications "$STAGING/Applications"
    hdiutil create -volname "AgeniusNote Lite ${VERSION}" \
        -srcfolder "$STAGING" \
        -ov -format UDZO \
        "$DMG_OUT"
    rm -rf "$STAGING"
fi

if [[ -f "$DMG_OUT" ]]; then
    SIZE_MB=$(du -m "$DMG_OUT" | awk '{print $1}')
    echo ">> SUCCESS"
    echo "   DMG:  $DMG_OUT"
    echo "   Size: ${SIZE_MB} MB"
else
    echo ">> Build finished but DMG not found at expected path: $DMG_OUT" >&2
    exit 1
fi
