from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from karting.core import Runtime
from karting.modules.serial import SerialSource
from karting.ports import claim_port


class Stop:
    def __init__(self, stop_on_wait=False):
        self.stopped = False
        self.waits = []
        self.stop_on_wait = stop_on_wait
    def is_set(self):
        return self.stopped
    def set(self):
        self.stopped = True
    def wait(self, interval):
        self.waits.append(interval)
        if self.stop_on_wait:
            self.set()


class Device:
    def __init__(self, lines):
        self.lines = deque(lines)
        self.closed = False
    def readline(self, size=-1):
        value = self.lines.popleft()
        if isinstance(value, Exception):
            raise value
        return value
    def close(self):
        self.closed = True


class SerialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.source = SerialSource(self.runtime.store, 'COM12')
        self.source._stop = Stop()
        self.runtime.bus.subscribe('data.temperature', lambda event:
                                   self.source._stop.set() if event.payload.value == 23.5 else None)

    def test_initial_open_error_retries_without_aborting_runtime(self):
        device = Device([b'23.5\n'])
        with patch('serial.Serial', side_effect=[OSError('not connected'), device]) as opened, \
             self.assertLogs('karting.modules.serial', level='INFO') as logs:
            self.source._run()
        self.assertEqual(2, opened.call_count)
        self.assertEqual([60], self.source._stop.waits)
        self.assertTrue(device.closed)
        self.assertTrue(any('reading restored' in line for line in logs.output))

    def test_disconnect_redetects_port_and_discards_old_partial_line(self):
        self.source._configured_port = ''
        self.source._vid, self.source._pid = 1, 2
        ports = [[SimpleNamespace(device=name, vid=1, pid=2)] for name in ('COM12', 'COM13')]
        first = Device([b'99', OSError('disconnected')]); second = Device([b'23.5\n'])
        with patch('serial.tools.list_ports.comports', side_effect=ports), \
             patch('serial.Serial', side_effect=[first, second]) as opened, \
             self.assertLogs('karting.modules.serial', level='WARNING'):
            self.source._run()
        self.assertEqual(['COM12', 'COM13'], [call.args[0] for call in opened.call_args_list])
        self.assertEqual(23.5, self.runtime.store.get('temperature'))
        self.assertTrue(first.closed and second.closed)

    def test_stop_interrupts_retry(self):
        self.source._stop = Stop(stop_on_wait=True)
        with patch('serial.Serial', side_effect=OSError('absent')) as opened, \
             self.assertLogs('karting.modules.serial', level='WARNING'):
            self.source._run()
        self.assertEqual(1, opened.call_count)
        self.assertEqual('stopped', self.source.health['state'])

    def test_unexpected_error_is_visible_and_not_retried_forever(self):
        with patch('serial.Serial', side_effect=RuntimeError('bug')), \
             self.assertLogs('karting.modules.serial', level='ERROR'):
            self.source._run()
        self.assertEqual([], self.source._stop.waits)
        self.assertEqual('failed', self.source.health['state'])

    def test_port_ownership_is_case_insensitive_and_released(self):
        first, second = object(), object()
        with claim_port('COM12', first):
            for port in ('com12', '\\\\.\\COM12'):
                with self.assertRaises(OSError):
                    with claim_port(port, second):
                        self.fail('duplicate ownership')
        with claim_port('COM12', second):
            pass

    def test_auto_detect_requires_unique_matching_identity(self):
        self.source._configured_port = ''
        self.source._vid, self.source._pid = 1, 2
        ports = [SimpleNamespace(device=name, vid=1, pid=2, serial_number=serial)
                 for name, serial in (('COM12', 'A'), ('COM13', 'B'))]
        with patch('serial.tools.list_ports.comports', return_value=ports):
            with self.assertRaises(OSError):
                self.source._find_port()
            self.source._serial_number = 'B'
            self.assertEqual('COM13', self.source._find_port())
