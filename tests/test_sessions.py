# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""The dispatcher's session rules without a transport: who owns a stream,
what ending a session stops, and when the plugin's ``shutdown`` hook runs.
``tests/test_http.py`` holds the same rules over real sockets."""
from __future__ import annotations

from collections.abc import Iterator

from bowire_plugin import BowirePlugin
from bowire_plugin._runtime import _Dispatcher


class _Ticker(BowirePlugin):
    id = "ticker"

    def __init__(self) -> None:
        self.shutdowns = 0
        self.streams_started = 0
        self.streams_closed = 0

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata,
                      settings) -> Iterator[str]:
        self.streams_started += 1
        try:
            yield "1"
            yield "2"
        finally:
            self.streams_closed += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


def _dispatcher(plugin: BowirePlugin, *, host_owned: bool) -> tuple[_Dispatcher, list]:
    emitted: list[tuple[str, str | None, str | None]] = []

    def emit(envelope, session):
        emitted.append((envelope["method"], envelope["params"].get("message"), session))

    return _Dispatcher(plugin, emit, host_owned=host_owned), emitted


def _run_stream(dispatcher: _Dispatcher, session: str | None) -> None:
    """Start a stream in ``session`` and wait until its pump is done."""
    _, start = dispatcher.handle("invokeStream", 1, {"streamId": "s"}, session)
    assert start is not None
    start()
    dispatcher.join_streams()


def test_a_streams_notifications_carry_the_session_that_started_it():
    dispatcher, emitted = _dispatcher(_Ticker(), host_owned=False)
    _run_stream(dispatcher, "host-a")
    assert emitted == [
        ("$/stream/data", "1", "host-a"),
        ("$/stream/data", "2", "host-a"),
        ("$/stream/end", None, "host-a"),
    ]


def test_a_stream_started_after_its_session_ended_runs_as_a_new_one():
    # The id is all there is to a session: nothing remembers an ended one,
    # so a host is free to go on using its id.
    plugin = _Ticker()
    dispatcher, emitted = _dispatcher(plugin, host_owned=False)
    dispatcher.end_session("host-a")
    dispatcher.end_session("host-a")  # twice is harmless
    _run_stream(dispatcher, "host-a")
    assert [message for _, message, _ in emitted] == ["1", "2", None]
    assert plugin.streams_closed == 1


def test_a_hosts_shutdown_does_not_run_the_plugins_hook_when_the_sidecar_stays():
    plugin = _Ticker()
    dispatcher, _ = _dispatcher(plugin, host_owned=False)
    for session in ("host-a", "host-a", None):
        reply, _ = dispatcher.handle("shutdown", 1, {}, session)
        assert reply is not None and reply["result"] is True
    assert plugin.shutdowns == 0
    assert not dispatcher.stopped


def test_stopping_runs_the_plugins_hook_once():
    plugin = _Ticker()
    dispatcher, _ = _dispatcher(plugin, host_owned=False)
    dispatcher.stop()
    dispatcher.stop()
    assert plugin.shutdowns == 1
    assert dispatcher.stopped


def test_a_stream_asked_for_while_stopping_is_ended_without_calling_the_plugin():
    plugin = _Ticker()
    dispatcher, emitted = _dispatcher(plugin, host_owned=False)
    dispatcher.stop()
    _run_stream(dispatcher, "host-a")
    assert emitted == [("$/stream/end", None, "host-a")]
    assert plugin.streams_started == 0
