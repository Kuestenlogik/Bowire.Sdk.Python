# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Exercise the HTTP/SSE server transport with a real socket + stdlib
urllib client — POST requests, SSE notifications, sessions, shutdown."""
from __future__ import annotations

import json
import queue
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


def _session_header(session: str | None) -> dict[str, str]:
    return {} if session is None else {"Bowire-Session": session}


def _post(port: int, envelope: dict, session: str | None = None) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json", **_session_header(session)},
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


# --- Sessions: several hosts on one sidecar -------------------------------


class _Feed:
    """One stream the test drives by hand: what goes into ``frames`` is
    yielded, ``None`` ends it, and ``closed`` is set once the plugin's
    generator has been closed or has run out."""

    def __init__(self) -> None:
        self.frames: queue.Queue[str | None] = queue.Queue()
        self.closed = threading.Event()


class _Fed(BowirePlugin):
    """Streams what the test feeds it: one feed per method name, so two
    hosts can each hold a stream the test controls frame by frame."""

    id = "fed"
    name = "Fed"

    def __init__(self, *methods: str) -> None:
        self.feeds = {method: _Feed() for method in methods}
        self.shutdowns = 0

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata,
                      settings) -> Iterator[str]:
        feed = self.feeds[method]
        try:
            while (frame := feed.frames.get()) is not None:
                yield frame
        finally:
            feed.closed.set()

    def shutdown(self) -> None:
        self.shutdowns += 1

    def finish(self) -> None:
        for feed in self.feeds.values():
            feed.frames.put(None)


class _Sidecar:
    """A running server over a :class:`_Fed` plugin, plus the two moments a
    test has to wait for instead of guessing: an SSE stream the server has
    seen close, and a session it has ended."""

    def __init__(self, *methods: str) -> None:
        self.plugin = _Fed(*methods)
        self.server = _HttpServer(self.plugin, host="127.0.0.1", port=0)
        self.port = self.server.port
        self.events: queue.Queue[tuple[str, str | None]] = queue.Queue()

        remove = self.server._subscribers.remove
        end_session = self.server._dispatcher.end_session

        def watched_remove(session, q):
            last = remove(session, q)
            self.events.put(("sse-closed", session))
            return last

        def watched_end_session(session):
            end_session(session)
            self.events.put(("session-ended", session))

        self.server._subscribers.remove = watched_remove
        self.server._dispatcher.end_session = watched_end_session
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.plugin.finish()  # no stream is left waiting for a frame
        self.server.shutdown()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()

    def sse(self, session: str | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/",
                                     headers=_session_header(session))
        return urllib.request.urlopen(req, timeout=5)

    def post(self, method: str, session: str | None = None) -> dict:
        return _post(self.port, {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}, session)

    def start_stream(self, method: str, stream_id: str, session: str | None = None) -> None:
        ack = _post(self.port, {"jsonrpc": "2.0", "id": 1, "method": "invokeStream",
                                "params": {"streamId": stream_id, "serverUrl": "fed://x",
                                           "service": "Feed", "method": method, "jsonMessages": [],
                                           "showInternalServices": False, "metadata": {}}}, session)
        assert ack["result"]["streamId"] == stream_id

    def feed(self, method: str, frame: str) -> None:
        self.plugin.feeds[method].frames.put(frame)

    def stream_closed(self, method: str) -> bool:
        return self.plugin.feeds[method].closed.wait(timeout=5)

    def stream_running(self, method: str) -> bool:
        return not self.plugin.feeds[method].closed.is_set()

    def next_event(self) -> tuple[str, str | None]:
        return self.events.get(timeout=5)


@pytest.fixture
def sidecar():
    running = _Sidecar("a", "b")
    yield running
    running.stop()


def _next_note(sse) -> dict:
    """The next notification on an SSE stream."""
    for raw in sse:
        line = raw.decode().strip()
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError("the SSE stream ended")


def _data(stream_id: str, message: str) -> dict:
    return {"jsonrpc": "2.0", "method": "$/stream/data",
            "params": {"streamId": stream_id, "message": message}}


def _end(stream_id: str) -> dict:
    return {"jsonrpc": "2.0", "method": "$/stream/end",
            "params": {"streamId": stream_id, "error": None}}


def test_a_notification_goes_only_to_the_session_that_owns_the_stream(sidecar):
    host_a = sidecar.sse("host-a")
    host_b = sidecar.sse("host-b")
    try:
        sidecar.start_stream("a", "sa", "host-a")
        sidecar.start_stream("b", "sb", "host-b")

        # Each read is the next thing on that host's stream: a frame that
        # had leaked across would be sitting there first.
        sidecar.feed("b", "b1")
        assert _next_note(host_b) == _data("sb", "b1")
        sidecar.feed("a", "a1")
        assert _next_note(host_a) == _data("sa", "a1")
        sidecar.feed("b", "b2")
        assert _next_note(host_b) == _data("sb", "b2")
    finally:
        host_a.close()
        host_b.close()


def test_two_hosts_may_pick_the_same_stream_id(sidecar):
    # The host mints the stream id, so two hosts can mint the same one; the
    # session is what tells the streams apart.
    host_a = sidecar.sse("host-a")
    host_b = sidecar.sse("host-b")
    try:
        sidecar.start_stream("a", "s1", "host-a")
        sidecar.start_stream("b", "s1", "host-b")
        sidecar.feed("a", "from-a")
        assert _next_note(host_a) == _data("s1", "from-a")
        sidecar.feed("b", "from-b")
        assert _next_note(host_b) == _data("s1", "from-b")
    finally:
        host_a.close()
        host_b.close()


def test_a_hosts_shutdown_ends_its_session_and_no_other(sidecar):
    host_a = sidecar.sse("host-a")
    host_b = sidecar.sse("host-b")
    try:
        sidecar.start_stream("a", "sa", "host-a")
        sidecar.start_stream("b", "sb", "host-b")
        sidecar.feed("a", "a1")
        assert _next_note(host_a) == _data("sa", "a1")
        sidecar.feed("b", "b1")
        assert _next_note(host_b) == _data("sb", "b1")

        assert sidecar.post("shutdown", "host-a")["result"] is True

        # The host that left: its stream is stopped where it next yields,
        # so the frame is not delivered and the plugin's generator is closed.
        sidecar.feed("a", "a2")
        assert sidecar.stream_closed("a")
        assert _next_note(host_a) == _end("sa")

        # The host that stayed: its stream runs on and still reaches it.
        sidecar.feed("b", "b2")
        assert _next_note(host_b) == _data("sb", "b2")
        assert sidecar.stream_running("b")

        # Ending a session twice is harmless.
        assert sidecar.post("shutdown", "host-a")["result"] is True
        sidecar.feed("b", "b3")
        assert _next_note(host_b) == _data("sb", "b3")
    finally:
        host_a.close()
        host_b.close()


def test_a_session_ends_when_its_last_sse_stream_closes(sidecar):
    # What a host that crashed or lost the network gets to say.
    first = sidecar.sse("host-a")
    second = sidecar.sse("host-a")
    host_b = sidecar.sse("host-b")
    try:
        sidecar.start_stream("a", "sa", "host-a")
        sidecar.start_stream("b", "sb", "host-b")
        sidecar.feed("a", "a1")
        assert _next_note(first) == _data("sa", "a1")
        assert _next_note(second) == _data("sa", "a1")

        # One of the session's two streams closes: the session lives on.
        first.close()
        assert sidecar.next_event() == ("sse-closed", "host-a")
        sidecar.feed("a", "a2")
        assert _next_note(second) == _data("sa", "a2")
        assert sidecar.stream_running("a")

        # The last one closes: the session ends, and its stream with it.
        second.close()
        assert sidecar.next_event() == ("sse-closed", "host-a")
        assert sidecar.next_event() == ("session-ended", "host-a")
        sidecar.feed("a", "a3")
        assert sidecar.stream_closed("a")

        # The other session is untouched.
        sidecar.feed("b", "b1")
        assert _next_note(host_b) == _data("sb", "b1")
        assert sidecar.stream_running("b")
    finally:
        first.close()
        second.close()
        host_b.close()


def test_the_plugins_hook_runs_when_the_server_stops_not_when_a_host_leaves():
    running = _Sidecar()
    try:
        assert running.post("shutdown", "host-a")["result"] is True
        assert running.post("shutdown")["result"] is True
        assert running.post("ping", "host-b")["result"] == "pong"
        assert running.plugin.shutdowns == 0
    finally:
        running.stop()
    assert running.plugin.shutdowns == 1


def test_a_shutdown_without_a_session_ends_nothing(sidecar):
    # The unnamed session is every host that sent no id: the sidecar cannot
    # know whose streams it would be ending.
    unnamed = sidecar.sse()
    try:
        sidecar.start_stream("a", "sa")
        sidecar.feed("a", "a1")
        assert _next_note(unnamed) == _data("sa", "a1")

        assert sidecar.post("shutdown")["result"] is True

        sidecar.feed("a", "a2")
        assert _next_note(unnamed) == _data("sa", "a2")
        assert sidecar.stream_running("a")
    finally:
        unnamed.close()


def test_requests_without_a_session_form_one_unnamed_session(sidecar):
    unnamed = sidecar.sse()
    named = sidecar.sse("host-b")
    try:
        sidecar.start_stream("a", "sa")
        sidecar.start_stream("b", "sb", "host-b")

        # A stream started without an id reaches the SSE stream opened
        # without one, and not the named host; and the other way round.
        sidecar.feed("a", "a1")
        assert _next_note(unnamed) == _data("sa", "a1")
        sidecar.feed("b", "b1")
        assert _next_note(named) == _data("sb", "b1")
        sidecar.feed("a", "a2")
        assert _next_note(unnamed) == _data("sa", "a2")
    finally:
        unnamed.close()
        named.close()
