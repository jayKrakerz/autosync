import concurrent.futures
import fnmatch
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone

import config as cfg
from onedrive_api import (
    get_api_base,
    list_remote_files,
    list_remote_changes,
    download_file,
    upload_file,
    delete_remote,
)
from state_db import (
    load_state,
    save_state,
    get_file_entry,
    set_file_entry,
    remove_file_entry,
    add_retry,
    remove_retry,
)

logger = logging.getLogger(__name__)

# Tracks recently synced files to prevent watcher feedback loops
recently_synced = {}
_recently_synced_lock = threading.Lock()

# Lock for thread-safe state mutations during parallel transfers
_state_lock = threading.Lock()

# Progress tracking — set by sync_manager, read by app.py /api/status
current_op = {"file": None, "action": None, "progress_pct": 0,
              "bytes_done": 0, "bytes_total": 0}
_op_lock = threading.Lock()


def _set_progress(file, action, bytes_done=0, bytes_total=0):
    with _op_lock:
        current_op["file"] = file
        current_op["action"] = action
        current_op["bytes_done"] = bytes_done
        current_op["bytes_total"] = bytes_total
        current_op["progress_pct"] = (
            int(bytes_done / bytes_total * 100) if bytes_total > 0 else 0
        )


def _clear_progress():
    with _op_lock:
        current_op["file"] = None
        current_op["action"] = None
        current_op["progress_pct"] = 0
        current_op["bytes_done"] = 0
        current_op["bytes_total"] = 0


def get_current_op():
    with _op_lock:
        return dict(current_op)


def mark_recently_synced(rel_path):
    """Record that a file was just synced (downloaded), so the watcher should ignore it."""
    with _recently_synced_lock:
        recently_synced[rel_path] = time.time()


def is_recently_synced(rel_path):
    """Check if a file was synced within the debounce window."""
    with _recently_synced_lock:
        ts = recently_synced.get(rel_path)
        if ts is None:
            return False
        if time.time() - ts < cfg.DEBOUNCE_SECONDS:
            return True
        return False


def cleanup_recently_synced():
    """Remove expired entries from recently_synced."""
    with _recently_synced_lock:
        now = time.time()
        expired = [k for k, v in recently_synced.items() if now - v > cfg.DEBOUNCE_EXPIRY_SECONDS]
        for k in expired:
            del recently_synced[k]


