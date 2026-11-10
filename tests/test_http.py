# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Exercise the HTTP/SSE server transport with a real socket + stdlib
urllib client — POST requests, SSE notifications, shutdown."""
from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator

import pytest

from bowire_plugin import BowirePlugin, InvokeResult, MethodInfo, ServiceInfo
from bowire_plugin._http import _HttpServer


class _Fake(BowirePlugin):
    id = "httpfake"
    name = "Http Fake"
    icon_svg = "<svg/>"

    def discover(self, server_url, show_internal, settings):
        return [ServiceInfo("Echo", package="httpfake", origin_url=server_url,
                            methods=[MethodInfo("echo", full_name="Echo/echo")])]

    def invoke(self, server_url, service, method, json_messages, show_internal, metadata,
               settings):
        return InvokeResult(response="echo: " + (json_messages[0] if json_messages else ""), status="OK")

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata,
                      settings) -> Iterator[str]:
        for i in range(1, 4):
            yield f'{{"tick":{i}}}'


@pytest.fixture
def server():
    srv = _HttpServer(_Fake(), host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    t.join(timeout=5)


def _post(port: int, envelope: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def test_http_initialize(server):
    out = _post(server.port, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert out["result"]["id"] == "httpfake"
    assert out["result"]["iconSvg"] == "<svg/>"


def test_http_discover_and_invoke(server):
    disc = _post(server.port, {"jsonrpc": "2.0", "id": 2, "method": "discover",
                               "params": {"serverUrl": "httpfake://x", "showInternalServices": False}})
    assert disc["result"][0]["name"] == "Echo"
    assert disc["result"][0]["originUrl"] == "httpfake://x"

    inv = _post(server.port, {"jsonrpc": "2.0", "id": 3, "method": "invoke",
                              "params": {"serverUrl": "httpfake://x", "service": "Echo", "method": "Echo/echo",
                                         "jsonMessages": ["hi"], "showInternalServices": False, "metadata": {}}})
    assert inv["result"]["response"] == "echo: hi"
    assert inv["result"]["status"] == "OK"


def test_http_invokestream_over_sse(server):
    # Open the SSE stream first so the events have a reader.
    sse = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5)
    try:
        ack = _post(server.port, {"jsonrpc": "2.0", "id": 4, "method": "invokeStream",
                                  "params": {"streamId": "s9", "serverUrl": "httpfake://x",
                                             "service": "Echo", "method": "Echo/echo",
                                             "jsonMessages": [], "showInternalServices": False, "metadata": {}}})
        assert ack["result"]["streamId"] == "s9"

        data_msgs: list[str] = []
        ended = False
        for raw in sse:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            note = json.loads(line[5:].strip())
            if note.get("method") == "$/stream/data":
                data_msgs.append(note["params"]["message"])
            elif note.get("method") == "$/stream/end":
                ended = True
                break
        assert ended
        assert data_msgs == ['{"tick":1}', '{"tick":2}', '{"tick":3}']
    finally:
        sse.close()


def test_http_unknown_method_errors(server):
    out = _post(server.port, {"jsonrpc": "2.0", "id": 5, "method": "nope", "params": {}})
    assert out["error"]["code"] == -32601


def test_a_hosts_shutdown_is_answered_and_the_server_keeps_serving(server):
    # An http sidecar outlives its hosts: the shutdown a host sends when it
    # lets go of the sidecar must not stop it for the next one.
    out = _post(server.port, {"jsonrpc": "2.0", "id": 6, "method": "shutdown", "params": {}})
    assert out["result"] is True
    again = _post(server.port, {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}})
    assert again["result"] == "pong"


def _stream_frames(sse) -> list[str]:
    frames: list[str] = []
    for raw in sse:
        line = raw.decode().strip()
        if not line.startswith("data:"):
            continue
        note = json.loads(line[5:].strip())
        if note.get("method") == "$/stream/data":
            frames.append(note["params"]["message"])
        elif note.get("method") == "$/stream/end":
            return frames
    raise AssertionError(f"the stream ended before $/stream/end; got {frames}")


def _invoke_stream(port: int, stream_id: str) -> None:
    _post(port, {"jsonrpc": "2.0", "id": 8, "method": "invokeStream",
                 "params": {"streamId": stream_id, "serverUrl": "httpfake://x",
                            "service": "Echo", "method": "Echo/echo", "jsonMessages": [],
                            "showInternalServices": False, "metadata": {}}})


def test_every_subscriber_gets_every_frame(server):
    # The subscribers used to share one queue and take turns: with two of
    # them, each frame reached only one, so a host lost frames to a stream
    # it was not reading - one left behind by a host that had gone.
    first = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5)
    second = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5)
    try:
        _invoke_stream(server.port, "s1")
        expected = ['{"tick":1}', '{"tick":2}', '{"tick":3}']
        assert _stream_frames(first) == expected
        assert _stream_frames(second) == expected
    finally:
        first.close()
        second.close()


def test_stopping_ends_the_open_sse_streams():
    srv = _HttpServer(_Fake(), host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    sse = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/", timeout=5)
    try:
        srv.shutdown()
        assert sse.read() == b""  # the stream ended rather than hanging
        t.join()
        assert not t.is_alive()
    finally:
        sse.close()
