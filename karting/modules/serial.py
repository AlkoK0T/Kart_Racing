from __future__ import annotations

import json
import logging
from threading import Event as ThreadEvent, Thread

from karting.core import DataStore

log = logging.getLogger(__name__)


class SerialSource:
    """RS-232 source accepting JSON lines or a plain temperature number."""

    name = "serial"

    def __init__(self, store: DataStore, port: str, baudrate: int = 9600) -> None:
        self._store = store
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
        while not self._stop.is_set():
            raw = self._serial.readline().decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            log.debug("serial received port=%s raw=%r", self._port, raw)
            try:
                message = json.loads(raw)
                if isinstance(message, dict) and "category" in message:
                    self._store.set(str(message["category"]), message.get("data"), source=self.name)
                    log.debug("serial category updated category=%s", message["category"])
                else:
                    self._store.set("temperature", message, source=self.name)
                    log.debug("serial temperature updated value=%r", message)
            except json.JSONDecodeError:
                try:
                    self._store.set("temperature", float(raw.replace(",", ".")), source=self.name)
                    log.debug("serial temperature updated value=%s", raw)
                except ValueError:
                    log.warning("serial ignored invalid line port=%s raw=%r", self._port, raw)
                    continue

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._serial:
            self._serial.close()
        log.info("serial source stopped port=%s", self._port)
