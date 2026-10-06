"""Auto-acquire: every poll, acquire each worklist entry that hasn't been stored yet."""

from __future__ import annotations

import logging
import threading
from collections import Counter
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
        do_acquire: Callable[..., AcquisitionResult | None],
        log: AcquisitionLog,
    ):
        self._query = query
        self._acquire = do_acquire
        self._log = log
        # A backstop for when the log can't be written: per process, at most MAX_ATTEMPTS tries, none once stored.
        self._attempts: Counter[str] = Counter()
        self._stored: set[str] = set()

    def run_once(self) -> None:
        for entry in self._query():
            if not entry.acquirable:
                continue
            accession = entry.accession_number
            if accession in self._stored or self._attempts[accession] >= MAX_ATTEMPTS:
                continue
            # Read the log for each entry: the console may have acquired it meanwhile.
            history = self._log.history().get(accession)
            if history and (history.stored or history.failures >= MAX_ATTEMPTS):
                continue
            self._attempts[accession] += 1
            try:
                # only_if_new: the acquirer re-checks under its lock, in case the console is acquiring it right now.
                result = self._acquire(entry, only_if_new=True)
            except Exception:
                logger.exception("Auto-acquire of accession %s failed unexpectedly", accession)
                continue
            if result is not None and result.ok:
                self._stored.add(accession)

    def run_forever(self, poll_seconds: float, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except GatewayError as e:
                logger.warning("Auto-acquire couldn't read the worklist: %s", e)
            except Exception:
                logger.exception("Auto-acquire poll failed")
            stop.wait(poll_seconds)
