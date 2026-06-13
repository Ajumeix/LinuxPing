#!/usr/bin/env bash
set -e

APP_NAME="LinuxPing"
APP_ID="linuxping"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$APP_DIR/run_linuxping.sh"
ICON_SRC="$APP_DIR/linuxping.svg"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_FILE="$DESKTOP_DIR/${APP_ID}.desktop"
ICON_DEST="$ICON_DIR/${APP_ID}.svg"

chmod +x "$APP_DIR"/*.sh "$APP_DIR/linuxping.py"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
cp "$ICON_SRC" "$ICON_DEST"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Visual multi-ping monitor with outage tracking
Exec=$RUNNER
Icon=$APP_ID
Terminal=false
Categories=Network;Utility;Monitor;
StartupNotify=true
DESKTOP

chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "LinuxPing app launcher installed."
echo "Open it from your app menu by searching: LinuxPing"
echo "Desktop launcher file: $DESKTOP_FILE"