def _compute_local_hash(file_path):
    """Compute SHA-256 hash of a local file."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _should_ignore(rel_path):
    """Check if a file path should be ignored by sync using fnmatch patterns."""
    basename = os.path.basename(rel_path)
    # Always ignore state files
    if basename == "sync_state.json" or basename.startswith(".sync_state"):
        return True
    for pattern in cfg.IGNORE_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            return True
    return False


def _is_in_sync_scope(rel_path):
    """Check if path is within selective sync scope (include/exclude folders)."""
    # Check exclude folders first
    for excl in cfg.EXCLUDE_FOLDERS:
        excl = excl.strip().strip("/")
        if not excl:
            continue
        if rel_path == excl or rel_path.startswith(excl + "/"):
            return False
    # Check include folders (empty = include all)
    if not cfg.SYNC_FOLDERS:
        return True
    for incl in cfg.SYNC_FOLDERS:
        incl = incl.strip().strip("/")
        if not incl:
            continue
        if rel_path == incl or rel_path.startswith(incl + "/"):
            return True
    return False


def _log_history(action, path, status, size=None, duration_ms=None, error=None):
    """Log a sync event to history."""
    try:
        import sync_history
        sync_history.log_event(action, path, status, size, duration_ms, error)
    except Exception:
        pass


def full_sync(api_base, progress_callback=None):
    """Perform a full bi-directional sync between local folder and OneDrive.

    Returns (synced_count, error_count) tuple.
    """
    logger.info("Starting full sync...")
    cleanup_recently_synced()

    state = load_state(cfg.STATE_DB_PATH)

    # Process retry queue first
    _process_retry_queue(api_base, state)

    # 1. List remote files
    try:
        remote_files_list = list_remote_files(api_base)
    except Exception as e:
        logger.error("Failed to list remote files: %s", e)
        return (0, 1)
    remote_files = {f["path"]: f for f in remote_files_list}

    # 2. List local files
    local_files = {}
    for root, _dirs, filenames in os.walk(cfg.LOCAL_FOLDER):
        for fname in filenames:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, cfg.LOCAL_FOLDER)
            rel_path = rel_path.replace(os.sep, "/")
            if _should_ignore(rel_path):
                continue
            if not _is_in_sync_scope(rel_path):
                continue
            try:
                stat = os.stat(full_path)
                local_files[rel_path] = {
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            except OSError as e:
                logger.warning("Cannot stat %s: %s", full_path, e)

    # 3. Determine the union of all known paths
    all_paths = set()
    all_paths.update(remote_files.keys())
    all_paths.update(local_files.keys())
    all_paths.update(state["files"].keys())

    # Filter by sync scope
    all_paths = {p for p in all_paths if _is_in_sync_scope(p)}

    # Build list of actions for parallel execution
    actions = []
    for rel_path in sorted(all_paths):
        in_remote = rel_path in remote_files
        in_local = rel_path in local_files
        in_state = rel_path in state["files"]

        if in_remote and in_local and in_state:
            actions.append(("sync_existing", rel_path, remote_files[rel_path], local_files[rel_path]))
        elif in_remote and in_state and not in_local:
            actions.append(("local_deleted", rel_path, None, None))
        elif in_local and in_state and not in_remote:
            actions.append(("remote_deleted", rel_path, None, None))
        elif in_remote and not in_state:
            actions.append(("download_new", rel_path, remote_files[rel_path], None))
        elif in_local and not in_state:
            actions.append(("upload_new", rel_path, None, local_files[rel_path]))

    synced_count = 0
    error_count = 0

    def _exec_action(action_tuple):
        action_type, rel_path, remote_info, local_info = action_tuple
        t0 = time.time()
        try:
            if action_type == "sync_existing":
                _sync_existing(api_base, state, rel_path, remote_info, local_info)
            elif action_type == "local_deleted":
                _handle_local_deleted_during_poll(api_base, state, rel_path)
            elif action_type == "remote_deleted":
                _handle_remote_deleted_during_poll(state, rel_path)
            elif action_type == "download_new":
                _download_new(api_base, state, rel_path, remote_info)
            elif action_type == "upload_new":
                _upload_new(api_base, state, rel_path, local_info)
            duration = int((time.time() - t0) * 1000)
            _log_history(action_type, rel_path, "ok", duration_ms=duration)
            return True
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            logger.error("Error syncing %s: %s", rel_path, e)
            _log_history(action_type, rel_path, "error", duration_ms=duration, error=str(e))
            # Add to retry queue
            with _state_lock:
                add_retry(state, rel_path, action_type, str(e))
            return False

    # Execute actions in parallel
    max_workers = max(1, cfg.MAX_WORKERS)
    if len(actions) <= 1:
        # No point in threading for 0 or 1 action
        for action_tuple in actions:
            if _exec_action(action_tuple):
                synced_count += 1
            else:
                error_count += 1
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_exec_action, a): a for a in actions}
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    synced_count += 1
                else:
                    error_count += 1

    state["last_poll"] = datetime.now(timezone.utc).isoformat()
    save_state(state, cfg.STATE_DB_PATH)
    _clear_progress()
    logger.info("Full sync complete: %d processed, %d errors", synced_count, error_count)
    return (synced_count, error_count)


def delta_sync(api_base):
    """Perform an incremental sync using Graph API delta.

    Falls back to full_sync if delta fails or no delta link exists.
    Returns (synced_count, error_count) tuple.
    """
    state = load_state(cfg.STATE_DB_PATH)
    delta_link = state.get("delta_link")

    if not delta_link:
        logger.info("No delta link — falling back to full sync")
        return full_sync(api_base)

    try:
        changes, new_delta_link = list_remote_changes(api_base, delta_link)
    except Exception as e:
        logger.warning("Delta query failed (%s) — falling back to full sync", e)
        return full_sync(api_base)

    if new_delta_link is None:
        logger.warning("Delta returned no new link — falling back to full sync")
        return full_sync(api_base)

    logger.info("Delta sync: %d changes", len(changes))
    cleanup_recently_synced()

    synced_count = 0
    error_count = 0

    for change in changes:
        rel_path = change["path"]
        if not rel_path or change.get("is_folder"):
            continue
        if _should_ignore(rel_path):
            continue
        if not _is_in_sync_scope(rel_path):
            continue

        t0 = time.time()
        try:
            if change["deleted"]:
                # Remote deleted
                local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
                if os.path.exists(local_path):
                    os.remove(local_path)
                    logger.info("Delta: remote delete → removed local: %s", rel_path)
                with _state_lock:
                    remove_file_entry(state, rel_path)
                _log_history("delete", rel_path, "ok",
                             duration_ms=int((time.time() - t0) * 1000))
            else:
                # Remote created/modified → download
                local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
                in_state = rel_path in state["files"]

                if in_state:
                    # Check if local also changed (conflict)
                    entry = state["files"][rel_path]
                    if os.path.exists(local_path):
                        stat = os.stat(local_path)
                        local_mtime = datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat()
                        if local_mtime != entry.get("local_mtime", ""):
                            # Both changed — conflict
                            _handle_conflict_delta(api_base, state, rel_path, change)
                            _log_history("conflict", rel_path, "ok",
                                         duration_ms=int((time.time() - t0) * 1000))
                            synced_count += 1
                            continue

                # Download remote version
                mark_recently_synced(rel_path)
                _set_progress(rel_path, "download")
                if download_file(api_base, rel_path, local_path,
                                 progress_cb=lambda done, total: _set_progress(rel_path, "download", done, total)):
                    stat = os.stat(local_path)
                    new_local_mtime = datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat()
                    with _state_lock:
                        set_file_entry(state, rel_path, change["size"],
                                       new_local_mtime, change["lastModifiedDateTime"],
                                       remote_hash=change.get("remote_hash"))
                    _log_history("download", rel_path, "ok",
                                 size=change["size"],
                                 duration_ms=int((time.time() - t0) * 1000))
                _clear_progress()

            synced_count += 1
        except Exception as e:
            logger.error("Delta sync error for %s: %s", rel_path, e)
            _log_history("delta_error", rel_path, "error",
                         duration_ms=int((time.time() - t0) * 1000), error=str(e))
            error_count += 1

    state["delta_link"] = new_delta_link
    state["last_poll"] = datetime.now(timezone.utc).isoformat()
    save_state(state, cfg.STATE_DB_PATH)
    _clear_progress()
    logger.info("Delta sync complete: %d processed, %d errors", synced_count, error_count)
    return (synced_count, error_count)


def _process_retry_queue(api_base, state):
    """Process items in the retry queue with exponential backoff."""
    queue = state.get("retry_queue", [])
    if not queue:
        return

    now = datetime.now(timezone.utc).timestamp()
    remaining = []
    for item in queue:
        if item.get("attempts", 0) >= 5:
            logger.warning("Retry queue: giving up on %s (%s) after 5 attempts",
                           item["path"], item["action"])
            _log_history(item["action"], item["path"], "retry_failed", error=item.get("error"))
            continue
        if item.get("next_retry", 0) > now:
            remaining.append(item)
            continue

        logger.info("Retry queue: retrying %s (%s), attempt %d",
                    item["path"], item["action"], item.get("attempts", 0) + 1)
        try:
            local_path = os.path.join(cfg.LOCAL_FOLDER, item["path"])
            if item["action"] in ("upload_new", "upload"):
                if os.path.isfile(local_path):
                    result = upload_file(api_base, item["path"], local_path)
                    if result:
                        stat = os.stat(local_path)
                        local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                        remote_mtime = result.get("lastModifiedDateTime", "")
                        set_file_entry(state, item["path"], stat.st_size, local_mtime, remote_mtime)
                        logger.info("Retry succeeded: uploaded %s", item["path"])
                        _log_history("retry_upload", item["path"], "ok")
                        continue
            elif item["action"] in ("download_new", "download"):
                if download_file(api_base, item["path"], local_path):
                    logger.info("Retry succeeded: downloaded %s", item["path"])
                    _log_history("retry_download", item["path"], "ok")
                    continue
            elif item["action"] in ("local_deleted", "delete"):
                if delete_remote(api_base, item["path"]):
                    remove_file_entry(state, item["path"])
                    logger.info("Retry succeeded: deleted remote %s", item["path"])
                    _log_history("retry_delete", item["path"], "ok")
                    continue
            # If we got here, the action wasn't successful
            item["attempts"] = item.get("attempts", 0) + 1
            item["next_retry"] = datetime.now(timezone.utc).timestamp() + min(2 ** item["attempts"] * 30, 1800)
            remaining.append(item)
        except Exception as e:
            logger.warning("Retry failed for %s: %s", item["path"], e)
            item["attempts"] = item.get("attempts", 0) + 1
            item["error"] = str(e)
            item["next_retry"] = datetime.now(timezone.utc).timestamp() + min(2 ** item["attempts"] * 30, 1800)
            remaining.append(item)

    state["retry_queue"] = remaining


def _sync_existing(api_base, state, rel_path, remote_info, local_info):
    """Handle a file that exists in remote, local, AND state."""
    with _state_lock:
        entry = state["files"][rel_path]
    state_remote_mtime = entry.get("remote_mtime", "")
    state_local_mtime = entry.get("local_mtime", "")

    remote_mtime = remote_info["lastModifiedDateTime"]
    local_mtime = local_info["mtime"]

    remote_changed = remote_mtime != state_remote_mtime
    local_changed = local_mtime != state_local_mtime

    # Hash-based skip: if hashes match, skip transfer even if mtime differs
    if (remote_changed or local_changed) and not (remote_changed and local_changed):
        remote_hash = remote_info.get("remote_hash", "")
        if remote_hash and remote_hash == entry.get("remote_hash", ""):
            # Remote hash unchanged — likely just a timezone/touch difference
            local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
            local_hash = _compute_local_hash(local_path)
            if local_hash and local_hash == entry.get("local_hash", ""):
                logger.debug("Hash match, skipping transfer: %s", rel_path)
                with _state_lock:
                    set_file_entry(state, rel_path, remote_info["size"],
                                   local_mtime, remote_mtime,
                                   local_hash=local_hash, remote_hash=remote_hash)
                return

    if remote_changed and local_changed:
        _handle_conflict(api_base, state, rel_path, remote_info, local_info)
    elif remote_changed and not local_changed:
        local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
        mark_recently_synced(rel_path)
        _set_progress(rel_path, "download")
        if download_file(api_base, rel_path, local_path,
                         progress_cb=lambda done, total: _set_progress(rel_path, "download", done, total)):
            stat = os.stat(local_path)
            new_local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            local_hash = _compute_local_hash(local_path)
            with _state_lock:
                set_file_entry(state, rel_path, remote_info["size"], new_local_mtime, remote_mtime,
                               local_hash=local_hash, remote_hash=remote_info.get("remote_hash"))
            logger.info("Pulled remote change: %s", rel_path)
        _clear_progress()
    elif local_changed and not remote_changed:
        local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
        _set_progress(rel_path, "upload")
        result = upload_file(api_base, rel_path, local_path,
                             progress_cb=lambda done, total: _set_progress(rel_path, "upload", done, total))
        if result:
            new_remote_mtime = result.get("lastModifiedDateTime", remote_mtime)
            local_hash = _compute_local_hash(local_path)
            remote_hash = ""
            hashes = result.get("file", {}).get("hashes", {})
            if hashes:
                remote_hash = hashes.get("sha256Hash") or hashes.get("quickXorHash") or ""
            with _state_lock:
                set_file_entry(state, rel_path, local_info["size"], local_mtime, new_remote_mtime,
                               local_hash=local_hash, remote_hash=remote_hash)
            logger.info("Pushed local change: %s", rel_path)
        _clear_progress()


def _handle_conflict(api_base, state, rel_path, remote_info, local_info):
    """Handle conflict: rename local file with CONFLICT suffix, download remote version."""
    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(rel_path)
    conflict_rel = f"{base}{cfg.CONFLICT_SUFFIX}_{timestamp}{ext}"
    conflict_path = os.path.join(cfg.LOCAL_FOLDER, conflict_rel)

    os.makedirs(os.path.dirname(conflict_path), exist_ok=True)
    os.rename(local_path, conflict_path)
    logger.warning("CONFLICT: %s — local version saved as %s", rel_path, conflict_rel)
    _log_history("conflict", rel_path, "ok")

    # Send notification
    try:
        import notify
        notify.notify_conflict(rel_path)
    except Exception:
        pass

    mark_recently_synced(rel_path)
    if download_file(api_base, rel_path, local_path):
        stat = os.stat(local_path)
        new_local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        local_hash = _compute_local_hash(local_path)
        with _state_lock:
            set_file_entry(state, rel_path, remote_info["size"], new_local_mtime,
                           remote_info["lastModifiedDateTime"],
                           local_hash=local_hash, remote_hash=remote_info.get("remote_hash"))


def _handle_conflict_delta(api_base, state, rel_path, change):
    """Handle conflict during delta sync."""
    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(rel_path)
    conflict_rel = f"{base}{cfg.CONFLICT_SUFFIX}_{timestamp}{ext}"
    conflict_path = os.path.join(cfg.LOCAL_FOLDER, conflict_rel)

    os.makedirs(os.path.dirname(conflict_path), exist_ok=True)
    os.rename(local_path, conflict_path)
    logger.warning("CONFLICT (delta): %s — local version saved as %s", rel_path, conflict_rel)

    try:
        import notify
        notify.notify_conflict(rel_path)
    except Exception:
        pass

    mark_recently_synced(rel_path)
    if download_file(api_base, rel_path, local_path):
        stat = os.stat(local_path)
        new_local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        with _state_lock:
            set_file_entry(state, rel_path, change["size"], new_local_mtime,
                           change["lastModifiedDateTime"],
                           remote_hash=change.get("remote_hash"))


def _handle_local_deleted_during_poll(api_base, state, rel_path):
    """File exists in remote + state but not locally -> user deleted it locally."""
    logger.info("Local delete detected, removing remote: %s", rel_path)
    delete_remote(api_base, rel_path)
    with _state_lock:
        remove_file_entry(state, rel_path)


def _handle_remote_deleted_during_poll(state, rel_path):
    """File exists locally + state but not remotely -> was deleted on OneDrive."""
    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    logger.info("Remote delete detected, removing local: %s", rel_path)
    try:
        os.remove(local_path)
        parent = os.path.dirname(local_path)
        while parent != cfg.LOCAL_FOLDER:
            try:
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            except OSError:
                break
    except OSError as e:
        logger.warning("Failed to delete local file %s: %s", local_path, e)
    with _state_lock:
        remove_file_entry(state, rel_path)


def _download_new(api_base, state, rel_path, remote_info):
    """Download a new remote file (not in state or local)."""
    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    mark_recently_synced(rel_path)
    _set_progress(rel_path, "download")
    if download_file(api_base, rel_path, local_path,
                     progress_cb=lambda done, total: _set_progress(rel_path, "download", done, total)):
        stat = os.stat(local_path)
        new_local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        local_hash = _compute_local_hash(local_path)
        with _state_lock:
            set_file_entry(state, rel_path, remote_info["size"], new_local_mtime,
                           remote_info["lastModifiedDateTime"],
                           local_hash=local_hash, remote_hash=remote_info.get("remote_hash"))
        logger.info("New remote file downloaded: %s", rel_path)
    _clear_progress()


def _upload_new(api_base, state, rel_path, local_info):
    """Upload a new local file (not in state or remote)."""
    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    _set_progress(rel_path, "upload")
    result = upload_file(api_base, rel_path, local_path,
                         progress_cb=lambda done, total: _set_progress(rel_path, "upload", done, total))
    if result:
        remote_mtime = result.get("lastModifiedDateTime", "")
        local_hash = _compute_local_hash(local_path)
        remote_hash = ""
        hashes = result.get("file", {}).get("hashes", {})
        if hashes:
            remote_hash = hashes.get("sha256Hash") or hashes.get("quickXorHash") or ""
        with _state_lock:
            set_file_entry(state, rel_path, local_info["size"], local_info["mtime"], remote_mtime,
                           local_hash=local_hash, remote_hash=remote_hash)
        logger.info("New local file uploaded: %s", rel_path)
    _clear_progress()


def handle_local_change(api_base, rel_path):
    """Called by file watcher when a local file is created or modified."""
    if is_recently_synced(rel_path):
        logger.debug("Skipping watcher event (recently synced): %s", rel_path)
        return

    local_path = os.path.join(cfg.LOCAL_FOLDER, rel_path)
    if not os.path.isfile(local_path):
        return

    if _should_ignore(rel_path):
        return
    if not _is_in_sync_scope(rel_path):
        return

    logger.info("Local change detected, uploading: %s", rel_path)
    state = load_state(cfg.STATE_DB_PATH)

    t0 = time.time()
    try:
        result = upload_file(api_base, rel_path, local_path)
        if result:
            stat = os.stat(local_path)
            local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            remote_mtime = result.get("lastModifiedDateTime", "")
            local_hash = _compute_local_hash(local_path)
            set_file_entry(state, rel_path, stat.st_size, local_mtime, remote_mtime,
                           local_hash=local_hash)
            save_state(state, cfg.STATE_DB_PATH)
            _log_history("upload", rel_path, "ok", size=stat.st_size,
                         duration_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        logger.error("Failed to upload %s on local change: %s", rel_path, e)
        _log_history("upload", rel_path, "error", duration_ms=int((time.time() - t0) * 1000), error=str(e))


def handle_local_delete(api_base, rel_path):
    """Called by file watcher when a local file is deleted."""
    if is_recently_synced(rel_path):
        logger.debug("Skipping watcher delete event (recently synced): %s", rel_path)
        return

    if _should_ignore(rel_path):
        return
    if not _is_in_sync_scope(rel_path):
        return

    logger.info("Local delete detected, removing from remote: %s", rel_path)
    state = load_state(cfg.STATE_DB_PATH)

    t0 = time.time()
    try:
        delete_remote(api_base, rel_path)
        remove_file_entry(state, rel_path)
        save_state(state, cfg.STATE_DB_PATH)
        _log_history("delete", rel_path, "ok", duration_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        logger.error("Failed to delete remote %s: %s", rel_path, e)
        _log_history("delete", rel_path, "error", duration_ms=int((time.time() - t0) * 1000), error=str(e))
