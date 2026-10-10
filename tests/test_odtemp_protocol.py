from collections import deque
from types import SimpleNamespace
import unittest

from karting.core import Runtime
from karting.modules.odtemp import ODTempSource, find_odtemp_port, parse_odtemp_line


class Device:
    def __init__(self, lines):
        self.lines = deque(lines)
        self.writes = []

    def readline(self, size=-1):
        return self.lines.popleft() if self.lines else b''

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass


class ODTempProtocolTests(unittest.TestCase):
    def setUp(self):
        self.source = ODTempSource(Runtime().store)

    def test_zero_negative_or_too_slow_confirmation_is_not_success(self):
        for period in (0, -1, 120000):
            device = Device([b'~F0\n', f'~F{period}\n'.encode()])
            with self.assertRaises(TimeoutError):
                self.source._ensure_stream(device)

    def test_slow_existing_period_is_reconfigured(self):
        device = Device([b'~F120000\n', b'~F2000\n'])
        self.assertEqual(2000, self.source._ensure_stream(device))
        self.assertIn(b'~W2000\n', device.writes)

    def test_serial_number_disambiguates_identical_devices(self):
        ports = [SimpleNamespace(device='COM12', vid=0x0483, pid=0xA26A, serial_number='A'),
                 SimpleNamespace(device='COM13', vid=0x0483, pid=0xA26A, serial_number='B')]
        self.assertEqual('COM13', find_odtemp_port(ports, serial_number='B'))
        with self.assertRaisesRegex(OSError, 'ambiguous'):
            find_odtemp_port(ports)
        self.assertIsNone(find_odtemp_port(ports, serial_number='missing'))

    def test_partial_measurement_is_assembled_across_timeout(self):
        device = Device([b'~G2', b'', b'3.5;50\n'])
        self.assertEqual('', self.source._read_line(device))
        self.assertEqual('', self.source._read_line(device))
        self.assertEqual((23.5, 50), parse_odtemp_line(self.source._read_line(device)))

    def test_new_device_does_not_inherit_partial_line(self):
        self.source._read_line(Device([b'~G99']))
        self.assertEqual('~G22', self.source._read_line(Device([b'~G22\n'])))

    def test_oversized_line_discards_tail_and_recovers(self):
        device = Device([b'x' * 4097, b'~G99\n', b'~G22\n'])
        with self.assertLogs('karting.modules.odtemp', level='WARNING'):
            self.assertEqual('', self.source._read_line(device))
        self.assertEqual('', self.source._read_line(device))
        self.assertEqual('~G22', self.source._read_line(device))

    def test_stop_interrupts_command_wait_without_next_write(self):
        source = self.source
        class StoppingDevice(Device):
            def readline(self, size=-1):
                source._stop.set()
                return b''
        device = StoppingDevice([])
        with self.assertRaises(InterruptedError):
            self.source._identify(device)
        self.assertEqual([b'~I\n'], device.writes)
        with self.assertRaises(InterruptedError):
            self.source._ensure_stream(device)
        self.assertEqual([b'~I\n'], device.writes)

    def test_command_responses_and_discarded_lines_are_logged(self):
        with self.assertLogs('karting.modules.odtemp', level='DEBUG') as logs:
            self.assertEqual('~F2000', self.source._read_prefixed(Device([b'unknown\n', b'~F2000\n']), '~F', 1))
        self.assertTrue(any('raw=' in line for line in logs.output))
        self.assertTrue(any('discarded command response' in line for line in logs.output))
