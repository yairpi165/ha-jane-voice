#!/bin/bash
# Deploy Jane source code to addon/ for HAOS packaging

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR="$SCRIPT_DIR/addon"
SRC_DIR="$SCRIPT_DIR/src"
CONFIG_DIR="$SCRIPT_DIR/config"

echo "📦 Copying source files to addon/..."
cp "$SRC_DIR"/brain.py "$ADDON_DIR/"
cp "$SRC_DIR"/ha_client.py "$ADDON_DIR/"
cp "$SRC_DIR"/memory.py "$ADDON_DIR/"
cp "$SRC_DIR"/web_api.py "$ADDON_DIR/"
cp "$CONFIG_DIR"/config_addon.py "$ADDON_DIR"/config.py

echo "✅ Addon ready — copy addon/ to Pi via Samba"
