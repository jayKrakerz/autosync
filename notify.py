"""Cross-platform desktop notifications (macOS + Windows)."""

import logging
import platform
import subprocess

import config as cfg

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()


def _send(title, message):
    """Send a desktop notification."""
    if not getattr(cfg, "NOTIFICATIONS_ENABLED", True):
        return
    try:
        if _SYSTEM == "Darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif _SYSTEM == "Windows":
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$textNodes = $template.GetElementsByTagName('text'); "
                f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null; "
                f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AutoSync').Show($toast)"
            )
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            logger.debug("Notifications not supported on %s", _SYSTEM)
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
