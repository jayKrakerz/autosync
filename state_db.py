import json
import os
import logging
import shutil
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def load_state(path):
    """Load sync state from JSON file. Returns empty state if file doesn't exist or is corrupt."""
    if not os.path.exists(path):
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if "files" not in state:
            state["files"] = {}
        if "last_poll" not in state:
            state["last_poll"] = None
        if "retry_queue" not in state:
            state["retry_queue"] = []
        if "delta_link" not in state:
            state["delta_link"] = None
        return state
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("State DB corrupt: %s — backing up and starting fresh", e)
        backup = path + ".corrupt." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        shutil.copy2(path, backup)
        logger.info("Corrupt state backed up to %s", backup)
        return _empty_state()


def save_state(state, path):
    """Atomically write state to JSON file via temp file + os.replace()."""
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_file_entry(state, rel_path):
    """Get the state entry for a file, or None if not tracked."""
    return state["files"].get(rel_path)


def set_file_entry(state, rel_path, size, local_mtime, remote_mtime,
                   local_hash=None, remote_hash=None):
    """Create or update a file entry in state."""
    entry = {
        "size": size,
        "local_mtime": local_mtime,
        "remote_mtime": remote_mtime,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    if local_hash is not None:
        entry["local_hash"] = local_hash
    if remote_hash is not None:
        entry["remote_hash"] = remote_hash
    state["files"][rel_path] = entry


def remove_file_entry(state, rel_path):
    """Remove a file entry from state."""
    state["files"].pop(rel_path, None)


def add_retry(state, path, action, error=""):
    """Add a failed operation to the retry queue."""
    queue = state.setdefault("retry_queue", [])
    # Update existing entry or add new
    for item in queue:
        if item["path"] == path and item["action"] == action:
            item["attempts"] = item.get("attempts", 0) + 1
            item["error"] = error
            item["next_retry"] = _next_retry_time(item["attempts"])
            return
    queue.append({
        "path": path,
        "action": action,
        "attempts": 1,
        "next_retry": _next_retry_time(1),
        "error": error,
    })


def remove_retry(state, path, action):
    """Remove an item from the retry queue on success."""
    queue = state.get("retry_queue", [])
    state["retry_queue"] = [
        item for item in queue
        if not (item["path"] == path and item["action"] == action)
    ]


def _next_retry_time(attempts):
    """Calculate next retry time with exponential backoff."""
    delay = min(2 ** attempts * 30, 1800)  # 30s, 60s, 120s, ... max 30 min
    return datetime.now(timezone.utc).timestamp() + delay


def _empty_state():
    return {"files": {}, "last_poll": None, "retry_queue": [], "delta_link": None}
