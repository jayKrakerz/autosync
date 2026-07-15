"""Windows auto-start via Startup folder shortcut."""

import logging
import os
import sys

import config as cfg

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
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}"'
        working_dir = cfg.DATA_DIR
    else:
        python = os.path.join(_DIR, "venv", "Scripts", "pythonw.exe")
        app_path = os.path.join(_DIR, "app.py")
        command = f'"{python}" "{app_path}"'
        working_dir = _DIR
    command = command.replace('"', '""')
    return (
        f'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{working_dir}"\n'
        f'WshShell.Run "{command}", 0, False\n'
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
