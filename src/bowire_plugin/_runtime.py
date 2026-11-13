# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""The JSON-RPC dispatch + stdio runtime that drive a :class:`BowirePlugin`.

Wire contract: NDJSON (one JSON-RPC 2.0 envelope per ``\\n``-terminated
UTF-8 line) over the process's stdin/stdout, exactly the framing Bowire's
host (and MCP's stdio transport) use. See the Bowire repo's
``docs/architecture/sidecar-plugins.md`` for the full spec.

The transport-agnostic dispatch lives in :class:`_Dispatcher`; the stdio
runtime and the HTTP/SSE server (in ``_http``) both reuse it.

Methods handled: initialize, ping, shutdown, discover, invoke,
invokeStream (host-minted streamId in params; ack then emit
``$/stream/data`` notifications + ``$/stream/end``).

Every request belongs to a session (see ``_sessions``): over stdio there is
one, the pipe; over http the transport passes the id the host sent. A stream
belongs to the session that started it.

The ``initialize`` reply carries ``protocolVersion`` +
``capabilities`` (#416), so the host knows which contract it is talking to
and which calls it can skip.

``discover`` / ``invoke`` / ``invokeStream`` params may carry a ``settings``
object (Kuestenlogik/Bowire#693): the values set for the settings the plugin
declared. Every call hands the plugin a fresh :class:`Settings` built from it,
so a changed value applies from the next call on.
"""
from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from ._models import PluginSetting
from ._plugin import BowirePlugin
from ._sessions import SessionId, _SessionStreams, _Stream
from ._settings import Settings

# A notification sink: hands a fully-formed JSON-RPC notification envelope,
# and the session that owns the stream it is about, to whichever transport
# is in play (stdout line / the SSE streams of that session).
EmitFn = Callable[[dict[str, Any], SessionId], None]

#: The sidecar wire-contract version this SDK speaks (#416). The host
#: accepts a sidecar inside its supported range and refuses one outside it
#: at the handshake, rather than failing at the first call. A sidecar that
#: sends none at all is tolerated as contract v1 — with a warning in the
#: host log on every boot, which is what this SDK used to earn.
SIDECAR_PROTOCOL_VERSION = 1


def _capabilities(plugin: BowirePlugin) -> dict[str, bool]:
    """What this plugin can actually answer, read off the subclass.

    A flag set to ``False`` lets the host skip the call entirely. The base
    class ships a polite default for every method — ``discover`` returns
    ``[]``, ``invoke`` an "not implemented" result — so "did the author
    override it?" is the only honest answer to "can it do this?", and the
    host gets to stop asking questions whose answer is a stub.

    ``channels`` is always ``False``: the Python SDK has no channel surface
    at all, so the host's ``openChannel`` would round-trip to a method-not-
    found every time an operator opened a duplex method.
    """
    cls = type(plugin)
    return {
        "discover": cls.discover is not BowirePlugin.discover,
        "invoke": cls.invoke is not BowirePlugin.invoke,
        "invokeStream": cls.invoke_stream is not BowirePlugin.invoke_stream,
        "channels": False,
    }


class _Dispatcher:
    """Transport-agnostic JSON-RPC dispatch over a :class:`BowirePlugin`.

    :meth:`handle` turns one request into its reply envelope (or
    ``None`` for notifications / post-shutdown). Streaming frames are
    pushed through the injected :data:`EmitFn` so stdio and HTTP route
    them to their own wire.

    ``host_owned`` says what a ``shutdown`` request means. ``True`` (stdio):
    the one host owns the process and is stopping it, so the request is
    :meth:`stop`. ``False`` (http): a host is leaving a sidecar that stays,
    so the request ends that host's session and nothing else.
    """

    def __init__(self, plugin: BowirePlugin, emit: EmitFn, *, host_owned: bool = True) -> None:
        self._plugin = plugin
        self._emit = emit
        self._host_owned = host_owned
        self._stop = threading.Event()
        self._stop_lock = threading.Lock()
        self._streams = _SessionStreams()
        self._declared: list[PluginSetting] | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _declared_settings(self) -> list[PluginSetting]:
        """What the plugin declared, asked once: the ``initialize`` reply and
        every call's defaults have to agree on it. Built lazily rather than in
        ``__init__`` so a plugin whose ``settings()`` raises fails the
        handshake as a JSON-RPC error instead of the process start."""
        if self._declared is None:
            self._declared = list(self._plugin.settings())
        return self._declared

    def _call_settings(self, params: dict[str, Any]) -> Settings:
        # Absent when nobody set anything — the common case, not an error.
        raw = params.get("settings")
        return Settings(raw if isinstance(raw, dict) else None, self._declared_settings())

    def join_streams(self, timeout: float = 5) -> None:
        """Let in-flight stream pumps flush their final frames on exit."""
        for t in self._streams.threads():
            t.join(timeout=timeout)

    def end_session(self, session: SessionId) -> None:
        """A host left: stop the streams its session owns. Every other
        session runs on, and the plugin's ``shutdown`` hook does not run.
        Ending a session twice is harmless."""
        self._streams.end(session)

    def stop(self) -> None:
        """The sidecar itself is stopping: stop every stream, then run the
        plugin's ``shutdown`` hook. The hook runs once, however often this
        is called."""
        with self._stop_lock:
            if self._stop.is_set():
                return
            self._stop.set()
        self._streams.end_all()
        self._plugin.shutdown()

    def handle(
        self, method: str | None, req_id: Any, params: dict[str, Any], session: SessionId = None
    ) -> tuple[dict[str, Any] | None, Callable[[], None] | None]:
        """Return ``(reply, post_action)``. The caller writes ``reply``
        (nothing if ``None``), then runs ``post_action`` if present.
        Streaming starts in ``post_action`` so the ack is always on the
        wire before the first ``$/stream/data`` frame.

        ``session`` is the session the request came in on; a stream the
        request starts belongs to it."""
        try:
            result, post = self._invoke_method(method, params, session)
        except _MethodNotFound:
            return (None if req_id is None else _err(req_id, -32601, f"method not found: {method}")), None
        except Exception as ex:  # noqa: BLE001 — surface as JSON-RPC error, never crash the loop
            return (None if req_id is None else _err(req_id, -32000, f"{type(ex).__name__}: {ex}")), None
        reply = None if req_id is None else {"jsonrpc": "2.0", "id": req_id, "result": result}
        return reply, post

    def _invoke_method(
        self, method: str | None, params: dict[str, Any], session: SessionId
    ) -> tuple[Any, Callable[[], None] | None]:
        if method == "initialize":
            reply: dict[str, Any] = {
                "name": self._plugin.name,
                "id": self._plugin.id,
                "iconSvg": self._plugin.icon_svg,
                "settings": [s.to_dict() for s in self._declared_settings()],
                "protocolVersion": SIDECAR_PROTOCOL_VERSION,
                "capabilities": _capabilities(self._plugin),
            }
            # Kuestenlogik/Bowire#1003 - absent when the plugin declares none.
            form = self._plugin.manual_entry()
            if form is not None:
                reply["manualEntry"] = form.to_dict()
            return reply, None
        if method == "ping":
            return "pong", None
        if method == "shutdown":
            if self._host_owned:
                self.stop()
            elif session is not None:
                self.end_session(session)
            # A host that named no session is acknowledged and ends nothing:
            # the unnamed session is shared by every such host, and there is
            # no telling whose streams these would be.
            return True, None
        if method == "discover":
            services = self._plugin.discover(
                params.get("serverUrl", ""),
                bool(params.get("showInternalServices", False)),
                self._call_settings(params),
            )
            return [s.to_dict() for s in services], None
        if method == "invoke":
            return self._plugin.invoke(
                params.get("serverUrl", ""),
                params.get("service", ""),
                params.get("method", ""),
                list(params.get("jsonMessages") or []),
                bool(params.get("showInternalServices", False)),
                dict(params.get("metadata") or {}),
                self._call_settings(params),
            ).to_dict(), None
        if method == "invokeStream":
            stream_id = params.get("streamId")
            # Owned before the ack goes out, so a session that ends right
            # after it still finds the stream.
            stream = self._streams.open(session)

            def start_pump() -> None:
                t = threading.Thread(
                    target=self._pump_stream, args=(stream_id, params, session, stream), daemon=True
                )
                stream.thread = t
                t.start()

            # ack now; the caller starts the pump after writing it
            return {"streamId": stream_id}, start_pump
        raise _MethodNotFound

    def _pump_stream(
        self, stream_id: Any, params: dict[str, Any], session: SessionId, stream: _Stream
    ) -> None:
        error: dict[str, Any] | None = None
        try:
            if not stream.stopped.is_set():
                self._pump_messages(stream_id, params, session, stream)
        except Exception as ex:  # noqa: BLE001
            error = {"code": -32000, "message": f"{type(ex).__name__}: {ex}"}
        finally:
            try:
                self._emit({"jsonrpc": "2.0", "method": "$/stream/end",
                            "params": {"streamId": stream_id, "error": error}}, session)
            finally:
                self._streams.discard(session, stream)

    def _pump_messages(
        self, stream_id: Any, params: dict[str, Any], session: SessionId, stream: _Stream
    ) -> None:
        messages = iter(self._plugin.invoke_stream(
            params.get("serverUrl", ""),
            params.get("service", ""),
            params.get("method", ""),
            list(params.get("jsonMessages") or []),
            bool(params.get("showInternalServices", False)),
            dict(params.get("metadata") or {}),
            self._call_settings(params),
        ))
        try:
            for message in messages:
                if stream.stopped.is_set():
                    break
                self._emit({"jsonrpc": "2.0", "method": "$/stream/data",
                            "params": {"streamId": stream_id, "message": message}}, session)
        finally:
            # A stopped stream is closed here rather than whenever the
            # iterator is collected, so the plugin's own cleanup (a
            # generator's ``finally`` / ``with``) runs now.
            close = getattr(messages, "close", None)
            if close is not None:
                close()


class _MethodNotFound(Exception):
    pass


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class _StdioRuntime:
    """NDJSON JSON-RPC over stdin/stdout, driving a :class:`_Dispatcher`."""

    def __init__(self, plugin: BowirePlugin, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        # One pipe, one host: every notification goes down it, whatever
        # its session.
        self._dispatcher = _Dispatcher(plugin, lambda envelope, _session: self._write(envelope))

    def _write(self, envelope: dict[str, Any]) -> None:
        line = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            self._stdout.write(line)
            self._stdout.write("\n")
            self._stdout.flush()

    def run(self) -> int:
        for raw in self._stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            reply, post = self._dispatcher.handle(msg.get("method"), msg.get("id"), msg.get("params") or {})
            if reply is not None:
                self._write(reply)
            if post is not None:
                post()  # start streaming only after the ack is on the wire
            if self._dispatcher.stopped:
                break

        self._dispatcher.join_streams()
        # stdin closed without a `shutdown`: the host is gone, and with it
        # the process. The plugin is told all the same. After a `shutdown`
        # this finds the sidecar already stopped and does nothing.
        self._dispatcher.stop()
        return 0


def run(plugin: BowirePlugin, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run ``plugin`` against the JSON-RPC-over-stdio contract until the
    host sends ``shutdown`` (or stdin closes). Blocks; returns 0.

    For the HTTP/SSE transport use :func:`bowire_plugin.run_http`.
    """
    return _StdioRuntime(plugin, stdin or sys.stdin, stdout or sys.stdout).run()
