import unittest
from types import SimpleNamespace
from unittest.mock import patch

from karting.core import Runtime
from karting.modules.odtemp import ODTempSource


class StopSignal:
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
        return self.stopped


class RecoveryDevice:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def reset_input_buffer(self):
        pass

    def readline(self):
        if self.fail:
            raise OSError('USB disconnected')
        return b'~G23.5\n'


class ODTempRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.source = ODTempSource(self.runtime.store)
        self.source._stop = StopSignal()
        self.states = []
        self.runtime.bus.subscribe('data.source.odtemp', lambda event: self.states.append(event.payload.value['state']))
        self.runtime.bus.subscribe('data.temperature', lambda event: self.source._stop.set())

    def run_source(self, ports, devices):
        with patch('serial.tools.list_ports.comports', side_effect=ports) as detect, \
             patch('serial.Serial', side_effect=devices) as open_port, \
             patch.object(self.source, '_identify', return_value='ODTEMP-10'), \
             patch.object(self.source, '_ensure_stream', return_value=2000), \
             self.assertLogs('karting.modules.odtemp', level='INFO') as logs:
            self.source._run()
        return detect, open_port, logs.output

    def test_enumeration_error_retries_and_restores_only_after_measurement(self):
        port = SimpleNamespace(vid=0x0483, pid=0xA26A, device='COM13')
        device = RecoveryDevice()
        detect, opened, logs = self.run_source([OSError('enumeration failed'), [port]], [device])
        self.assertEqual(2, detect.call_count)
        self.assertEqual('COM13', opened.call_args.args[0])
        self.assertEqual([60], self.source._stop.waits)
        self.assertEqual(['starting', 'reconnecting', 'waiting_measurement', 'online', 'stopped'], self.states)
        self.assertEqual(23.5, self.runtime.store.get('temperature'))
        self.assertTrue(device.closed)
        self.assertEqual(1, sum('temperature reading restored' in line for line in logs))

    def test_disconnect_redetects_changed_port(self):
        ports = [[SimpleNamespace(vid=0x0483, pid=0xA26A, device=name)] for name in ('COM12', 'COM14')]
        first, second = RecoveryDevice(fail=True), RecoveryDevice()
        detect, opened, logs = self.run_source(ports, [first, second])
        self.assertEqual(['COM12', 'COM14'], [call.args[0] for call in opened.call_args_list])
        self.assertEqual([60], self.source._stop.waits)
        self.assertTrue(first.closed and second.closed)

    def test_stop_interrupts_retry_and_does_not_open_device(self):
        self.source._stop = StopSignal(stop_on_wait=True)
        detect, opened, logs = self.run_source([OSError('failed')], [])
        self.assertEqual(1, detect.call_count)
        self.assertFalse(opened.called)
        self.assertEqual('stopped', self.states[-1])

    def test_unexpected_bug_is_failed_not_an_infinite_retry(self):
        with patch('serial.tools.list_ports.comports', side_effect=RuntimeError('bug')), \
             self.assertLogs('karting.modules.odtemp', level='ERROR'):
            self.source._run()
        self.assertEqual([], self.source._stop.waits)
        self.assertEqual('failed', self.runtime.store.get('source.odtemp')['state'])

    def test_explicit_port_bypasses_enumeration(self):
        self.source._port = 'COM12'
        detect, opened, logs = self.run_source([], [RecoveryDevice()])
        self.assertFalse(detect.called)
        self.assertEqual('COM12', opened.call_args.args[0])
