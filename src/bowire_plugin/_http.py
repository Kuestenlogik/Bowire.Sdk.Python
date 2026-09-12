# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""HTTP/SSE server transport — the MCP-style streamable-HTTP shape.

The same :class:`_Dispatcher` that powers the stdio runtime drives an
HTTP service here: JSON-RPC requests arrive as ``POST`` (the HTTP
response body carries the reply); server-initiated notifications
(``$/stream/data`` / ``$/channel/data`` …) stream back over one
long-lived SSE ``GET`` on the same path. Bowire's host wires to this
with a ``"transport": "http"`` sidecar manifest pointing at the URL.

Pure stdlib (``http.server``) — no extra dependencies.
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ._plugin import BowirePlugin
from ._runtime import _Dispatcher

# Sentinel pushed onto the SSE queue to wake the GET loop for shutdown.
_STOP = object()


class _HttpServer:
    """A running HTTP/SSE sidecar server. Reusable from tests
    (start/port/shutdown); :func:`run_http` wraps it for the blocking
    process entry point."""

    def __init__(self, plugin: BowirePlugin, host: str = "127.0.0.1", port: int = 8770) -> None:
        # One SSE event queue drained by the connected GET client. emit()
        # from the dispatcher (incl. its stream-pump threads) lands here.
        self._sse: queue.Queue[Any] = queue.Queue()
        self._dispatcher = _Dispatcher(plugin, self._sse.put)
        self._stop = threading.Event()

        dispatcher = self._dispatcher
        sse = self._sse
        stop = self._stop

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence default stderr logging
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b""
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid json"})
                    return
                reply, post = dispatcher.handle(msg.get("method"), msg.get("id"), msg.get("params") or {})
                self._send_json(200, reply if reply is not None else {})
                if post is not None:
                    post()  # start streaming only after the ack response is sent
                if dispatcher.stopped:
                    # shutdown handled — wake the SSE loop + stop serving
                    # (on a separate thread; can't stop from a handler).
                    stop.set()
                    sse.put(_STOP)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()

            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while not stop.is_set():
                    try:
                        evt = sse.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if evt is _STOP:
                        break
                    try:
                        payload = json.dumps(evt, separators=(",", ":"), ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def _send_json(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer((host, port), Handler)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        finally:
            self._stop.set()
            self._dispatcher.join_streams(timeout=2)
            self._server.server_close()

    def shutdown(self) -> None:
        self._stop.set()
        self._sse.put(_STOP)
        self._server.shutdown()


def run_http(plugin: BowirePlugin, *, host: str = "127.0.0.1", port: int = 8770) -> int:
    """Serve ``plugin`` over HTTP/SSE on ``host:port`` until the host
    sends ``shutdown``. Blocks; returns 0.

    Point a sidecar manifest at it:

        { "transport": "http", "url": "http://127.0.0.1:8770/" }
    """
    _HttpServer(plugin, host, port).serve_forever()
    return 0
