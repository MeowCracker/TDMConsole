#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$ROOT_DIR/dist/TDMConsole.app"
ARTIFACT_SUFFIX="${1:-macos-$(uname -m)}"
ZIP_PATH="$ROOT_DIR/dist/TDMConsole-${ARTIFACT_SUFFIX}.app.zip"
DMG_PATH="$ROOT_DIR/dist/TDMConsole-${ARTIFACT_SUFFIX}.dmg"
ENTITLEMENTS="$ROOT_DIR/assets/entitlements.plist"
ICON="$ROOT_DIR/assets/favicon.icns"
BACKGROUND="$ROOT_DIR/assets/dmg-bg.png"
BUILD_VERSION="${TDM_BUILD_VERSION:-}"
VOLNAME="TDMConsole"
if [ -n "$BUILD_VERSION" ]; then
    VOLNAME="TDMConsole $BUILD_VERSION"
fi

if [ "$(uname)" != "Darwin" ]; then
    echo "This packaging script must run on macOS." >&2
    exit 1
fi
if [ ! -d "$APP_PATH" ]; then
    echo "Missing $APP_PATH; run 'uv run pyinstaller --clean --noconfirm tdmconsole.spec' first." >&2
    exit 1
fi
if ! command -v create-dmg >/dev/null 2>&1; then
    echo "create-dmg is required; install it with 'brew install create-dmg'." >&2
    exit 1
fi
if [ ! -f "$BACKGROUND" ]; then
    echo "Missing DMG background: $BACKGROUND" >&2
    exit 1
fi

# Remove quarantine/resource-fork metadata before applying the final ad-hoc
# hardened-runtime signature with the app's requested entitlements.
xattr -cr "$APP_PATH"
codesign \
    --force \
    --deep \
    --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign - \
    "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

rm -f "$ZIP_PATH" "$DMG_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tdmconsole-dmg.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT
ditto "$APP_PATH" "$STAGING_DIR/TDMConsole.app"

create-dmg \
    --volname "$VOLNAME" \
    --volicon "$ICON" \
    --window-pos 400 200 \
    --window-size 660 400 \
    --icon-size 100 \
    --icon "TDMConsole.app" 160 185 \
    --hide-extension "TDMConsole.app" \
    --app-drop-link 500 185 \
    --background "$BACKGROUND" \
    "$DMG_PATH" \
    "$STAGING_DIR"

echo "Created:"
echo "  $ZIP_PATH"
echo "  $DMG_PATH"
