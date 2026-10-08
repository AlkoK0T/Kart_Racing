from __future__ import annotations

import json
import logging
import socketserver
from threading import Thread
from typing import Any

from karting.core import DataStore
from karting.modules.files import normalize_race

log = logging.getLogger(__name__)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        store: DataStore = self.server.store  # type: ignore[attr-defined]
        limit: int = self.server.max_line_bytes  # type: ignore[attr-defined]
        log.info("TCP client connected address=%s:%s", *self.client_address)
        while line := self.rfile.readline(limit + 1):
            log.debug("TCP received address=%s:%s raw=%r", *self.client_address, line)
            if len(line) > limit:
                self.wfile.write(b'{"ok":false,"error":"message too large"}\n')
                return
            try:
                message: Any = json.loads(line)
                category = message["category"]
                if not isinstance(category, str) or not category:
                    raise ValueError("category must be a non-empty string")
                value = message.get("data")
                if category == "current_race":
                    value = normalize_race(value, "laps.yaml")
                store.set(category, value, source=f"tcp:{self.client_address[0]}")
                log.debug("TCP category updated category=%s client=%s", category, self.client_address[0])
                self.wfile.write(b'{"ok":true}\n')
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                log.warning("TCP invalid message client=%s error=%s", self.client_address[0], exc)
                answer = json.dumps({"ok": False, "error": str(exc)}).encode()
                self.wfile.write(answer + b"\n")
        log.info("TCP client disconnected address=%s:%s", *self.client_address)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class TcpJsonSource:
    """Newline-delimited JSON input: {"category": "temperature", "data": 21.5}."""

    name = "tcp-json"

    def __init__(self, store: DataStore, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._store = store
        self._address = (host, port)
        self._server: _Server | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        self._server = _Server(self._address, _Handler)
        self._server.store = self._store  # type: ignore[attr-defined]
        self._server.max_line_bytes = 1024 * 1024  # type: ignore[attr-defined]
        self._thread = Thread(target=self._server.serve_forever, name=self.name, daemon=True)
        self._thread.start()
        log.info("TCP JSON server listening address=%s:%s", *self._server.server_address)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        log.info("TCP JSON server stopped")
