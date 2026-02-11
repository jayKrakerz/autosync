"""In-memory API metrics and health endpoint data."""

import os
import shutil
import threading
import time

import config as cfg

_lock = threading.Lock()
_start_time = time.time()

# Rolling window of API call results: list of (timestamp, status_code)
_api_calls = []
_API_WINDOW = 300  # 5 minutes

_last_successful_sync = None


def record_api_call(status_code):
    """Record an API call result for health tracking."""
    now = time.time()
    with _lock:
        _api_calls.append((now, status_code))
        # Prune entries older than the window
        cutoff = now - _API_WINDOW
        while _api_calls and _api_calls[0][0] < cutoff:
            _api_calls.pop(0)


def record_successful_sync():
    """Record that a sync completed successfully."""
    global _last_successful_sync
    _last_successful_sync = time.time()


def get_health(token_expires_in=None):
    """Return a health status dict."""
    now = time.time()
    with _lock:
        cutoff = now - _API_WINDOW
        recent = [c for c in _api_calls if c[0] >= cutoff]
        total = len(recent)
        errors = sum(1 for _, code in recent if code >= 400)
        error_rate = (errors / total * 100) if total > 0 else 0.0

    try:
        disk = shutil.disk_usage(cfg.LOCAL_FOLDER if os.path.isdir(cfg.LOCAL_FOLDER) else "/")
        disk_free = disk.free
    except Exception:
        disk_free = None

    return {
        "token_expires_in": token_expires_in,
        "api_calls_5min": total,
        "api_error_rate_5min": round(error_rate, 1),
        "last_successful_sync": _last_successful_sync,
        "disk_free_bytes": disk_free,
        "uptime_seconds": int(now - _start_time),
    }
