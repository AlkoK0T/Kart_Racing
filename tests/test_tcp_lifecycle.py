import json
import socket
import unittest
from threading import Event as Signal, Thread
from unittest.mock import patch

from karting.core import Runtime
from karting.modules.tcp import TcpJsonSource


class TcpLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()

    def source(self, **kwargs):
        source = TcpJsonSource(self.runtime.store, port=0, **kwargs)
        source.start()
        self.addCleanup(source.stop)
        return source

    def connect(self, source):
        client = socket.create_connection(source._server.server_address, timeout=2)
        self.addCleanup(client.close)
        return client

    def test_stop_closes_existing_client_and_prevents_further_writes(self):
        source = self.source()
        client = self.connect(source)
        client.sendall(b'{"category":"temperature","data":20}\n')
        self.assertTrue(json.loads(client.recv(4096))['ok'])
        source.stop()
        revision = self.runtime.store.revision
        try:
            client.sendall(b'{"category":"temperature","data":99}\n')
        except OSError:
            pass
        try:
            self.assertEqual(b'', client.recv(4096))
        except (ConnectionResetError, ConnectionAbortedError):
            pass  # Windows may report reset rather than EOF for a closed peer.
        self.assertEqual(20, self.runtime.store.get('temperature'))
        self.assertEqual(revision, self.runtime.store.revision)

    def test_pending_parsed_message_cannot_commit_after_stop_gate(self):
        source = self.source()
        server = source._server
        entered, release = Signal(), Signal()
        from karting.modules.tcp import parse_json
        def blocked(raw, limits):
            entered.set()
            release.wait(2)
            return parse_json(raw, limits)
        with patch('karting.modules.tcp.parse_json', side_effect=blocked):
            client = self.connect(source)
            client.sendall(b'{"category":"temperature","data":99}\n')
            self.assertTrue(entered.wait(1))
            stop_errors = []
            def stop():
                try:
                    source.stop()
                except Exception as exc:
                    stop_errors.append(exc)
            stopper = Thread(target=stop)
            stopper.start()
            try:
                self.assertTrue(server.stopping.wait(1))
            finally:
                release.set(); stopper.join(3)
            self.assertFalse(stopper.is_alive())
            self.assertEqual([], stop_errors)
        self.assertEqual(0, self.runtime.store.revision)

    def test_max_clients_rejects_excess_connection(self):
        source = self.source(max_clients=1)
        first = self.connect(source)
        first.sendall(b'{"category":"temperature","data":20}\n')
        self.assertTrue(json.loads(first.recv(4096))['ok'])
        second = self.connect(source)
        self.assertFalse(json.loads(second.recv(4096))['ok'])
        self.assertEqual(1, len(source._server.clients))

    def test_idle_timeout_and_partial_frame_deadline_close_client(self):
        for payload, options in ((b'', {'client_timeout': 0.05}),
                                 (b'{"category":', {'frame_timeout': 0.05})):
            source = self.source(**options)
            client = self.connect(source)
            if payload:
                client.sendall(payload)
            self.assertEqual(b'', client.recv(4096))
            source.stop()
            self.assertEqual(0, self.runtime.store.revision)

    def test_unterminated_eof_is_not_a_message(self):
        source = self.source()
        client = self.connect(source)
        client.sendall(b'{"category":"temperature","data":99}')
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(b'', client.recv(4096))
        self.assertEqual(0, self.runtime.store.revision)

    def test_invalid_limits_are_rejected_before_start(self):
        for options in ({'max_clients': 0}, {'max_clients': True},
                        {'frame_timeout': float('inf')}, {'client_timeout': -1}):
            with self.assertRaises(ValueError):
                TcpJsonSource(self.runtime.store, **options)
