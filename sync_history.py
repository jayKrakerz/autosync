"""Append-only JSONL sync history with rotation."""

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(_DIR, "sync_history.jsonl")
MAX_ENTRIES = 1000
_lock = threading.Lock()


def log_event(action, path, status, size=None, duration_ms=None, error=None):
    """Append a sync event to the history file."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "path": path,
        "status": status,
        "size": size,
        "duration_ms": duration_ms,
        "error": error,
    }
    with _lock:
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            _rotate_if_needed()
        except Exception as e:
            logger.debug("Failed to write history: %s", e)


def get_history(limit=50, offset=0):
    """Read recent history entries (newest first)."""
    if not os.path.exists(HISTORY_PATH):
        return []
    with _lock:
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return []

    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return entries[offset : offset + limit]


def _rotate_if_needed():
    """Keep only the last MAX_ENTRIES lines."""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_ENTRIES:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_ENTRIES:])
    except Exception:
        pass
