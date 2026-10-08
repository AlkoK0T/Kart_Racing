from __future__ import annotations

import logging
import re
import time
from threading import Event as ThreadEvent, Thread
from typing import Any, Iterable

from karting.core import DataStore

log = logging.getLogger(__name__)
_MEASUREMENT = re.compile(r"^~G\s*(-?\d+(?:[.,]\d+)?)(?:;(-?\d+(?:[.,]\d+)?))?$")


def find_odtemp_port(
    ports: Iterable[Any], vid: int = 0x0483, pid: int = 0xA26A,
    explicit_port: str = "",
) -> str | None:
    if explicit_port:
        return explicit_port
    for port in ports:
        if port.vid == vid and port.pid == pid:
            return str(port.device)
    return None


def parse_odtemp_line(line: str) -> tuple[float, float | None] | None:
    match = _MEASUREMENT.match(line.strip())
    if not match:
        return None
    temperature = float(match.group(1).replace(",", "."))
    humidity = float(match.group(2).replace(",", ".")) if match.group(2) else None
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
    ) -> None:
        self._store = store
        self._port = port
        self._vid = vid
        self._pid = pid
        self._baudrate = baudrate
        self._stream_interval_ms = max(200, stream_interval_ms)
        self._watchdog_timeout = max(1.0, watchdog_timeout)
        self._reconnect_interval = max(1.0, reconnect_interval)
        self._stop = ThreadEvent()
        self._thread: Thread | None = None

    def start(self) -> None:
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
        if self._thread:
            self._thread.join(timeout=3)
        log.info("ODTEMP source stopped")

    def _run(self) -> None:
        import serial
        from serial.tools import list_ports

        recovering = False
        ever_received = False
        while not self._stop.is_set():
            if recovering:
                log.info("ODTEMP reinitialization attempt: starting USB auto-detect")
            port = find_odtemp_port(
                list_ports.comports(), self._vid, self._pid, self._port
            )
            if not port:
                message = "ODTEMP reinitialization failed: device not found" if recovering else "ODTEMP not found"
                log.info("%s vid=%04X pid=%04X; retry in %ss", message,
                         self._vid, self._pid, self._reconnect_interval)
                self._stop.wait(self._reconnect_interval)
                continue
            log.info("ODTEMP port selected port=%s", port)
            try:
                with serial.Serial(
                    port, self._baudrate, timeout=1, write_timeout=1
                ) as device:
                    device.reset_input_buffer()
                    identification = self._identify(device)
                    stream_period = self._ensure_stream(device)
                    log.info(
                        "ODTEMP initialized port=%s identification=%r stream=%dms",
                        port, identification, stream_period,
                    )
                    self._store.set(
                        "odtemp_device",
                        {"port": port, "identification": identification,
                         "vid": self._vid, "pid": self._pid,
                         "stream_interval_ms": stream_period},
                        source=self.name,
                    )
                    last_measurement = time.monotonic()
                    while not self._stop.is_set():
                        measurement = parse_odtemp_line(
                            (raw := device.readline().decode("ascii", errors="replace"))
                        )
                        if raw:
                            log.debug("ODTEMP received port=%s raw=%r", port, raw.strip())
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
                            log.debug(
                                "ODTEMP measurement port=%s temperature=%s humidity=%s",
                                port, temperature, humidity,
                            )
                            self._store.set("temperature", temperature, source=self.name)
                            if humidity is not None:
                                self._store.set("humidity", humidity, source=self.name)
                        elif is_measurement_stale(
                            last_measurement, time.monotonic(), self._watchdog_timeout
                        ):
                            raise TimeoutError(
                                f"no ODTEMP measurements for {self._watchdog_timeout:g} seconds"
                            )
            except (OSError, serial.SerialException, TimeoutError) as exc:
                log.warning(
                    "ODTEMP connection lost port=%s error=%s; reinitialization in %ss",
                    port, exc, self._reconnect_interval,
                )
                recovering = True
                self._store.remove("odtemp_device", source=self.name)
                self._stop.wait(self._reconnect_interval)

    @staticmethod
    def _identify(device: Any) -> str:
        log.debug("ODTEMP send command='~I'")
        device.write(b"~I\n")
        device.flush()
        answer = ODTempSource._read_prefixed(device, "~I", 3)
        if not answer:
            raise TimeoutError("ODTEMP did not answer ~I")
        return answer[2:] if answer.startswith("~I") else answer

    def _ensure_stream(self, device: Any) -> int:
        log.debug("ODTEMP send command='~F'")
        device.write(b"~F\n")
        device.flush()
        answer = self._read_prefixed(device, "~F", 3)
        try:
            current = int(answer[2:]) if answer else 0
        except ValueError:
            current = 0
        if current > 0:
            log.info("ODTEMP automatic stream already enabled period=%dms", current)
            return current
        log.info("ODTEMP automatic stream is disabled; enabling period=%dms", self._stream_interval_ms)
        log.debug("ODTEMP send command='~W%d'", self._stream_interval_ms)
        device.write(f"~W{self._stream_interval_ms}\n".encode("ascii"))
        device.flush()
        answer = self._read_prefixed(device, "~F", 3)
        if not answer:
            raise TimeoutError("ODTEMP did not confirm automatic streaming")
        try:
            return int(answer[2:])
        except (ValueError, IndexError) as exc:
            raise TimeoutError("ODTEMP returned an invalid stream period") from exc

    @staticmethod
    def _read_prefixed(device: Any, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = device.readline().decode("ascii", errors="replace").strip()
            if line.startswith(prefix):
                return line
        return ""
