import unittest
from unittest.mock import MagicMock

from app import create_app
from karting.core import Runtime
from karting.lifecycle import already_running, join_worker
from karting.modules.clock import ClockSource


class Module:
    def __init__(self, name, calls, fail_start=False, fail_stop=False):
        self.name, self.calls = name, calls
        self.fail_start, self.fail_stop = fail_start, fail_stop

    def start(self):
        self.calls.append('start:' + self.name)
        if self.fail_start:
            raise ValueError('primary startup error')

    def stop(self):
        self.calls.append('stop:' + self.name)
        if self.fail_stop:
            raise RuntimeError('cleanup error')


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.calls = []

    def test_partial_start_is_cleaned_before_previously_started_modules(self):
        self.runtime.add(Module('first', self.calls))
        self.runtime.add(Module('broken', self.calls, fail_start=True))
        with self.assertLogs('karting.core', level='ERROR'), self.assertRaisesRegex(ValueError, 'primary'):
            self.runtime.start()
        self.assertEqual(['start:first', 'start:broken', 'stop:broken', 'stop:first'], self.calls)
        self.assertEqual('failed', self.runtime.health_snapshot()['state'])

    def test_rollback_preserves_primary_error_even_when_cleanup_fails(self):
        module = self.runtime.add(Module('broken', self.calls, True, True))
        with self.assertLogs('karting.core', level='ERROR'), self.assertRaises(ValueError):
            self.runtime.start()
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            self.runtime.start()
        module.fail_stop = False
        self.runtime.stop()

    def test_stop_failure_does_not_skip_other_modules_and_is_retryable(self):
        self.runtime.add(Module('first', self.calls))
        module = self.runtime.add(Module('second', self.calls, fail_stop=True))
        self.runtime.start()
        with self.assertLogs('karting.core', level='ERROR'), self.assertRaisesRegex(RuntimeError, 'shutdown errors'):
            self.runtime.stop()
        self.assertEqual(['stop:second', 'stop:first'], self.calls[-2:])
        self.assertEqual('failed', self.runtime.health_snapshot()['modules']['second']['state'])
        module.fail_stop = False
        self.runtime.stop()
        self.assertEqual('stopped', self.runtime.health_snapshot()['state'])

    def test_repeated_start_stop_and_restart_have_defined_order(self):
        self.runtime.add(Module('one', self.calls))
        for _ in range(2):
            self.runtime.start(); self.runtime.start()
            self.runtime.stop(); self.runtime.stop()
        self.assertEqual(['start:one', 'stop:one'] * 2, self.calls)

    def test_optional_failure_allows_other_modules_to_start(self):
        self.runtime.add(Module('optional', self.calls, fail_start=True), required=False)
        self.runtime.add(Module('first', self.calls))
        with self.assertLogs('karting.core', level='ERROR'):
            self.runtime.start()
        try:
            self.assertEqual('degraded', self.runtime.health_snapshot()['state'])
            self.assertEqual('running', self.runtime.health_snapshot()['modules']['first']['state'])
        finally:
            self.runtime.stop()

    def test_dead_worker_is_reported_without_claiming_runtime_is_healthy(self):
        module = self.runtime.add(Module('worker', self.calls))
        self.runtime.start()
        try:
            module._thread = MagicMock()
            module._thread.is_alive.return_value = False
            with self.assertLogs('karting.core', level='ERROR'):
                self.runtime._check_workers()
            health = create_app(self.runtime).test_client().get('/api/health').json['runtime']
            self.assertEqual('degraded', health['state'])
            self.assertEqual('failed', health['modules']['worker']['state'])
        finally:
            self.runtime.stop()

    def test_worker_guards_reject_unfinished_stop(self):
        module = Module('worker', self.calls)
        module._stop = MagicMock(); module._stop.is_set.return_value = True
        module._thread = MagicMock(); module._thread.is_alive.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'still stopping'):
            already_running(module)
        with self.assertRaisesRegex(RuntimeError, 'did not stop'):
            join_worker(module, timeout=0)
        self.assertIsNotNone(module._thread)

    def test_real_clock_does_not_duplicate_thread_and_can_restart(self):
        clock = self.runtime.add(ClockSource(self.runtime.store))
        self.runtime.start()
        try:
            first = clock._thread
            clock.start()
            self.assertIs(first, clock._thread)
        finally:
            self.runtime.stop()
        self.assertFalse(first.is_alive())
        self.runtime.start()
        try:
            self.assertIsNot(first, clock._thread)
        finally:
            self.runtime.stop()
