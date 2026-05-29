# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""The JSON-RPC-over-stdio runtime that drives a :class:`BowirePlugin`.

Wire contract: NDJSON (one JSON-RPC 2.0 envelope per ``\\n``-terminated
UTF-8 line) over the process's stdin/stdout, exactly the framing Bowire's
host (and MCP's stdio transport) use. See the Bowire repo's
``docs/architecture/sidecar-plugins.md`` for the full spec.

Methods handled: initialize, ping, shutdown, discover, invoke,
invokeStream (host-minted streamId in params; we ack then emit
``$/stream/data`` notifications + ``$/stream/end``).
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, TextIO

from ._plugin import BowirePlugin


class _Runtime:
    def __init__(self, plugin: BowirePlugin, stdin: TextIO, stdout: TextIO) -> None:
        self._plugin = plugin
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._stream_threads: list[threading.Thread] = []

    # -- wire I/O -----------------------------------------------------

    def _write(self, envelope: dict[str, Any]) -> None:
        line = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            self._stdout.write(line)
            self._stdout.write("\n")
            self._stdout.flush()

    def _reply(self, req_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # -- main loop ----------------------------------------------------

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

            method = msg.get("method")
            req_id = msg.get("id")
            params = msg.get("params") or {}

            try:
                self._dispatch(method, req_id, params)
            except Exception as ex:  # noqa: BLE001 — surface as JSON-RPC error, never crash the loop
                if req_id is not None:
                    self._error(req_id, -32000, f"{type(ex).__name__}: {ex}")

            if self._stop.is_set():
                break

        # Let in-flight stream pumps finish flushing their $/stream/data
        # + $/stream/end notifications before we return (stdin closed or
        # shutdown received). Bounded so a hung generator can't wedge exit.
        for t in self._stream_threads:
            t.join(timeout=5)
        return 0

    def _dispatch(self, method: str | None, req_id: Any, params: dict[str, Any]) -> None:
        if method == "initialize":
            self._reply(req_id, {
                "name": self._plugin.name,
                "id": self._plugin.id,
                "iconSvg": self._plugin.icon_svg,
                "settings": [s.to_dict() for s in self._plugin.settings()],
            })
        elif method == "ping":
            self._reply(req_id, "pong")
        elif method == "shutdown":
            try:
                self._plugin.shutdown()
            finally:
                self._reply(req_id, True)
                self._stop.set()
        elif method == "discover":
            services = self._plugin.discover(
                params.get("serverUrl", ""),
                bool(params.get("showInternalServices", False)),
            )
            self._reply(req_id, [s.to_dict() for s in services])
        elif method == "invoke":
            result = self._plugin.invoke(
                params.get("serverUrl", ""),
                params.get("service", ""),
                params.get("method", ""),
                list(params.get("jsonMessages") or []),
                bool(params.get("showInternalServices", False)),
                dict(params.get("metadata") or {}),
            )
            self._reply(req_id, result.to_dict())
        elif method == "invokeStream":
            stream_id = params.get("streamId")
            # Ack immediately so the host knows the stream is accepted;
            # it already holds the streamId it minted.
            self._reply(req_id, {"streamId": stream_id})
            t = threading.Thread(
                target=self._pump_stream, args=(stream_id, params), daemon=True
            )
            self._stream_threads.append(t)
            t.start()
        else:
            if req_id is not None:
                self._error(req_id, -32601, f"method not found: {method}")

    def _pump_stream(self, stream_id: Any, params: dict[str, Any]) -> None:
        error: dict[str, Any] | None = None
        try:
            for message in self._plugin.invoke_stream(
                params.get("serverUrl", ""),
                params.get("service", ""),
                params.get("method", ""),
                list(params.get("jsonMessages") or []),
                bool(params.get("showInternalServices", False)),
                dict(params.get("metadata") or {}),
            ):
                if self._stop.is_set():
                    break
                self._notify("$/stream/data", {"streamId": stream_id, "message": message})
        except Exception as ex:  # noqa: BLE001
            error = {"code": -32000, "message": f"{type(ex).__name__}: {ex}"}
        finally:
            self._notify("$/stream/end", {"streamId": stream_id, "error": error})


def run(plugin: BowirePlugin, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run ``plugin`` against the JSON-RPC-over-stdio contract until the
    host sends ``shutdown`` (or stdin closes). Blocks; returns the
    process exit code (0).

    Wire ``stdin`` / ``stdout`` are overridable for testing; default to
    the process streams.
    """
    return _Runtime(plugin, stdin or sys.stdin, stdout or sys.stdout).run()
