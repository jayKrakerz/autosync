#!/bin/bash
# ============================================================================
# AutoSync Installer — RiskArena Brokerage Services
# Run: curl -fsSL https://raw.githubusercontent.com/jayKrakerz/autosync/master/install.sh | bash
# ============================================================================
set -e

REPO="https://github.com/jayKrakerz/autosync.git"
INSTALL_DIR="$HOME/.autosync"
PLIST_LABEL="com.riskarena.autosync"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
PORT=8050

# Pre-configured Azure App Registration (shared across the company)
CLIENT_ID="c4dca575-9641-440e-b2cc-08c4f191698d"
TENANT_ID="0716b81d-2dfc-4e22-a250-3c77832c1b0e"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     AutoSync Installer — RiskArena   ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# -------------------------------------------------------------------
# 1. Check for Python 3
# -------------------------------------------------------------------
if command -v python3 &>/dev/null; then
    PY=$(command -v python3)
else
    echo "ERROR: Python 3 is required. Install it from https://www.python.org/downloads/"
    exit 1
fi
echo "[1/6] Python found: $PY ($($PY --version 2>&1))"

# -------------------------------------------------------------------
# 2. Clone or update repo
# -------------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[2/6] Updating existing installation..."
    git -C "$INSTALL_DIR" pull --quiet
else
    echo "[2/6] Installing to $INSTALL_DIR ..."
    git clone --quiet "$REPO" "$INSTALL_DIR"
fi

# -------------------------------------------------------------------
# 3. Create virtual environment & install dependencies
# -------------------------------------------------------------------
echo "[3/6] Setting up Python environment..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    $PY -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# -------------------------------------------------------------------
# 4. Pre-configure Azure App (so users don't have to enter it)
# -------------------------------------------------------------------
echo "[4/6] Configuring app..."
CONFIG_FILE="$INSTALL_DIR/user_config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << CONF
{
  "client_id": "$CLIENT_ID",
  "tenant_id": "$TENANT_ID",
  "local_folder": "$HOME/OneDrive Sync",
  "poll_interval": 300
}
CONF
else
    echo "       Existing config found, keeping it."
fi

# -------------------------------------------------------------------
# 5. Install LaunchAgent (auto-start on login)
# -------------------------------------------------------------------
echo "[5/6] Setting up auto-start..."
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/venv/bin/python</string>
        <string>${INSTALL_DIR}/app.py</string>
        <string>--no-gui</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/autosync.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/autosync_err.log</string>
</dict>
</plist>
PLIST
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

# -------------------------------------------------------------------
# 6. Wait for server and open browser
# -------------------------------------------------------------------
echo "[6/6] Starting AutoSync..."
for i in $(seq 1 15); do
    if curl -s "http://localhost:$PORT/api/health" &>/dev/null; then
        break
    fi
    sleep 1
done

echo ""
echo "  ✅ AutoSync installed successfully!"
echo ""
echo "  Dashboard:  http://localhost:$PORT"
echo "  Sync folder: ~/OneDrive Sync"
echo "  Logs:        $INSTALL_DIR/autosync.log"
echo ""
echo "  Next steps:"
echo "    1. Your browser will open the dashboard"
echo "    2. Click 'Sign in with Microsoft' and log in with your work account"
echo "    3. Paste your OneDrive shared folder link in Settings"
echo "    4. Click Start Sync"
echo ""
echo "  AutoSync will start automatically on login."
echo "  Opening app window now..."
echo ""

# Launch native app window (server is already running via LaunchAgent)
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app.py" &
