"""The hand-entry form a plugin declares (Kuestenlogik/Bowire#1003): it lands
in the initialize reply as ``manualEntry``, absent when the plugin declares
none, and a form the host would refuse is refused where it is declared."""
from __future__ import annotations

import pytest

from bowire_plugin import (
    BowirePlugin,
    ManualCallKind,
    ManualEntryForm,
    ManualField,
    SettingOption,
    manual_entry_problems,
)
from bowire_plugin._runtime import _Dispatcher


def _form() -> ManualEntryForm:
    return ManualEntryForm(
        url_placeholder="mybroker://localhost:9000",
        call_kinds=[
            ManualCallKind("produce", "Unary", label="Produce", service="{topic}", method="produce"),
            ManualCallKind("consume", "ServerStreaming", service="{topic}", method="consume"),
        ],
        fields=[
            ManualField("topic", label="Topic", placement="template", required=True),
            ManualField("priority", type="select", call_kinds=["produce"],
                        options=[SettingOption("high", "High"), SettingOption("low")]),
        ],
    )


class _Plugin(BowirePlugin):
    id = "mybroker"
    name = "My broker"

    def __init__(self, form: ManualEntryForm | None) -> None:
        self._form = form

    def manual_entry(self) -> ManualEntryForm | None:
        return self._form


def _initialize(plugin: BowirePlugin) -> dict:
    reply, _ = _Dispatcher(plugin, lambda _frame: None).handle("initialize", 1, {})
    assert reply is not None
    return reply["result"]


def test_the_declared_form_is_in_the_initialize_reply() -> None:
    result = _initialize(_Plugin(_form()))

    form = result["manualEntry"]
    assert form["urlPlaceholder"] == "mybroker://localhost:9000"
    assert [(k["id"], k["label"], k["methodType"]) for k in form["callKinds"]] == [
        ("produce", "Produce", "Unary"),
        ("consume", "consume", "ServerStreaming"),
    ]
    topic, priority = form["fields"]
    assert (topic["key"], topic["placement"], topic["required"]) == ("topic", "template", True)
    assert priority["callKinds"] == ["produce"]
    assert priority["options"] == [{"value": "high", "label": "High"}, {"value": "low"}]


def test_no_form_means_no_manual_entry_in_the_reply() -> None:
    assert "manualEntry" not in _initialize(_Plugin(None))


def test_a_form_the_host_would_refuse_is_refused_where_it_is_declared() -> None:
    with pytest.raises(ValueError, match="placeholder 'topic'"):
        ManualEntryForm(call_kinds=[ManualCallKind("produce", "Unary", service="{topic}")])


@pytest.mark.parametrize(
    ("form_kwargs", "expected"),
    [
        ({"call_kinds": []}, "it offers no call kind"),
        ({"call_kinds": [ManualCallKind("a", "Sometimes")]}, "call kind 'a' has method type 'Sometimes'"),
        ({"call_kinds": [ManualCallKind("a", "Unary"), ManualCallKind("a", "Unary")]},
         "call kind 'a' is declared twice"),
        ({"call_kinds": [ManualCallKind("a", "Unary")], "fields": [ManualField("v", type="select")]},
         "select field 'v' has no options"),
        ({"call_kinds": [ManualCallKind("a", "Unary")], "fields": [ManualField("w", call_kinds=["b"])]},
         "field 'w' names unknown call kind 'b'"),
    ],
)
def test_the_check_names_each_problem(form_kwargs: dict, expected: str) -> None:
    # Built without the constructor's check, to read what the check says.
    form = ManualEntryForm.__new__(ManualEntryForm)
    form.call_kinds = form_kwargs["call_kinds"]
    form.fields = form_kwargs.get("fields", [])
    form.url_placeholder = None
    assert expected in manual_entry_problems(form)
