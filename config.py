import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
USER_CONFIG_PATH = os.path.join(_DIR, "user_config.json")


def _load_user_config():
    """Load saved user configuration from user_config.json."""
    if os.path.exists(USER_CONFIG_PATH):
        try:
            with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_user_config(data):
    """Save user configuration to user_config.json."""
    existing = _load_user_config()
    existing.update(data)
    with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)


_DEFAULT_IGNORE = ["~$*", "*.tmp", ".DS_Store", "Thumbs.db"]

_user_cfg = _load_user_config()

# Microsoft OAuth (Auth Code Flow with PKCE)
CLIENT_ID = os.environ.get("AUTOSYNC_CLIENT_ID", _user_cfg.get("client_id", ""))
TENANT_ID = os.environ.get("AUTOSYNC_TENANT_ID", _user_cfg.get("tenant_id", "consumers"))
TOKEN_CACHE_PATH = os.path.join(_DIR, ".token_cache.json")

# OneDrive shared link (Edit permissions required for bi-directional sync)
# Priority: env var > user_config.json > empty
SHARE_LINK = os.environ.get("AUTOSYNC_SHARE_LINK", _user_cfg.get("share_link", ""))

# Local folder to sync
LOCAL_FOLDER = os.environ.get("AUTOSYNC_LOCAL_FOLDER", _user_cfg.get("local_folder", os.path.join(_DIR, "sync_folder")))

# Polling interval in seconds (default: 5 minutes)
POLL_INTERVAL = int(os.environ.get("AUTOSYNC_POLL_INTERVAL", _user_cfg.get("poll_interval", 300)))

# Path to sync state database
STATE_DB_PATH = os.environ.get("AUTOSYNC_STATE_DB", os.path.join(_DIR, "sync_state.json"))

# Suffix added to conflicting local files
CONFLICT_SUFFIX = "_CONFLICT"

# Graph API base URL
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Upload chunk size for large files (10 MB)
UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024

# Simple upload threshold (4 MB)
SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1  # seconds

# Debounce window — ignore watcher events for files synced within this many seconds
DEBOUNCE_SECONDS = 3
DEBOUNCE_EXPIRY_SECONDS = 5

# File ignore patterns (fnmatch-style)
IGNORE_PATTERNS = _user_cfg.get("ignore_patterns", _DEFAULT_IGNORE)

# Parallel transfer workers
MAX_WORKERS = int(_user_cfg.get("max_workers", 4))

# Selective sync — subfolder include/exclude (empty = sync all)
SYNC_FOLDERS = _user_cfg.get("sync_folders", [])
EXCLUDE_FOLDERS = _user_cfg.get("exclude_folders", [])

# Desktop notifications
NOTIFICATIONS_ENABLED = _user_cfg.get("notifications_enabled", True)

# Webhook subscriptions (disabled by default — requires public URL)
WEBHOOK_ENABLED = _user_cfg.get("webhook_enabled", False)
WEBHOOK_URL = _user_cfg.get("webhook_url", "")


def reload_config():
    """Reload user configuration from user_config.json at runtime."""
    global SHARE_LINK, LOCAL_FOLDER, POLL_INTERVAL, CLIENT_ID, TENANT_ID
    global IGNORE_PATTERNS, MAX_WORKERS, SYNC_FOLDERS, EXCLUDE_FOLDERS
    global NOTIFICATIONS_ENABLED, WEBHOOK_ENABLED, WEBHOOK_URL
    fresh = _load_user_config()
    SHARE_LINK = os.environ.get("AUTOSYNC_SHARE_LINK", fresh.get("share_link", ""))
    LOCAL_FOLDER = os.environ.get(
        "AUTOSYNC_LOCAL_FOLDER",
        fresh.get("local_folder", os.path.join(_DIR, "sync_folder")),
    )
    POLL_INTERVAL = int(
        os.environ.get("AUTOSYNC_POLL_INTERVAL", fresh.get("poll_interval", 300))
    )
    CLIENT_ID = os.environ.get("AUTOSYNC_CLIENT_ID", fresh.get("client_id", ""))
    TENANT_ID = os.environ.get("AUTOSYNC_TENANT_ID", fresh.get("tenant_id", "consumers"))
    IGNORE_PATTERNS = fresh.get("ignore_patterns", _DEFAULT_IGNORE)
    MAX_WORKERS = int(fresh.get("max_workers", 4))
    SYNC_FOLDERS = fresh.get("sync_folders", [])
    EXCLUDE_FOLDERS = fresh.get("exclude_folders", [])
    NOTIFICATIONS_ENABLED = fresh.get("notifications_enabled", True)
    WEBHOOK_ENABLED = fresh.get("webhook_enabled", False)
    WEBHOOK_URL = fresh.get("webhook_url", "")
