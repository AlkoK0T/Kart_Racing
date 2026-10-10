from __future__ import annotations

import logging
import math
import re
import time
from threading import Event as ThreadEvent, Thread
from typing import Any, Iterable

from karting.core import DataStore
from karting.lifecycle import already_running, join_worker

log = logging.getLogger(__name__)
_MEASUREMENT = re.compile(r"^~G\s*(-?\d+(?:[.,]\d+)?)(?:;(-?\d+(?:[.,]\d+)?))?$")


def find_odtemp_port(
    ports: Iterable[Any], vid: int = 0x0483, pid: int = 0xA26A,
    explicit_port: str = "", serial_number: str = '',
) -> str | None:
    if explicit_port:
        return explicit_port
    matches = [port for port in ports if port.vid == vid and port.pid == pid
               and (not serial_number or getattr(port, 'serial_number', None) == serial_number)]
    if len(matches) > 1:
        raise OSError('ambiguous ODTEMP devices; configure serial number or explicit port')
    return str(matches[0].device) if matches else None


def parse_odtemp_line(line: str) -> tuple[float, float | None] | None:
    match = _MEASUREMENT.match(line.strip())
    if not match:
        return None
    temperature = float(match.group(1).replace(",", "."))
    humidity = float(match.group(2).replace(",", ".")) if match.group(2) else None
    if not math.isfinite(temperature) or (humidity is not None and (not math.isfinite(humidity) or not 0 <= humidity <= 100)):
        return None
    return temperature, humidity


def is_measurement_stale(last_measurement: float, now: float, timeout: float = 120) -> bool:
    return now - last_measurement >= timeout


