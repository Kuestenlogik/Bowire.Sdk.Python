# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""HTTP/SSE server transport — the MCP-style streamable-HTTP shape.

The same :class:`_Dispatcher` that powers the stdio runtime drives an
HTTP service here: JSON-RPC requests arrive as ``POST`` (the HTTP
response body carries the reply); server-initiated notifications
(``$/stream/data`` …) stream back over a long-lived SSE ``GET`` on the
same path. Bowire's host wires to this with a ``"transport": "http"``
sidecar manifest pointing at the URL.

Unlike a stdio sidecar, an http one is not its host's: it runs on its own,
any number of hosts connect to it over its lifetime, and each holds an SSE
stream open while it uses the sidecar. So every subscriber gets every
notification, a host's ``shutdown`` is acknowledged without stopping the
server, and the server stops when its process is told to (SIGTERM, Ctrl-C)
or :meth:`_HttpServer.shutdown` is called.

Pure stdlib (``http.server``) — no extra dependencies.
"""
from __future__ import annotations

import json
import queue
import signal
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ._plugin import BowirePlugin
from ._runtime import _Dispatcher

# Sentinel pushed onto every subscriber's queue to end its SSE stream.
_STOP = object()


class _Subscribers:
    """The SSE streams open right now, one queue each.

    One shared queue used to feed them all, so subscribers took turns: a
    host that had gone away could still take an event off the queue, fail
    to write it and drop it, and the host that was waiting for it never got
    it. Each subscriber now has a queue of its own, and a notification goes
    into all of them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: set[queue.Queue[Any]] = set()
        self._closed = False

    def add(self) -> queue.Queue[Any]:
        q: queue.Queue[Any] = queue.Queue()
        with self._lock:
            if self._closed:
                q.put(_STOP)
            else:
                self._queues.add(q)
        return q

    def remove(self, q: queue.Queue[Any]) -> None:
        with self._lock:
            self._queues.discard(q)

    def emit(self, event: Any) -> None:
        with self._lock:
            for q in self._queues:
                q.put(event)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for q in self._queues:
                q.put(_STOP)


class _Server(ThreadingHTTPServer):
    """A host that goes away resets its keep-alive connections; that is a
    host leaving, not an error, so it is not printed as a traceback."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class _HttpServer:
    """A running HTTP/SSE sidecar server. Reusable from tests
    (start/port/shutdown); :func:`run_http` wraps it for the blocking
    process entry point."""

    def __init__(self, plugin: BowirePlugin, host: str = "127.0.0.1", port: int = 8770) -> None:
        subscribers = _Subscribers()
        dispatcher = _Dispatcher(plugin, subscribers.emit)
        self._subscribers = subscribers
        self._dispatcher = dispatcher

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
                # A host's `shutdown` is the host leaving, not the sidecar
                # being told to stop: it is answered, and the server keeps
                # serving the hosts still connected and the ones to come.
                reply, post = dispatcher.handle(msg.get("method"), msg.get("id"), msg.get("params") or {})
                self._send_json(200, reply if reply is not None else {})
                if post is not None:
                    post()  # start streaming only after the ack response is sent

            def do_GET(self) -> None:
                # Subscribed before the headers go out, so a host that has
                # them can rely on getting every notification after.
                q = subscribers.add()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    while True:
                        evt = q.get()
                        if evt is _STOP:
                            break
                        payload = json.dumps(evt, separators=(",", ":"), ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # the host went away
                finally:
                    subscribers.remove(q)
                    # The stream ends with this response; the connection is
                    # not reused for another request.
                    self.close_connection = True

            def _send_json(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = _Server((host, port), Handler)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        finally:
            self._subscribers.close()
            self._dispatcher.join_streams(timeout=2)
            self._server.server_close()

    def shutdown(self) -> None:
        """Stop serving: end every open SSE stream, then let
        :meth:`serve_forever` return. Safe from any thread but the one
        running :meth:`serve_forever`."""
        self._subscribers.close()
        self._server.shutdown()


def run_http(
    plugin: BowirePlugin,
    *,
    host: str = "127.0.0.1",
    port: int = 8770,
    on_listening: Callable[[int], None] | None = None,
) -> int:
    """Serve ``plugin`` over HTTP/SSE on ``host:port`` until the process is
    told to stop: SIGTERM (what a container manager sends) or Ctrl-C. Blocks;
    returns 0 once it has stopped cleanly. A host's ``shutdown`` does not
    stop it: an http sidecar outlives the hosts that connect to it.

    ``on_listening`` is called with the bound port once the socket accepts
    connections, before serving starts: with ``port=0`` the OS picks the
    port, and this is how a caller learns which one.

    Point a sidecar manifest at it:

        { "transport": "http", "url": "http://127.0.0.1:8770/" }
    """
    server = _HttpServer(plugin, host, port)

    # serve_forever runs on this thread, and shutdown() waits for it to
    # return, so the handlers hand the stop to a thread of its own.
    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

    if on_listening is not None:
        on_listening(server.port)
    server.serve_forever()
    return 0
