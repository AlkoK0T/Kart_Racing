import json
import socket
import tempfile
import unittest
from pathlib import Path
from threading import Event as Signal

from karting.core import Event, Runtime
from karting.modules.clock import ClockSource
from karting.modules.files import FileSource
from karting.modules.tcp import TcpJsonSource


def broken(event):
    raise RuntimeError('observer failed')


class EventIsolationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.addCleanup(self.runtime.bus.close)

    def test_failure_does_not_abort_set_or_remaining_notifications(self):
        bus, store = self.runtime.bus, self.runtime.store
        seen = []
        bus.subscribe('data.temperature', broken)
        bus.subscribe('data.temperature', seen.append)
        bus.subscribe('*', broken)
        bus.subscribe('*', seen.append)
        with self.assertLogs('karting.core', level='ERROR') as logs:
            item = store.set('temperature', 22, source='sensor')
        self.assertEqual(22, item.value)
        self.assertEqual(['data.temperature', 'data.temperature', 'data.changed'], [e.topic for e in seen])
        self.assertTrue(all('source=sensor' in line for line in logs.output))
        health = bus.subscriber_health()
        self.assertEqual(1, health[0]['failures'])
        self.assertEqual(2, health[2]['failures'])
        self.assertEqual('degraded', health[0]['status'])
        health[0]['failures'] = 99
        self.assertEqual(1, bus.subscriber_health()[0]['failures'])

    def test_slow_queued_callback_does_not_block_source_and_queue_is_bounded(self):
        started, release, finished = Signal(), Signal(), Signal()
        def slow(event):
            started.set()
            release.wait(2)
            finished.set()
        unsubscribe = self.runtime.bus.subscribe('data.temperature', slow, queued=True, queue_size=1)
        self.addCleanup(unsubscribe)
        self.addCleanup(release.set)
        self.runtime.store.set('temperature', 20)
        self.assertTrue(started.wait(1))
        self.runtime.store.set('temperature', 21)
        with self.assertLogs('karting.core', level='WARNING'):
            self.runtime.store.set('temperature', 22)
        self.assertEqual(22, self.runtime.store.get('temperature'))
        self.assertEqual(1, self.runtime.bus.subscriber_health()[0]['dropped'])
        release.set()
        self.assertTrue(finished.wait(1))

    def test_queued_failures_are_logged_without_killing_worker(self):
        received = Signal()
        def handler(event):
            if event.payload == 1:
                broken(event)
            else:
                received.set()
        self.runtime.bus.subscribe('test', handler, queued=True)
        with self.assertLogs('karting.core', level='ERROR'):
            self.runtime.bus.publish(Event('test', 1, 'producer'))
            self.runtime.bus.publish(Event('test', 2, 'producer'))
            self.assertTrue(received.wait(1))
        self.assertEqual(1, self.runtime.bus.subscriber_health()[0]['failures'])

    def test_tcp_acknowledges_committed_data_even_if_observer_fails(self):
        self.runtime.bus.subscribe('*', broken)
        source = TcpJsonSource(self.runtime.store, port=0)
        source.start()
        self.addCleanup(source.stop)
        with self.assertLogs('karting.core', level='ERROR'):
            with socket.create_connection(source._server.server_address, timeout=2) as client:
                with client.makefile('rb') as stream:
                    for value in (20, 21):
                        client.sendall(json.dumps({'category': 'temperature', 'data': value}).encode() + b'\n')
                        self.assertEqual({'ok': True}, json.loads(stream.readline()))
        self.assertEqual(21, self.runtime.store.get('temperature'))

    def test_file_and_clock_sources_continue_after_observer_failure(self):
        self.runtime.bus.subscribe('*', broken)
        with tempfile.TemporaryDirectory() as directory:
            category = Path(directory, 'temperature')
            category.mkdir()
            path = category / 'sensor.yaml'
            path.write_text('20', encoding='utf-8')
            source = FileSource(self.runtime.store, directory)
            with self.assertLogs('karting.core', level='ERROR'):
                source.scan()
            self.assertEqual(20, self.runtime.store.get('temperature'))
        clock = ClockSource(self.runtime.store, interval=0.001)
        received = Signal()
        self.runtime.bus.subscribe('data.current_time', lambda event: received.set())
        with self.assertLogs('karting.core', level='ERROR'):
            clock.start()
            try:
                self.assertTrue(received.wait(1))
                self.assertTrue(clock._thread.is_alive())
            finally:
                clock.stop()

    def test_unsubscribe_and_close_are_idempotent(self):
        seen = []
        unsubscribe = self.runtime.bus.subscribe('*', seen.append, queued=True)
        unsubscribe(); unsubscribe()
        self.runtime.bus.close(); self.runtime.bus.close()
        self.runtime.bus.publish(Event('x'))
        self.assertEqual([], seen)

    def test_control_flow_exceptions_are_not_swallowed(self):
        def interrupt(event):
            raise KeyboardInterrupt()
        self.runtime.bus.subscribe('x', interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.runtime.bus.publish(Event('x'))
