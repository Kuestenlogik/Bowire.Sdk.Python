# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Drive the runtime with scripted stdin lines and assert the NDJSON
replies — no real Bowire host needed."""
from __future__ import annotations

import io
import json
from collections.abc import Iterator

from bowire_plugin import BowirePlugin, InvokeResult, MethodInfo, ServiceInfo
from bowire_plugin._runtime import _Runtime


class _Fake(BowirePlugin):
    id = "fake"
    name = "Fake"
    icon_svg = "<svg/>"

    def discover(self, server_url, show_internal):
        return [ServiceInfo("Echo", package="fake", origin_url=server_url,
                            methods=[MethodInfo("echo", full_name="Echo/echo")])]

    def invoke(self, server_url, service, method, json_messages, show_internal, metadata):
        return InvokeResult(response="echo: " + (json_messages[0] if json_messages else ""),
                            status="OK", metadata={"service": service})

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata) -> Iterator[str]:
        for i in range(1, 4):
            yield f'{{"tick":{i}}}'


def _drive(*requests: dict) -> list[dict]:
    """Feed request envelopes (NDJSON) through the runtime, return every
    reply/notification it writes back."""
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    _Runtime(_Fake(), stdin, stdout).run()
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_initialize_reports_metadata():
    out = _drive({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    result = out[0]["result"]
    assert result["id"] == "fake"
    assert result["name"] == "Fake"
    assert result["iconSvg"] == "<svg/>"


def test_ping_returns_pong():
    out = _drive({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
    assert out[0]["result"] == "pong"


def test_discover_serialises_service_tree():
    out = _drive({"jsonrpc": "2.0", "id": 3, "method": "discover",
                  "params": {"serverUrl": "fake://x", "showInternalServices": False}})
    services = out[0]["result"]
    assert len(services) == 1
    assert services[0]["name"] == "Echo"
    assert services[0]["originUrl"] == "fake://x"
    assert services[0]["methods"][0]["fullName"] == "Echo/echo"
    assert services[0]["methods"][0]["methodType"] == "Unary"


def test_invoke_round_trips():
    out = _drive({"jsonrpc": "2.0", "id": 4, "method": "invoke",
                  "params": {"serverUrl": "fake://x", "service": "Echo", "method": "Echo/echo",
                             "jsonMessages": ["hello"], "showInternalServices": False, "metadata": {}}})
    result = out[0]["result"]
    assert result["response"] == "echo: hello"
    assert result["status"] == "OK"
    assert result["metadata"]["service"] == "Echo"


def test_invoke_stream_acks_then_emits_data_and_end():
    out = _drive({"jsonrpc": "2.0", "id": 5, "method": "invokeStream",
                  "params": {"streamId": "abc", "serverUrl": "fake://x", "service": "Echo",
                             "method": "Echo/ticker", "jsonMessages": [],
                             "showInternalServices": False, "metadata": {}}})
    # First line is the ack reply carrying the host-minted streamId.
    assert out[0]["id"] == 5
    assert out[0]["result"]["streamId"] == "abc"
    data = [m for m in out if m.get("method") == "$/stream/data"]
    ends = [m for m in out if m.get("method") == "$/stream/end"]
    assert len(data) == 3
    assert data[0]["params"]["streamId"] == "abc"
    assert data[0]["params"]["message"] == '{"tick":1}'
    assert len(ends) == 1
    assert ends[0]["params"]["error"] is None


def test_unknown_method_returns_error():
    out = _drive({"jsonrpc": "2.0", "id": 6, "method": "nope", "params": {}})
    assert out[0]["error"]["code"] == -32601


def test_shutdown_acks_and_stops():
    out = _drive(
        {"jsonrpc": "2.0", "id": 7, "method": "shutdown", "params": {}},
        {"jsonrpc": "2.0", "id": 8, "method": "ping", "params": {}},  # must NOT be processed
    )
    assert out[0]["id"] == 7
    assert out[0]["result"] is True
    # Only the shutdown reply — the loop stopped before ping.
    assert all(m.get("id") != 8 for m in out)


def test_malformed_json_line_is_skipped():
    stdin = io.StringIO('not json\n{"jsonrpc":"2.0","id":9,"method":"ping","params":{}}\n')
    stdout = io.StringIO()
    _Runtime(_Fake(), stdin, stdout).run()
    out = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert out[0]["result"] == "pong"
