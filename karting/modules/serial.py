from __future__ import annotations

import logging
from threading import Event as ThreadEvent, Thread

from karting.core import DataStore
from karting.domain import ValidationError
from karting.ingestion import DataIngestor, parse_json, utf8_message

log = logging.getLogger(__name__)


class SerialSource:
    """RS-232 source accepting JSON lines or a plain temperature number."""

    name = "serial"

    def __init__(self, store: DataStore, port: str, baudrate: int = 9600,
                 *, ingestor: DataIngestor | None = None) -> None:
        self._store = store
        self._ingestor = ingestor if ingestor is not None else DataIngestor(store)
        self._port = port
        self._baudrate = baudrate
        self._stop = ThreadEvent()
        self._thread: Thread | None = None
        self._serial = None

    def start(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("RS-232 module requires 'pyserial'") from exc
        self._serial = serial.Serial(self._port, self._baudrate, timeout=1)
        self._stop.clear()
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        log.info("serial source connected port=%s baudrate=%d", self._port, self._baudrate)

    def _run(self) -> None:
        pending = bytearray()
        discarding = False
        limit = self._ingestor.registry.limits.max_bytes
        while not self._stop.is_set():
            raw = self._serial.readline(limit + 1)
            if not raw:
                continue
            if discarding:
                discarding = not raw.endswith(b'\n')
                continue
            pending.extend(raw)
            if len(pending) > limit:
                log.warning('serial ignored oversized line port=%s limit=%s', self._port, limit)
                pending.clear()
                discarding = not raw.endswith(b'\n')
                continue
            if not raw.endswith(b'\n'):
                continue
            try:
                if pending.strip():
                    self.consume_line(bytes(pending))
            except ValidationError as exc:
                log.warning("serial ignored invalid line port=%s error=%s", self._port, exc)
            finally:
                pending.clear()

    def consume_line(self, raw: bytes | str):
        log.debug("serial received port=%s raw=%r", self._port, raw)
        limits = self._ingestor.registry.limits
        line = utf8_message(raw, limits).strip()
        try:
            message = parse_json(line, limits)
        except ValidationError as parse_error:
            try:
                message = float(line.replace(',', '.'))
            except ValueError:
                raise parse_error
        if isinstance(message, dict):
            return self._ingestor.message(message, source=f'{self.name}:{self._port}')
        return self._ingestor.ingest('temperature', message, source=f'{self.name}:{self._port}')

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._serial:
            self._serial.close()
        log.info("serial source stopped port=%s", self._port)
