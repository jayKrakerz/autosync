#!/bin/bash
# ============================================================================
# AutoSync Uninstaller — RiskArena Brokerage Services
# ============================================================================
set -e

INSTALL_DIR="$HOME/.autosync"
PLIST_LABEL="com.riskarena.autosync"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

echo ""
echo "  Uninstalling AutoSync..."
echo ""

# Stop the service
if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "  ✓ LaunchAgent removed"
fi

# Remove installation
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "  ✓ Installation removed ($INSTALL_DIR)"
fi

echo ""
echo "  ✅ AutoSync uninstalled."
echo "  Note: Your synced files in ~/OneDrive Sync were NOT deleted."
echo ""
