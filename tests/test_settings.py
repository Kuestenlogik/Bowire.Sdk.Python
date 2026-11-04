# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Settings, both halves: what a plugin declares in the initialize reply, and
the per-call values the host sends back on discover / invoke / invokeStream
(Kuestenlogik/Bowire#693)."""
from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest

from bowire_plugin import (
    BowirePlugin,
    InvokeResult,
    PluginSetting,
    ServiceInfo,
    SettingOption,
    Settings,
)
from bowire_plugin._runtime import _StdioRuntime

_DECLARED = [
    PluginSetting("prefix", "Prefix", type="string", default_value="echo"),
    PluginSetting("verbose", "Verbose", type="bool", default_value=False),
    PluginSetting("ticks", "Ticks", type="number", default_value=5),
    PluginSetting("window", "Probe window", type="number", default_value=1.5),
    PluginSetting("mode", "Mode", type="select", default_value="fast",
                  options=[SettingOption("fast", "Fast"), SettingOption("thorough")]),
]


# --- the accessors --------------------------------------------------------


def test_a_set_value_is_returned_parsed():
    s = Settings({"prefix": "hi", "verbose": "true", "ticks": "7", "window": "2.25"}, _DECLARED)
    assert s.get_str("prefix") == "hi"
    assert s.get_bool("verbose") is True
    assert s.get_int("ticks") == 7
    assert s.get_float("window") == 2.25


def test_an_absent_key_returns_the_declared_default():
    s = Settings(None, _DECLARED)
    assert s.get_str("prefix") == "echo"
    assert s.get_bool("verbose") is False
    assert s.get_int("ticks") == 5
    assert s.get_float("window") == 1.5
    assert s.get_str("mode") == "fast"


def test_a_declared_number_default_reads_as_a_float_too():
    assert Settings(None, _DECLARED).get_float("ticks") == 5.0


def test_an_explicit_default_wins_over_the_declared_one():
    s = Settings(None, _DECLARED)
    assert s.get_int("ticks", 9) == 9
    assert s.get_str("prefix", "") == ""


def test_an_explicit_default_does_not_shadow_a_set_value():
    assert Settings({"ticks": "3"}, _DECLARED).get_int("ticks", 9) == 3


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("false", False), ("True", True), ("FALSE", False), (" true ", True),
])
def test_bool_parsing(raw, expected):
    assert Settings({"verbose": raw}, _DECLARED).get_bool("verbose") is expected


@pytest.mark.parametrize("raw,expected", [
    ("0", 0), ("42", 42), ("-3", -3), ("+8", 8), (" 12 ", 12),
])
def test_int_parsing(raw, expected):
    assert Settings({"ticks": raw}, _DECLARED).get_int("ticks") == expected


@pytest.mark.parametrize("raw,expected", [
    ("2", 2.0), ("0.5", 0.5), (".5", 0.5), ("-1.25", -1.25), ("1e3", 1000.0), ("3.", 3.0),
])
def test_float_parsing(raw, expected):
    assert Settings({"window": raw}, _DECLARED).get_float("window") == expected


# A value that does not read as the declared type falls back like an absent
# one — the host's own accessors do the same, so an operator's typo degrades
# to the default instead of failing every call.
@pytest.mark.parametrize("raw", ["yes", "1", "", "on"])
def test_an_unparseable_bool_falls_back_to_the_default(raw):
    assert Settings({"verbose": raw}, _DECLARED).get_bool("verbose") is False


@pytest.mark.parametrize("raw", ["7.5", "1_000", "seven", "", "٣", "0x10"])
def test_an_unparseable_int_falls_back_to_the_default(raw):
    assert Settings({"ticks": raw}, _DECLARED).get_int("ticks") == 5


@pytest.mark.parametrize("raw", ["nan", "inf", "1e999", "1,5", "", "1_0"])
def test_an_unparseable_float_falls_back_to_the_default(raw):
    assert Settings({"window": raw}, _DECLARED).get_float("window") == 1.5


def test_an_empty_string_is_a_set_string():
    assert Settings({"prefix": ""}, _DECLARED).get_str("prefix") == ""


def test_no_value_and_no_default_is_a_key_error():
    with pytest.raises(KeyError, match="undeclared"):
        Settings(None, _DECLARED).get_int("undeclared")


def test_a_declared_default_of_the_wrong_type_is_no_default():
    # True is not a tick count, even though Python's bool is an int.
    declared = [PluginSetting("ticks", "Ticks", type="number", default_value=True)]
    with pytest.raises(KeyError):
        Settings(None, declared).get_int("ticks")


def test_the_mapping_holds_only_what_the_host_sent():
    s = Settings({"prefix": "hi"}, _DECLARED)
    assert dict(s) == {"prefix": "hi"}
    assert "ticks" not in s
    assert s.get("ticks") is None
    assert len(Settings(None, _DECLARED)) == 0


