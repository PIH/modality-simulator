"""Auto-acquire: every poll, acquire each worklist entry that hasn't been stored yet."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from modality_simulator.acquire import AcquisitionResult
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.results import AcquisitionLog

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class AutoAcquirer:
    def __init__(
        self,
        query: Callable[[], list[WorklistEntry]],
        do_acquire: Callable[[WorklistEntry], AcquisitionResult],
        log: AcquisitionLog,
    ):
        self._query = query
        self._acquire = do_acquire
        self._log = log

    def run_once(self) -> None:
        for entry in self._query():
            if not entry.acquirable:
                continue
            # Read the log for each entry: the console may have acquired it meanwhile.
            history = self._log.history().get(entry.accession_number)
            if history and (history.stored or history.failures >= MAX_ATTEMPTS):
                continue
            self._acquire(entry)

    def run_forever(self, poll_seconds: float, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except GatewayError as e:
                logger.warning("Auto-acquire couldn't read the worklist: %s", e)
            except Exception:
                logger.exception("Auto-acquire poll failed")
            stop.wait(poll_seconds)
