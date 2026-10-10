from __future__ import annotations

import logging
import math
from threading import Event as ThreadEvent, Thread

from karting.core import DataStore
from karting.lifecycle import already_running, join_worker
from karting.domain import ValidationError
from karting.ingestion import DataIngestor, parse_json, utf8_message
from karting.ports import claim_port

log = logging.getLogger(__name__)


class SerialSource:
    """RS-232 source accepting JSON lines or a plain temperature number."""

    name = "serial"

    def __init__(self, store: DataStore, port: str = '', baudrate: int = 9600,
                 *, ingestor: DataIngestor | None = None, reconnect_interval: float = 60,
                 vid: int | None = None, pid: int | None = None, serial_number: str = '') -> None:
        if not port and (vid is None or pid is None):
            raise ValueError('serial source requires a port or USB VID/PID')
        if not math.isfinite(reconnect_interval) or reconnect_interval <= 0:
            raise ValueError('serial reconnect interval must be positive and finite')
        self._store = store
        self._ingestor = ingestor if ingestor is not None else DataIngestor(store)
        self._port = port
        self._configured_port = port
        self._vid, self._pid, self._serial_number = vid, pid, serial_number
        self._reconnect_interval = reconnect_interval
        self._recovering = False
        self.health = {'state': 'stopped', 'port': port, 'error': None}
        self._baudrate = baudrate
        self._stop = ThreadEvent()
        self._thread: Thread | None = None
        self._serial = None

    def start(self) -> None:
        if already_running(self):
            return
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("RS-232 module requires 'pyserial'") from exc
        self._stop.clear()
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        log.info("serial source started port=%s baudrate=%d", self._port, self._baudrate)

    def _run(self) -> None:
        import serial
        try:
            while not self._stop.is_set():
                try:
                    self._status('connecting')
                    self._port = self._find_port()
                    with claim_port(self._port, self):
                        try:
                            if self._serial is None:
                                self._serial = serial.Serial(self._port, self._baudrate, timeout=0.25)
                            self._status('waiting_measurement')
                            self._read_device(self._serial)
                        finally:
                            self._close_device()
                except (OSError, getattr(serial, 'SerialException', OSError)) as exc:
                    if self._stop.is_set():
                        break
                    self._recovering = True
                    self._status('reconnecting', str(exc))
                    for key in ('temperature', 'humidity'):
                        self._store.mark_quality(key, 'unavailable', owner=f'{self.name}:{self._port}')
                    log.warning('serial connection lost port=%s error=%s retry=%ss', self._port, exc, self._reconnect_interval)
                    self._stop.wait(self._reconnect_interval)
        except Exception as exc:
            log.exception('serial worker failed')
            self._status('failed', str(exc))
        else:
            self._status('stopped')

    def _status(self, state, error=None):
        value = {'state': state, 'port': self._port, 'error': error}
        if self.health != value:
            self.health = value
            log.info('serial state=%s port=%s error=%s', state, self._port, error)

    def _find_port(self):
        if self._configured_port:
            return self._configured_port
        from serial.tools import list_ports
        matches = [port for port in list_ports.comports() if port.vid == self._vid and port.pid == self._pid
                   and (not self._serial_number or getattr(port, 'serial_number', None) == self._serial_number)]
        if len(matches) != 1:
            raise OSError('serial USB device missing or ambiguous; configure a serial number')
        return str(matches[0].device)

    def _close_device(self):
        device, self._serial = self._serial, None
        close = getattr(device, 'close', None)
        if close:
            close()

    def _read_device(self, device) -> None:
        pending = bytearray()
        discarding = False
        limit = self._ingestor.registry.limits.max_bytes
        while not self._stop.is_set():
            raw = device.readline(limit + 1)
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
                    if self._recovering:
                        log.info('serial reading restored port=%s', self._port)
                    self._recovering = False
                    self._status('running')
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
        self._close_device()
        join_worker(self)
        log.info("serial source stopped port=%s", self._port)
