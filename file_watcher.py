import logging
import os

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config as cfg
from sync_engine import handle_local_change, handle_local_delete

logger = logging.getLogger(__name__)


class SyncEventHandler(FileSystemEventHandler):
    """Handles local filesystem events and triggers sync actions."""

    def __init__(self, api_base):
        super().__init__()
        self.api_base = api_base

    def _get_rel_path(self, event):
        """Convert an absolute event path to a relative path from cfg.LOCAL_FOLDER."""
        rel = os.path.relpath(event.src_path, cfg.LOCAL_FOLDER)
        return rel.replace(os.sep, "/")

    def on_created(self, event):
        if event.is_directory:
            return
        rel_path = self._get_rel_path(event)
        handle_local_change(self.api_base, rel_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        rel_path = self._get_rel_path(event)
        handle_local_change(self.api_base, rel_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        rel_path = self._get_rel_path(event)
        handle_local_delete(self.api_base, rel_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        # Treat move as delete old + create new
        old_rel = os.path.relpath(event.src_path, cfg.LOCAL_FOLDER).replace(os.sep, "/")
        new_rel = os.path.relpath(event.dest_path, cfg.LOCAL_FOLDER).replace(os.sep, "/")
        handle_local_delete(self.api_base, old_rel)
        handle_local_change(self.api_base, new_rel)


def start_watcher(api_base):
    """Start the watchdog observer watching cfg.LOCAL_FOLDER. Returns the observer thread."""
    event_handler = SyncEventHandler(api_base)
    observer = Observer()
    observer.schedule(event_handler, cfg.LOCAL_FOLDER, recursive=True)
    observer.start()
    logger.info("File watcher started on %s", cfg.LOCAL_FOLDER)
    return observer


def stop_watcher(observer):
    """Gracefully stop the watchdog observer."""
    observer.stop()
    observer.join()
    logger.info("File watcher stopped")
