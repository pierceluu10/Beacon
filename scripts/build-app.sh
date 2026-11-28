#!/bin/bash
# Builds dist/Beacon.app and dist/Beacon.dmg for Apple silicon: the Swift menu bar app as the executable, a standalone
# Python with Beacon's dependencies, and node with the Playwright MCP server, so nothing else needs installing.
# Needs Xcode's command line tools and uv. Downloads are pinned and checked against their published SHA-256.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_SHA256="fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e"
NODE_VERSION="v24.22.2"
NODE_URL="https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-darwin-arm64.tar.gz"
NODE_SHA256="aa9549349e47dffaa42dcf95d6e06ade8c4bdafc980979883b88767670df385a"
PLAYWRIGHT_MCP=$(sed -n 's/^PLAYWRIGHT_MCP = "\(.*\)"$/\1/p' agent.py)  # the one version agent.py runs from a terminal
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
BUNDLE_ID="io.github.pierceluu10.beacon"
CACHE=build
APP=dist/Beacon.app
RESOURCES=$APP/Contents/Resources
ICON_SIZES=(16 32 128 256 512)

fetch() {  # url, sha256, file: downloaded once into the cache
    local file="$CACHE/$3"
    [ -f "$file" ] || curl -fsSL -o "$file" "$1"
    echo "$2  $file" | shasum -a 256 -c - >/dev/null
}

rm -rf dist
mkdir -p "$CACHE" "$APP/Contents/MacOS" "$RESOURCES/beacon"

echo "Python and Beacon's dependencies"
fetch "$PYTHON_URL" "$PYTHON_SHA256" python.tar.gz
tar -xzf "$CACHE/python.tar.gz" -C "$RESOURCES"
uv export --frozen --no-dev --no-hashes --no-emit-project --quiet > "$CACHE/requirements.txt"
uv pip install --quiet --python "$RESOURCES/python/bin/python3" --system --break-system-packages -r "$CACHE/requirements.txt"

echo "node and $PLAYWRIGHT_MCP"
fetch "$NODE_URL" "$NODE_SHA256" node.tar.gz
tar -xzf "$CACHE/node.tar.gz" -C "$CACHE"
rm -rf "$RESOURCES/node"
mkdir -p "$RESOURCES/node/bin"
cp "$CACHE/node-$NODE_VERSION-darwin-arm64/bin/node" "$RESOURCES/node/bin/"
PATH="$CACHE/node-$NODE_VERSION-darwin-arm64/bin:$PATH" npm install --silent --global --prefix "$RESOURCES/node" "$PLAYWRIGHT_MCP"
rm -rf "$RESOURCES/node/bin/playwright-mcp"  # a symlink into the package; the app runs cli.js with node directly

echo "Beacon"
cp ./*.py ./*.js prompt.txt mac_prompt.txt mark.png "$RESOURCES/beacon/"
cp mark.png "$RESOURCES/"
swiftc -O app/*.swift -o "$APP/Contents/MacOS/Beacon"

iconset="$CACHE/Beacon.iconset"
rm -rf "$iconset" && mkdir "$iconset"
for size in "${ICON_SIZES[@]}"; do
    sips -z "$size" "$size" logo.png --out "$iconset/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) logo.png --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$RESOURCES/Beacon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleName</key><string>Beacon</string>
    <key>CFBundleExecutable</key><string>Beacon</string>
    <key>CFBundleIconFile</key><string>Beacon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Beacon listens for its name and for what you ask it to do.</string>
    <key>NSAppleEventsUsageDescription</key><string>Beacon uses your apps when you ask it to, like Notes, Music, Messages and Mail.</string>
    <key>NSCalendarsFullAccessUsageDescription</key><string>Beacon reads and adds calendar events when you ask it to.</string>
    <key>NSRemindersFullAccessUsageDescription</key><string>Beacon reads and adds reminders when you ask it to.</string>
</dict>
</plist>
PLIST

echo "Signing (ad hoc)"
find "$RESOURCES" -type f \( -name "*.so" -o -name "*.dylib" -o -path "*/bin/python3.12" -o -path "*/bin/node" \) \
    -exec codesign --force --sign - {} + 2>/dev/null  # each prints "replacing existing signature"
codesign --force --sign - "$APP"

echo "Disk image"
staging="$CACHE/dmg"
rm -rf "$staging" && mkdir "$staging"
cp -R "$APP" "$staging/"
ln -s /Applications "$staging/Applications"
hdiutil create -quiet -volname Beacon -srcfolder "$staging" -ov -format UDZO dist/Beacon.dmg
du -sh "$APP" dist/Beacon.dmg