class ODTempSource:
    """ODTEMP USB CDC reader with VID/PID port discovery and hotplug recovery."""

    name = "odtemp"

    def __init__(
        self, store: DataStore, port: str = "", vid: int = 0x0483,
        pid: int = 0xA26A, baudrate: int = 9600,
        stream_interval_ms: int = 2000, watchdog_timeout: float = 120.0,
        reconnect_interval: float = 60.0,
        serial_number: str = '',
    ) -> None:
        self._store = store
        self._port = port
        self._vid = vid
        self._pid = pid
        self._baudrate = baudrate
        self._serial_number = serial_number
        self._stream_interval_ms = max(200, stream_interval_ms)
        self._watchdog_timeout = max(1.0, watchdog_timeout)
        self._reconnect_interval = max(1.0, reconnect_interval)
        self._stop = ThreadEvent()
        self._thread: Thread | None = None
        self._line_device = None
        self._pending_line = bytearray()
        self._discarding_line = False
        if self._stream_interval_ms * 2 > self._watchdog_timeout * 1000:
            raise ValueError('ODTEMP configured stream period must not exceed half the watchdog timeout')

    def start(self) -> None:
        if already_running(self):
            return
        try:
            import serial  # noqa: F401
            from serial.tools import list_ports  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("ODTEMP module requires 'pyserial'") from exc
        self._stop.clear()
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        log.info(
            "ODTEMP source started auto_port=%s vid=%04X pid=%04X watchdog=%ss retry=%ss",
            not bool(self._port), self._vid, self._pid,
            self._watchdog_timeout, self._reconnect_interval,
        )

    def stop(self) -> None:
        self._stop.set()
        join_worker(self)
        log.info("ODTEMP source stopped")

    def _run(self) -> None:
        try:
            self._read_loop()
        except Exception as exc:
            log.exception('ODTEMP worker failed; automatic retries stopped')
            self._invalidate_readings('unavailable')
            self._store.remove('odtemp_device', source=self.name)
            self._status('failed', error=f'{type(exc).__name__}: {exc}')

    def _invalidate_readings(self, quality):
        for key in ('temperature', 'humidity'):
            self._store.mark_quality(key, quality, owner=self.name)

    def _status(self, state: str, port: str | None = None, error: str | None = None):
        self._store.set('source.odtemp', {'state': state, 'port': port, 'error': error}, source=self.name)

    def _read_loop(self) -> None:
        import serial
        from serial.tools import list_ports

        recovering = False
        ever_received = False
        self._status('starting')
        while not self._stop.is_set():
            if recovering:
                log.info("ODTEMP reinitialization attempt: starting USB auto-detect")
            port = None
            try:
                port = find_odtemp_port(
                    () if self._port else list_ports.comports(), self._vid, self._pid, self._port,
                    self._serial_number,
                )
                if not port:
                    raise OSError('ODTEMP device not found')
                log.info("ODTEMP port selected port=%s", port)
                with serial.Serial(
                    port, self._baudrate, timeout=0.25, write_timeout=1
                ) as device:
                    device.reset_input_buffer()
                    identification = self._identify(device)
                    stream_period = self._ensure_stream(device)
                    log.info(
                        "ODTEMP initialized port=%s identification=%r stream=%dms",
                        port, identification, stream_period,
                    )
                    self._status('waiting_measurement', port)
                    self._store.set(
                        "odtemp_device",
                        {"port": port, "identification": identification,
                         "vid": self._vid, "pid": self._pid,
                         "stream_interval_ms": stream_period},
                        source=self.name,
                    )
                    last_measurement = time.monotonic()
                    while not self._stop.is_set():
                        measurement = parse_odtemp_line(self._read_line(device))
                        if measurement is not None:
                            last_measurement = time.monotonic()
                            temperature, humidity = measurement
                            if recovering:
                                log.info(
                                    "ODTEMP temperature reading restored port=%s temperature=%s",
                                    port, temperature,
                                )
                            elif not ever_received:
                                log.info(
                                    "ODTEMP temperature reading started port=%s temperature=%s",
                                    port, temperature,
                                )
                            recovering = False
                            ever_received = True
                            if self._store.get('source.odtemp', {}).get('state') != 'online':
                                self._status('online', port)
                            log.debug(
                                "ODTEMP measurement port=%s temperature=%s humidity=%s",
                                port, temperature, humidity,
                            )
                            self._store.set("temperature", temperature, source=self.name)
                            if humidity is not None:
                                self._store.set("humidity", humidity, source=self.name)
                            else:
                                self._store.mark_quality('humidity', 'unavailable', owner=self.name)
                        elif is_measurement_stale(
                            last_measurement, time.monotonic(), self._watchdog_timeout
                        ):
                            raise TimeoutError(
                                f"no ODTEMP measurements for {self._watchdog_timeout:g} seconds"
                            )
            except (OSError, serial.SerialException, TimeoutError) as exc:
                if self._stop.is_set():
                    break
                log.warning(
                    "ODTEMP connection lost port=%s error=%s; reinitialization in %ss",
                    port, exc, self._reconnect_interval,
                )
                recovering = True
                self._invalidate_readings('stale' if isinstance(exc, TimeoutError) else 'unavailable')
                self._store.remove("odtemp_device", source=self.name)
                self._status('reconnecting', port, str(exc))
                self._stop.wait(self._reconnect_interval)
        self._invalidate_readings('unavailable')
        self._store.remove('odtemp_device', source=self.name)
        self._status('stopped')

    def _identify(self, device: Any) -> str:
        if self._stop.is_set():
            raise InterruptedError('ODTEMP initialization stopped')
        log.debug("ODTEMP send command='~I'")
        device.write(b"~I\n")
        device.flush()
        answer = self._read_prefixed(device, "~I", 3)
        if not answer or not answer[2:].strip():
            raise TimeoutError("ODTEMP did not answer ~I")
        return answer[2:] if answer.startswith("~I") else answer

    def _ensure_stream(self, device: Any) -> int:
        if self._stop.is_set():
            raise InterruptedError('ODTEMP initialization stopped')
        log.debug("ODTEMP send command='~F'")
        device.write(b"~F\n")
        device.flush()
        answer = self._read_prefixed(device, "~F", 3)
        try:
            current = int(answer[2:]) if answer else 0
        except ValueError:
            current = 0
        if self._valid_period(current):
            log.info("ODTEMP automatic stream already enabled period=%dms", current)
            return current
        log.info("ODTEMP automatic stream is disabled or too slow; enabling period=%dms", self._stream_interval_ms)
        log.debug("ODTEMP send command='~W%d'", self._stream_interval_ms)
        device.write(f"~W{self._stream_interval_ms}\n".encode("ascii"))
        device.flush()
        answer = self._read_prefixed(device, "~F", 3)
        if not answer:
            raise TimeoutError("ODTEMP did not confirm automatic streaming")
        try:
            confirmed = int(answer[2:])
        except (ValueError, IndexError) as exc:
            raise TimeoutError("ODTEMP returned an invalid stream period") from exc
        if not self._valid_period(confirmed):
            raise TimeoutError('ODTEMP confirmed a zero/negative period or one incompatible with watchdog')
        return confirmed

    def _valid_period(self, period):
        return period > 0 and period * 2 <= self._watchdog_timeout * 1000

    def _read_line(self, device):
        if self._line_device is not device:
            self._line_device = device
            self._pending_line.clear()
            self._discarding_line = False
        if self._stop.is_set():
            return ''
        raw = device.readline(4097)
        if raw:
            log.debug('ODTEMP received port=%s raw=%r', getattr(device, 'port', self._port), raw)
        if self._discarding_line:
            self._discarding_line = not raw.endswith(b'\n')
            return ''
        self._pending_line.extend(raw)
        if len(self._pending_line) > 4096:
            self._pending_line.clear()
            self._discarding_line = not raw.endswith(b'\n')
            log.warning('ODTEMP discarded oversized line')
            return ''
        if not raw.endswith(b'\n'):
            return ''
        line = bytes(self._pending_line)
        self._pending_line.clear()
        try:
            return line.decode('ascii').strip()
        except UnicodeDecodeError:
            log.debug('ODTEMP discarded non-ASCII line raw=%r', line)
            return ''

    def _read_prefixed(self, device: Any, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop.is_set():
                raise InterruptedError('ODTEMP command wait stopped')
            line = self._read_line(device)
            if self._stop.is_set():
                raise InterruptedError('ODTEMP command wait stopped')
            if line.startswith(prefix):
                return line
            if line:
                log.debug('ODTEMP discarded command response expected=%s line=%r', prefix, line)
        return ""
