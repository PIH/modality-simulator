"""The most recent log records, kept in memory for the console to show."""

from __future__ import annotations

import logging
from collections import deque

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RecentLogs(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self._records: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append(self.format(record))
        except Exception:
            self.handleError(record)

    def lines(self) -> list[str]:
        """The kept records, oldest first."""
        with self.lock:  # handle() holds it while emitting, so the deque can't change mid-copy
            return list(self._records)
