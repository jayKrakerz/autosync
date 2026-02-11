"""macOS desktop notifications via osascript."""

import logging
import subprocess

import config as cfg

logger = logging.getLogger(__name__)


def _send(title, message):
    """Send a macOS notification using osascript."""
    if not getattr(cfg, "NOTIFICATIONS_ENABLED", True):
        return
    try:
        script = (
            f'display notification "{message}" with title "{title}"'
        )
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.debug("Notification failed: %s", e)


def notify_sync_complete(count):
    """Notify user that a sync cycle finished."""
    _send("AutoSync", f"Sync complete — {count} file(s) processed.")


def notify_conflict(path):
    """Notify user about a file conflict."""
    _send("AutoSync — Conflict", f"Conflict detected: {path}")


def notify_error(message):
    """Notify user about a sync error."""
    _send("AutoSync — Error", message)
