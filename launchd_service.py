"""macOS LaunchAgent install/uninstall for AutoSync auto-start."""

import logging
import os
import subprocess
import sys

import config as cfg

logger = logging.getLogger(__name__)

PLIST_LABEL = "com.riskarena.autosync"
PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(PLIST_DIR, f"{PLIST_LABEL}.plist")

_DIR = os.path.dirname(os.path.abspath(__file__))


def _program_arguments():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(_DIR, "app.py")]


def _build_plist():
    """Generate the LaunchAgent plist XML."""
    args = "\n".join(f"        <string>{arg}</string>" for arg in _program_arguments())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>WorkingDirectory</key>
    <string>{cfg.DATA_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{os.path.join(cfg.DATA_DIR, "autosync_stdout.log")}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.join(cfg.DATA_DIR, "autosync_stderr.log")}</string>
</dict>
</plist>"""


def install():
    """Write the LaunchAgent plist and load it."""
    os.makedirs(PLIST_DIR, exist_ok=True)
    plist_content = _build_plist()
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(plist_content)
    try:
        subprocess.run(["launchctl", "load", PLIST_PATH], check=True,
                       capture_output=True, text=True)
        logger.info("LaunchAgent installed and loaded: %s", PLIST_PATH)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Failed to load LaunchAgent: %s", e.stderr)
        return False


def uninstall():
    """Unload and remove the LaunchAgent plist."""
    if os.path.exists(PLIST_PATH):
        try:
            subprocess.run(["launchctl", "unload", PLIST_PATH], check=True,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logger.warning("launchctl unload: %s", e.stderr)
        os.remove(PLIST_PATH)
        logger.info("LaunchAgent uninstalled: %s", PLIST_PATH)
        return True
    return False


def is_installed():
    """Check if the LaunchAgent plist exists."""
    return os.path.exists(PLIST_PATH)
