"""Windows auto-start via Startup folder shortcut."""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
    "Programs", "Startup"
)
_SHORTCUT_NAME = "AutoSync.vbs"
_SHORTCUT_PATH = os.path.join(_STARTUP_DIR, _SHORTCUT_NAME)


def _vbs_content():
    """Generate a VBScript that launches AutoSync without a console window."""
    python = os.path.join(_DIR, "venv", "Scripts", "pythonw.exe")
    app_path = os.path.join(_DIR, "app.py")
    return (
        f'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{_DIR}"\n'
        f'WshShell.Run """{python}"" ""{app_path}""", 0, False\n'
    )


def install():
    """Create a VBScript in the Startup folder to launch AutoSync on login."""
    try:
        os.makedirs(_STARTUP_DIR, exist_ok=True)
        with open(_SHORTCUT_PATH, "w", encoding="utf-8") as f:
            f.write(_vbs_content())
        logger.info("Windows Startup shortcut installed: %s", _SHORTCUT_PATH)
        return True
    except Exception as e:
        logger.error("Failed to install Startup shortcut: %s", e)
        return False


def uninstall():
    """Remove the AutoSync Startup shortcut."""
    if os.path.exists(_SHORTCUT_PATH):
        try:
            os.remove(_SHORTCUT_PATH)
            logger.info("Windows Startup shortcut removed: %s", _SHORTCUT_PATH)
            return True
        except Exception as e:
            logger.error("Failed to remove Startup shortcut: %s", e)
            return False
    return False


def is_installed():
    """Check if the Startup shortcut exists."""
    return os.path.exists(_SHORTCUT_PATH)
