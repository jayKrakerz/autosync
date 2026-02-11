"""Custom logging handler for SSE log streaming."""

import logging
import queue
import threading
from collections import deque


class SSELogHandler(logging.Handler):
    """Logging handler that captures log records for SSE streaming.

    Keeps a bounded history for backfill on new connections and
    pushes formatted entries to all subscriber queues.
    """

    def __init__(self, maxlen=100):
        super().__init__()
        self._history = deque(maxlen=maxlen)
        self._subscribers = []
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    def emit(self, record):
        entry = {
            "timestamp": self.format(record).split(" [")[0],
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "formatted": self.format(record),
        }
        with self._lock:
            self._history.append(entry)
            for q in self._subscribers:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass

    def subscribe(self):
        """Create a new subscriber queue, backfill with history, return it."""
        q = queue.Queue(maxsize=200)
        with self._lock:
            for entry in self._history:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    break
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        """Remove a subscriber queue."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


# Singleton instance attached to root logger
sse_handler = SSELogHandler()