def test_non_string_wire_values_are_tolerated_as_their_text():
    # The contract says strings; the host itself tolerates a number or bool in
    # a hand-edited settings file, so the SDK does not fall over on one.
    s = Settings({"ticks": 3, "verbose": True, "nested": {"x": 1}}, _DECLARED)
    assert s["ticks"] == "3"
    assert s.get_bool("verbose") is True
    assert "nested" not in s


# --- the declaration ------------------------------------------------------


def test_select_options_serialise_with_value_and_label():
    d = _DECLARED[4].to_dict()
    assert d["type"] == "select"
    assert d["options"] == [{"value": "fast", "label": "Fast"}, {"value": "thorough"}]


def test_a_setting_without_options_sends_no_options_field():
    assert "options" not in _DECLARED[0].to_dict()


def test_a_select_without_options_is_refused():
    with pytest.raises(ValueError, match="mode"):
        PluginSetting("mode", "Mode", type="select")


# --- through the runtime --------------------------------------------------


class _Recorder(BowirePlugin):
    """Records the settings each call was handed."""

    id = "rec"
    name = "Recorder"

    def __init__(self) -> None:
        self.seen: list[tuple[str, Settings]] = []

    def settings(self):
        return _DECLARED

    def discover(self, server_url, show_internal, settings):
        self.seen.append(("discover", settings))
        return [ServiceInfo("S")]

    def invoke(self, server_url, service, method, json_messages, show_internal, metadata,
               settings):
        self.seen.append(("invoke", settings))
        return InvokeResult(response=settings.get_str("prefix"))

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata,
                      settings) -> Iterator[str]:
        self.seen.append(("invokeStream", settings))
        for i in range(settings.get_int("ticks")):
            yield str(i)


def _drive(plugin: BowirePlugin, *requests: dict) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    _StdioRuntime(plugin, stdin, stdout).run()
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_initialize_declares_the_settings_with_select_options():
    out = _drive(_Recorder(), {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    declared = out[0]["result"]["settings"]
    assert [s["key"] for s in declared] == ["prefix", "verbose", "ticks", "window", "mode"]
    assert declared[2] == {"key": "ticks", "label": "Ticks", "description": None,
                           "type": "number", "defaultValue": 5}
    assert declared[4]["options"] == [{"value": "fast", "label": "Fast"}, {"value": "thorough"}]


def test_every_call_gets_the_settings_its_own_params_carried():
    plugin = _Recorder()
    out = _drive(
        plugin,
        {"jsonrpc": "2.0", "id": 1, "method": "discover",
         "params": {"serverUrl": "x", "settings": {"prefix": "one"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "invoke",
         "params": {"serverUrl": "x", "service": "S", "method": "S/m", "jsonMessages": [],
                    "settings": {"prefix": "two"}}},
        # A value changed between two calls of one process applies to the
        # second — the property the per-call form exists for.
        {"jsonrpc": "2.0", "id": 3, "method": "invoke",
         "params": {"serverUrl": "x", "service": "S", "method": "S/m", "jsonMessages": [],
                    "settings": {"prefix": "three"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "invokeStream",
         "params": {"streamId": "s", "serverUrl": "x", "service": "S", "method": "S/t",
                    "jsonMessages": [], "settings": {"ticks": "2"}}},
    )
    assert [(m, dict(s)) for m, s in plugin.seen] == [
        ("discover", {"prefix": "one"}),
        ("invoke", {"prefix": "two"}),
        ("invoke", {"prefix": "three"}),
        ("invokeStream", {"ticks": "2"}),
    ]
    replies = {m["id"]: m["result"] for m in out if "id" in m}
    assert replies[2]["response"] == "two"
    assert replies[3]["response"] == "three"
    data = [m["params"]["message"] for m in out if m.get("method") == "$/stream/data"]
    assert data == ["0", "1"]


def test_a_call_without_settings_reads_the_declared_defaults():
    # The common case: nobody touched the settings, so the host sends no
    # `settings` field at all.
    plugin = _Recorder()
    out = _drive(plugin, {"jsonrpc": "2.0", "id": 1, "method": "invoke",
                          "params": {"serverUrl": "x", "service": "S", "method": "S/m",
                                     "jsonMessages": []}})
    assert out[0]["result"]["response"] == "echo"
    assert len(plugin.seen[0][1]) == 0


def test_a_malformed_settings_field_reads_as_none_set():
    plugin = _Recorder()
    out = _drive(plugin, {"jsonrpc": "2.0", "id": 1, "method": "invoke",
                          "params": {"serverUrl": "x", "service": "S", "method": "S/m",
                                     "jsonMessages": [], "settings": ["prefix", "x"]}})
    assert out[0]["result"]["response"] == "echo"
