from __future__ import annotations

import json
import logging
import math
import socket
import socketserver
import time
from threading import BoundedSemaphore, Event as ThreadEvent, RLock, Thread, current_thread

from karting.core import DataStore
from karting.domain import ValidationError
from karting.ingestion import DataIngestor, parse_json

log = logging.getLogger(__name__)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        log.info("TCP client connected address=%s:%s", *self.client_address)
        try:
            self._receive()
        except (OSError, TimeoutError) as exc:
            log.debug('TCP connection ended address=%s:%s reason=%s', *self.client_address, exc)
        finally:
            log.info("TCP client disconnected address=%s:%s", *self.client_address)

    def _reply(self, answer):
        self.connection.settimeout(self.server.client_timeout)
        self.wfile.write(json.dumps(answer).encode() + b'\n')

    def _receive(self):
        server = self.server
        pending = bytearray()
        deadline = None
        idle_deadline = time.monotonic() + server.client_timeout
        while not server.stopping.is_set():
            now = time.monotonic()
            remaining = idle_deadline - now if deadline is None else min(idle_deadline - now, deadline - now)
            if remaining <= 0:
                raise TimeoutError('TCP frame deadline expired')
            # Windows shutdown(SHUT_RD) may not interrupt an outstanding recv.
            self.connection.settimeout(min(remaining, 0.25))
            try:
                chunk = self.connection.recv(min(8192, server.max_line_bytes + 1))
            except socket.timeout:
                continue
            if not chunk:
                if pending:
                    log.warning('TCP discarded unterminated frame address=%s:%s', *self.client_address)
                return
            if not pending:
                deadline = time.monotonic() + server.frame_timeout
            idle_deadline = time.monotonic() + server.client_timeout
            pending.extend(chunk)
            while b'\n' in pending:
                end = pending.index(b'\n') + 1
                if end > server.max_line_bytes:
                    self._reply({'ok': False, 'error': 'message too large', 'field': 'message'})
                    return
                line = bytes(pending[:end]); del pending[:end]
                log.debug('TCP received address=%s:%s raw=%r', *self.client_address, line)
                try:
                    message = parse_json(line, server.ingestor.registry.limits)
                    with server.gate:
                        if server.stopping.is_set():
                            return
                        server.ingestor.message(message, source=f'tcp:{self.client_address[0]}:{self.client_address[1]}')
                    self._reply({'ok': True})
                except ValidationError as exc:
                    log.warning('TCP invalid message client=%s error=%s', self.client_address[0], exc)
                    self._reply({'ok': False, 'error': str(exc), 'field': exc.field})
                deadline = time.monotonic() + server.frame_timeout if pending else None
            if len(pending) > server.max_line_bytes:
                self._reply({'ok': False, 'error': 'message too large', 'field': 'message'})
                return


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, ingestor, max_clients, client_timeout, frame_timeout):
        self.ingestor = ingestor
        self.max_line_bytes = ingestor.registry.limits.max_bytes
        self.client_timeout, self.frame_timeout = client_timeout, frame_timeout
        self.gate = RLock()
        self.stopping = ThreadEvent()
        self.slots = BoundedSemaphore(max_clients)
        self.clients = set()
        self.workers = set()
        super().__init__(address, _Handler)

    def process_request(self, request, address):
        with self.gate:
            if self.stopping.is_set() or not self.slots.acquire(blocking=False):
                try:
                    request.settimeout(1)
                    request.sendall(b'{"ok":false,"error":"server busy or stopping"}\n')
                except OSError:
                    pass
                self.shutdown_request(request)
                return
            self.clients.add(request)
            worker = Thread(target=self._client, args=(request, address), name='tcp-client', daemon=True)
            self.workers.add(worker)
            try:
                worker.start()
            except Exception:
                self.workers.discard(worker); self.clients.discard(request)
                self.slots.release(); self.shutdown_request(request)
                raise

    def _client(self, request, address):
        try:
            self.process_request_thread(request, address)
        finally:
            with self.gate:
                self.clients.discard(request)
                self.workers.discard(current_thread())
                self.slots.release()

    def stop_clients(self):
        with self.gate:
            self.stopping.set()
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def join_clients(self):
        with self.gate:
            workers = tuple(self.workers)
        deadline = time.monotonic() + 3
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('TCP client workers did not stop')


class TcpJsonSource:
    """Newline-delimited JSON input: {"category": "temperature", "data": 21.5}."""

    name = "tcp-json"

    def __init__(self, store: DataStore, host: str = "127.0.0.1", port: int = 8765,
                 *, ingestor: DataIngestor | None = None, max_clients: int = 16,
                 client_timeout: float = 30, frame_timeout: float = 10) -> None:
        if type(max_clients) is not int or max_clients < 1:
            raise ValueError('max_clients must be a positive integer')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in (client_timeout, frame_timeout)):
            raise ValueError('TCP timeouts must be finite and positive')
        self._store = store
        self._ingestor = ingestor if ingestor is not None else DataIngestor(store)
        self._address = (host, port)
        self._limits = max_clients, client_timeout, frame_timeout
        self._server: _Server | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._server = _Server(self._address, self._ingestor, *self._limits)
        self._thread = Thread(target=self._server.serve_forever, name=self.name, daemon=True)
        self._thread.start()
        log.info("TCP JSON server listening address=%s:%s", *self._server.server_address)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.stop_clients()
        if self._thread and self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        self._server.join_clients()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError('TCP server worker did not stop')
        self._thread = None
        self._server = None
        log.info("TCP JSON server stopped")
