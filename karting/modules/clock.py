from __future__ import annotations

from datetime import datetime
import logging
from threading import Event as ThreadEvent, Thread

from karting.core import DataStore

log = logging.getLogger(__name__)


class ClockSource:
    name = "clock"

    def __init__(self, store: DataStore, interval: float = 1.0) -> None:
        self._store = store
        self._interval = interval
        self._stop = ThreadEvent()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        log.info("clock source started interval=%s", self._interval)

    def _run(self) -> None:
        while not self._stop.is_set():
            current = datetime.now().astimezone()
            log.debug("clock value=%s", current.isoformat())
            self._store.set("current_time", current, source=self.name)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
        log.info("clock source stopped")
