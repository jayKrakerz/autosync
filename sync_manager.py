"""Thread-safe sync lifecycle manager for the web dashboard."""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import config as cfg
from onedrive_api import get_api_base, validate_share_link, list_remote_changes
from state_db import load_state, save_state
from sync_engine import full_sync, delta_sync, get_current_op
from file_watcher import start_watcher, stop_watcher

logger = logging.getLogger(__name__)


class SyncManager:
    """Manages the sync lifecycle: start, stop, trigger, status."""

    def __init__(self):
        self._running = False
        self._connected = False
        self._api_base = None
        self._observer = None
        self._poll_thread = None
        self._stop_event = threading.Event()
        self._sync_lock = threading.Lock()
        self._last_sync = None
        self._next_sync = None
        self._error = None
        self._consecutive_failures = 0

    @property
    def running(self):
        return self._running

    def start(self):
        """Start sync: validate link, run initial sync, start watcher + poll loop."""
        if self._running:
            return {"ok": False, "error": "Already running"}

        if not cfg.SHARE_LINK:
            return {"ok": False, "error": "No share link configured"}

        self._api_base = get_api_base(cfg.SHARE_LINK)

        logger.info("Validating share link...")
        if not validate_share_link(self._api_base):
            self._error = "Share link validation failed"
            return {"ok": False, "error": self._error}

        self._connected = True
        self._error = None
        self._consecutive_failures = 0

        # Create local folder if needed
        os.makedirs(cfg.LOCAL_FOLDER, exist_ok=True)

        # Ensure state DB exists
        state = load_state(cfg.STATE_DB_PATH)
        save_state(state, cfg.STATE_DB_PATH)

        # Initial full sync (always full for first run)
        logger.info("Running initial full sync...")
        with self._sync_lock:
            try:
                synced, errors = full_sync(self._api_base)
                self._last_sync = datetime.now(timezone.utc)
                self._record_successful_sync()
                # Initialize delta link for subsequent delta syncs
                self._init_delta_link()
                self._notify_sync_complete(synced)
            except Exception as e:
                logger.error("Initial sync failed: %s", e)

        # Start file watcher
        self._observer = start_watcher(self._api_base)

        # Start poll thread
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self._running = True
        logger.info("Sync engine started")
        return {"ok": True}

    def stop(self):
        """Stop sync: signal stop, stop watcher, join poll thread."""
        if not self._running:
            return {"ok": False, "error": "Not running"}

        self._stop_event.set()

        if self._observer:
            stop_watcher(self._observer)
            self._observer = None

        if self._poll_thread:
            self._poll_thread.join(timeout=10)
            self._poll_thread = None

        self._running = False
        self._connected = False
        self._next_sync = None
        logger.info("Sync engine stopped")
        return {"ok": True}

    def trigger_sync(self):
        """Run full_sync() immediately in a background thread."""
        if not self._running:
            return {"ok": False, "error": "Sync engine not running"}

        def _run():
            with self._sync_lock:
                try:
                    synced, errors = full_sync(self._api_base)
                    self._last_sync = datetime.now(timezone.utc)
                    self._record_successful_sync()
                    self._notify_sync_complete(synced)
                except Exception as e:
                    logger.error("Manual sync failed: %s", e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"ok": True}

    def get_status(self):
        """Return current status as a dict."""
        file_count = 0
        retry_count = 0
        try:
            state = load_state(cfg.STATE_DB_PATH)
            file_count = len(state.get("files", {}))
            retry_count = len(state.get("retry_queue", []))
        except Exception:
            pass

        # Include progress info
        op = get_current_op()

        return {
            "running": self._running,
            "connected": self._connected,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "next_sync": self._next_sync.isoformat() if self._next_sync else None,
            "file_count": file_count,
            "retry_count": retry_count,
            "poll_interval": cfg.POLL_INTERVAL,
            "local_folder": cfg.LOCAL_FOLDER,
            "share_link_set": bool(cfg.SHARE_LINK),
            "error": self._error,
            "current_op": op,
        }

    def _init_delta_link(self):
        """Initialize delta link if not already present."""
        try:
            state = load_state(cfg.STATE_DB_PATH)
            if not state.get("delta_link"):
                # Do an initial delta call to get the first delta link
                _, delta_link = list_remote_changes(self._api_base)
                if delta_link:
                    state["delta_link"] = delta_link
                    save_state(state, cfg.STATE_DB_PATH)
                    logger.info("Delta link initialized for incremental sync")
        except Exception as e:
            logger.debug("Could not initialize delta link: %s", e)

    def _record_successful_sync(self):
        """Record a successful sync for health monitoring."""
        self._consecutive_failures = 0
        try:
            import health_monitor
            health_monitor.record_successful_sync()
        except Exception:
            pass

    def _notify_sync_complete(self, count):
        """Send desktop notification after sync."""
        try:
            import notify
            notify.notify_sync_complete(count)
        except Exception:
            pass

    def _notify_error(self, message):
        """Send desktop notification for repeated errors."""
        try:
            import notify
            notify.notify_error(message)
        except Exception:
            pass

    def _poll_loop(self):
        """Poll loop that sleeps in 1-second increments for responsive shutdown."""
        while not self._stop_event.is_set():
            # Calculate next sync time
            poll_end = time.time() + cfg.POLL_INTERVAL
            self._next_sync = datetime.fromtimestamp(poll_end, tz=timezone.utc)

            # Sleep in 1-second increments
            for _ in range(cfg.POLL_INTERVAL):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

            if self._stop_event.is_set():
                return

            with self._sync_lock:
                try:
                    # Use delta sync for poll cycles (falls back to full if needed)
                    synced, errors = delta_sync(self._api_base)
                    self._last_sync = datetime.now(timezone.utc)
                    self._record_successful_sync()
                    self._notify_sync_complete(synced)
                except Exception as e:
                    logger.error("Poll sync failed: %s", e)
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 3:
                        self._notify_error(f"Sync failing repeatedly: {e}")
