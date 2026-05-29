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
#
# Signing + notarization (all optional; unset = unsigned build, prior behavior):
#     VN_SIGN_IDENTITY    "Developer ID Application: Your Name (TEAMID)"
#                         (list yours with: security find-identity -v -p codesigning)
#   Notary auth, pick ONE:
#     VN_NOTARY_PROFILE   name of a stored notarytool keychain profile, created once:
#                           xcrun notarytool store-credentials NOTARY_PROFILE \
#                             --apple-id you@example.com --team-id TEAMID \
#                             --password <app-specific-password>
#     -- or the Apple ID trio --
#     VN_NOTARY_APPLE_ID  your Apple ID email
#     VN_NOTARY_PASSWORD  an app-specific password (appleid.apple.com > Sign-In & Security)
#     VN_NOTARY_TEAM_ID   your 10-char Developer Team ID
#
# Example signed + notarized build:
#     VN_SIGN_IDENTITY="Developer ID Application: Michael Frostbutter (TEAMID)" \
#     VN_NOTARY_PROFILE="agenius-notary" ./packaging/build.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ $# -gt 0 ]]; then
    VERSION="$1"
else
    VERSION="$(tr -d '[:space:]' < packaging/VERSION)"
fi

if [[ -n "${SKIP_BUILD:-}" ]]; then
    # Reuse the most recent existing dist-build-mac-* dir that actually has the
    # built .app inside, so re-running just the .dmg step works.
    DIST="$(ls -td dist-build-mac-*/ 2>/dev/null | while read -r d; do
        d="${d%/}"
        [[ -d "${d}/AgeniusNote Lite.app" ]] && { echo "$d"; break; }
    done)"
    if [[ -z "$DIST" ]]; then
        echo "ERROR: SKIP_BUILD=1 set but no existing dist-build-mac-*/AgeniusNote Lite.app found. Run a full build first." >&2
        exit 1
    fi
    WORK=""  # unused when skipping PyInstaller
    echo ">> Reusing existing build path: ${DIST}"
else
    STAMP="$(date +%Y%m%d-%H%M%S)"
    DIST="dist-build-mac-${STAMP}"
    WORK="build-build-mac-${STAMP}"
fi

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

# --- 1b. Codesign with Developer ID + hardened runtime ---
# Gated on VN_SIGN_IDENTITY (e.g. "Developer ID Application: Name (TEAMID)").
# When unset, we ship the prior ad-hoc/unsigned bundle unchanged.
#
# We must sign every Mach-O *inside* the bundle (PyInstaller ships hundreds of
# .dylib/.so from pip wheels) bottom-up, then the .app last. Hardened runtime
# (--options runtime) + a secure --timestamp are required for notarization.
ENTITLEMENTS="packaging/entitlements.plist"
if [[ -n "${VN_SIGN_IDENTITY:-}" ]]; then
    echo ">> Codesigning with: ${VN_SIGN_IDENTITY}"
    if [[ ! -f "$ENTITLEMENTS" ]]; then
        echo "ERROR: $ENTITLEMENTS missing." >&2
        exit 1
    fi
    SIGNED=0
    while IFS= read -r -d '' f; do
        if file "$f" | grep -q "Mach-O"; then
            codesign --force --options runtime --timestamp \
                --entitlements "$ENTITLEMENTS" \
                --sign "$VN_SIGN_IDENTITY" "$f"
            SIGNED=$((SIGNED + 1))
        fi
    done < <(find "$APP" -type f -print0)
    echo ">> Signed ${SIGNED} nested Mach-O files"
    # Sign the bundle itself last so its seal covers the freshly-signed contents.
    codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" \
        --sign "$VN_SIGN_IDENTITY" "$APP"
    echo ">> Verifying signature..."
    codesign --verify --deep --strict --verbose=2 "$APP"
    echo ">> Signature OK"
else
    echo ">> VN_SIGN_IDENTITY not set; skipping codesign (ad-hoc/unsigned build)"
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

if [[ ! -f "$DMG_OUT" ]]; then
    echo ">> Build finished but DMG not found at expected path: $DMG_OUT" >&2
    exit 1
fi

# --- 3. Notarize + staple ---
# Auth via either a stored notarytool keychain profile (VN_NOTARY_PROFILE,
# created once with `xcrun notarytool store-credentials`) or the Apple ID trio
# (VN_NOTARY_APPLE_ID + VN_NOTARY_PASSWORD app-specific pw + VN_NOTARY_TEAM_ID).
# Skipped silently if neither is set, so unsigned local builds still finish.
if [[ -n "${VN_NOTARY_PROFILE:-}" || -n "${VN_NOTARY_APPLE_ID:-}" ]]; then
    if [[ -z "${VN_SIGN_IDENTITY:-}" ]]; then
        echo "ERROR: notarization requested but VN_SIGN_IDENTITY was not set, so the bundle is unsigned. Set the signing identity and rebuild." >&2
        exit 1
    fi
    if [[ -n "${VN_NOTARY_PROFILE:-}" ]]; then
        NOTARY_AUTH=(--keychain-profile "$VN_NOTARY_PROFILE")
    else
        NOTARY_AUTH=(--apple-id "$VN_NOTARY_APPLE_ID" --password "$VN_NOTARY_PASSWORD" --team-id "$VN_NOTARY_TEAM_ID")
    fi
    echo ">> Submitting $DMG_OUT to Apple notary service (this can take a few minutes)..."
    # notarytool exits 0 even when the verdict is Invalid, so capture the output
    # and require "status: Accepted" before stapling. A ticket only exists for an
    # accepted submission; stapling an Invalid one fails with "no ticket found"
    # and (worse) leaves you guessing why. On failure, dump the rejection log.
    SUBMIT_OUT="$(xcrun notarytool submit "$DMG_OUT" "${NOTARY_AUTH[@]}" --wait 2>&1)"
    echo "$SUBMIT_OUT"
    if echo "$SUBMIT_OUT" | grep -q "status: Accepted"; then
        echo ">> Stapling ticket to DMG..."
        xcrun stapler staple "$DMG_OUT"
        xcrun stapler validate "$DMG_OUT"
        echo ">> Notarized + stapled"
    else
        echo "ERROR: notarization did not return 'Accepted'. DMG is signed but not notarized." >&2
        SUBMISSION_ID="$(echo "$SUBMIT_OUT" | awk '/id:/{print $2; exit}')"
        [[ -n "$SUBMISSION_ID" ]] && xcrun notarytool log "$SUBMISSION_ID" "${NOTARY_AUTH[@]}" >&2 || true
        exit 1
    fi
else
    echo ">> Notary creds not set; skipping notarization."
    echo "   To notarize: set VN_NOTARY_PROFILE, or VN_NOTARY_APPLE_ID + VN_NOTARY_PASSWORD + VN_NOTARY_TEAM_ID."
fi

SIZE_MB=$(du -m "$DMG_OUT" | awk '{print $1}')
echo ">> SUCCESS"
echo "   DMG:  $DMG_OUT"
echo "   Size: ${SIZE_MB} MB"
