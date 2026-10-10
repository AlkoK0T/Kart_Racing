"""Opt-in real compiler/loopback test: set DELPHI_DCC32 to a licensed dcc32.exe."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest


@unittest.skipUnless(os.environ.get('DELPHI_DCC32'), 'DELPHI_DCC32 compiler opt-in is not set')
class DelphiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([os.environ['DELPHI_DCC32'], '-Q', '-B',
            '-E' + cls.folder.name, '-N' + cls.folder.name,
            str(root / 'delphi/RecordsSenderSelfTest.dpr')], cwd=root / 'delphi',
            capture_output=True, timeout=60)
        cls.executable = Path(cls.folder.name) / 'RecordsSenderSelfTest.exe'
        if result.returncode or not cls.executable.exists():
            raise AssertionError('Delphi compilation failed: ' + result.stdout.decode(errors='replace') +
                                 result.stderr.decode(errors='replace'))

    def test_packet_and_json_parser(self):
        data = json.loads(subprocess.check_output([str(self.executable)], timeout=10).decode('utf-8'))
        self.assertEqual(data['data']['children']['name'], 'Маша')
        self.assertIsNone(data['data']['women'])
        self.assertEqual(data['data']['date'], '2026-10-10')

    def test_real_loopback_ack(self):
        from karting import Runtime
        from karting.modules import TcpJsonSource
        runtime = Runtime()
        server = TcpJsonSource(runtime.store, '127.0.0.1', 0)
        server.start()
        try:
            subprocess.check_output([str(self.executable), '--send', str(server._server.server_address[1])], timeout=10)
            self.assertEqual(runtime.store.get('records_day')['children']['name'], 'Маша')
        finally:
            server.stop()
            runtime.close()

    def test_deadline_and_queue_cancel_with_silent_server(self):
        for mode in ('--deadline', '--cancel'):
            with self.subTest(mode=mode), socket.socket() as listener:
                listener.bind(('127.0.0.1', 0)); listener.listen()
                stopping = threading.Event()
                def silent():
                    listener.settimeout(8)
                    connection, _ = listener.accept()
                    with connection:
                        stopping.wait(8)
                worker = threading.Thread(target=silent)
                worker.start()
                try:
                    subprocess.check_output([str(self.executable), mode,
                                             str(listener.getsockname()[1])], timeout=8)
                finally:
                    stopping.set(); worker.join(9)
                self.assertFalse(worker.is_alive())
