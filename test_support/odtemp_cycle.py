from collections import deque
import logging
from types import SimpleNamespace
from unittest.mock import patch

from karting import Runtime
from karting.modules.odtemp import ODTempSource


def watchdog_recovery_cycle():
    clock = [0.0]
    class Stop:
        stopped = False
        def __init__(self):
            self.waits = []
        def is_set(self):
            return self.stopped
        def set(self):
            self.stopped = True
        def wait(self, seconds):
            self.waits.append(seconds)
            clock[0] += seconds
            return self.stopped

    class Device:
        def __init__(self, port, recovered=False):
            self.port, self.closed, self.commands = port, False, []
            self.lines = deque([b'~IODTEMP-10\n', b'~F2000\n',
                                b'~G23.5\n' if recovered else b'~G22\n'])
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
        def write(self, raw):
            self.commands.append(raw)
            return len(raw)
        def flush(self):
            pass
        def reset_input_buffer(self):
            pass
        def readline(self, size=-1):
            if self.lines:
                return self.lines.popleft()
            clock[0] = 120.0
            return b''

    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    runtime = Runtime()
    source = ODTempSource(runtime.store)
    source._stop = Stop()
    states, readings, messages = [], [], []
    runtime.bus.subscribe('data.source.odtemp', lambda event: states.append(event.payload.value['state']))
    def measurement(event):
        readings.append((event.payload.value, event.payload.quality))
        if event.payload.quality == 'good' and event.payload.value == 23.5:
            source._stop.set()
    runtime.bus.subscribe('data.temperature', measurement)
    devices = [Device('COM12'), Device('COM14', True)]
    ports = [[SimpleNamespace(device=name, vid=0x0483, pid=0xA26A)] for name in ('COM12', 'COM14')]
    logger, handler = logging.getLogger('karting.modules.odtemp'), Capture()
    old_level = logger.level
    logger.addHandler(handler); logger.setLevel(logging.DEBUG)
    try:
        with patch('serial.tools.list_ports.comports', side_effect=ports) as detect, \
             patch('serial.Serial', side_effect=devices) as opened, \
             patch('karting.modules.odtemp.time.monotonic', side_effect=lambda: clock[0]):
            source._run()
        return SimpleNamespace(states=states, readings=readings, waits=source._stop.waits,
            messages=messages, devices=devices, detects=detect.call_count,
            opened=[call.args[0] for call in opened.call_args_list])
    finally:
        logger.removeHandler(handler); handler.close(); logger.setLevel(old_level)
        runtime.close()
