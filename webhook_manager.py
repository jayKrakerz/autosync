"""Graph API webhook subscription management (disabled by default).

Graph API requires a publicly-accessible callback URL for subscriptions,
so this is only useful when exposed via ngrok/tunnel. Polling remains default.
"""

import logging
import time

import config as cfg

logger = logging.getLogger(__name__)


def subscribe(api_base, notification_url):
    """Create a subscription for file change notifications.

    Requires WEBHOOK_ENABLED=True and a publicly-accessible notification_url.
    """
    if not getattr(cfg, "WEBHOOK_ENABLED", False):
        logger.info("Webhooks disabled — skipping subscription")
        return None

    from onedrive_api import _request_with_retry, _resolve_drive_base
    drive_base = _resolve_drive_base(api_base)
    if not drive_base:
        logger.error("Cannot subscribe: drive base not resolved")
        return None

    # Extract driveId from drive_base URL
    # Format: .../drives/{driveId}/items/{itemId}
    parts = drive_base.split("/")
    try:
        drive_idx = parts.index("drives")
        drive_id = parts[drive_idx + 1]
    except (ValueError, IndexError):
        logger.error("Cannot parse driveId from drive base: %s", drive_base)
        return None

    body = {
        "changeType": "updated",
        "notificationUrl": notification_url,
        "resource": f"/drives/{drive_id}/root",
        "expirationDateTime": _expiry_iso(),
        "clientState": "autosync-webhook-secret",
    }

    resp = _request_with_retry("POST", f"{cfg.GRAPH_API_BASE}/subscriptions", json=body)
    if resp.status_code in (200, 201):
        data = resp.json()
        logger.info("Webhook subscription created: %s", data.get("id"))
        return data
    logger.error("Webhook subscribe failed: %s %s", resp.status_code, resp.text[:200])
    return None


def renew(subscription_id):
    """Renew an existing subscription."""
    from onedrive_api import _request_with_retry

    body = {"expirationDateTime": _expiry_iso()}
    resp = _request_with_retry(
        "PATCH",
        f"{cfg.GRAPH_API_BASE}/subscriptions/{subscription_id}",
        json=body,
    )
    if resp.status_code == 200:
        logger.info("Webhook subscription renewed: %s", subscription_id)
        return True
    logger.error("Webhook renew failed: %s %s", resp.status_code, resp.text[:200])
    return False


def handle_notification(data):
    """Process incoming webhook notification data.

    Returns list of resource URLs that changed.
    """
    changed = []
    for item in data.get("value", []):
        if item.get("clientState") != "autosync-webhook-secret":
            logger.warning("Webhook notification with invalid clientState")
            continue
        resource = item.get("resource", "")
        changed.append(resource)
        logger.info("Webhook notification for resource: %s", resource)
    return changed


def _expiry_iso():
    """Return an ISO timestamp ~3 days from now (max Graph subscription lifetime)."""
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
