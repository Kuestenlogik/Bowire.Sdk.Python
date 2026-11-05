"""How a call to the plugin is entered by hand (Kuestenlogik/Bowire#1003).

A plugin returns a :class:`ManualEntryForm` from
:meth:`~bowire_plugin.BowirePlugin.manual_entry`; the SDK puts it in the
``initialize`` reply as ``manualEntry``, and the workbench renders a Compose
form from it and offers exactly these method types in its freeform form.
The wire shape is the Bowire repo's docs/architecture/sidecar-plugins.md,
"A hand-entry form".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ._models import SettingOption

#: How a call kind runs: Unary is invoke, ServerStreaming is invokeStream,
#: ClientStreaming and Duplex open a channel.
METHOD_TYPES = ("Unary", "ServerStreaming", "ClientStreaming", "Duplex")

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


@dataclass
class ManualCallKind:
    """One kind of call the plugin offers, e.g. produce or consume.

    ``service`` and ``method`` are templates: ``{key}`` is the value of the
    field with that key.
    """

    id: str
    method_type: str
    label: str | None = None
    service: str = ""
    method: str = ""
    description: str | None = None
    body_example: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label or self.id,
            "methodType": self.method_type,
            "service": self.service,
            "method": self.method,
        }
        if self.description is not None:
            d["description"] = self.description
        if self.body_example is not None:
            d["bodyExample"] = self.body_example
        return d


@dataclass
class ManualField:
    """One field of the form.

    ``placement`` ``"template"`` feeds only the call kinds' templates;
    ``"metadata"`` (the default) goes into the call's metadata under ``key``.
    ``call_kinds`` limits the field to those kinds; empty means all.
    """

    key: str
    label: str | None = None
    placement: str = "metadata"
    description: str | None = None
    placeholder: str | None = None
    required: bool = False
    type: str = "string"
    options: list[SettingOption] = field(default_factory=list)
    default_value: str | None = None
    call_kinds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key": self.key,
            "label": self.label or self.key,
            "placement": self.placement,
            "required": self.required,
            "type": self.type,
        }
        for name, value in (
            ("description", self.description),
            ("placeholder", self.placeholder),
            ("defaultValue", self.default_value),
        ):
            if value is not None:
                d[name] = value
        if self.options:
            d["options"] = [o.to_dict() for o in self.options]
        if self.call_kinds:
            d["callKinds"] = list(self.call_kinds)
        return d


def manual_entry_problems(form: ManualEntryForm) -> list[str]:
    """What is wrong with a form, or nothing — the check the host runs
    before it serves one."""
    problems: list[str] = []
    if not form.call_kinds:
        problems.append("it offers no call kind")

    kind_ids: set[str] = set()
    for kind in form.call_kinds:
        if not kind.id:
            problems.append("a call kind has no id")
        elif kind.id in kind_ids:
            problems.append(f"call kind '{kind.id}' is declared twice")
        else:
            kind_ids.add(kind.id)
        if kind.method_type not in METHOD_TYPES:
            problems.append(f"call kind '{kind.id}' has method type '{kind.method_type}'")

    field_keys: set[str] = set()
    for f in form.fields:
        if not f.key:
            problems.append("a field has no key")
        elif f.key in field_keys:
            problems.append(f"field '{f.key}' is declared twice")
        else:
            field_keys.add(f.key)
        if f.type == "select" and not f.options:
            problems.append(f"select field '{f.key}' has no options")
        for kind_id in f.call_kinds:
            if kind_id not in kind_ids:
                problems.append(f"field '{f.key}' names unknown call kind '{kind_id}'")

    for kind in form.call_kinds:
        for template in (kind.service, kind.method):
            for key in _PLACEHOLDER.findall(template or ""):
                if key not in field_keys:
                    problems.append(
                        f"call kind '{kind.id}' uses the placeholder '{key}', which no field declares"
                    )
    return problems


@dataclass
class ManualEntryForm:
    """How a call to this plugin is entered by hand.

    A form the host would refuse — an unknown method type, a placeholder no
    field declares, a ``select`` without options — is refused here, when it
    is declared, rather than silently never appearing in the workbench.
    """

    call_kinds: list[ManualCallKind]
    fields: list[ManualField] = field(default_factory=list)
    url_placeholder: str | None = None

    def __post_init__(self) -> None:
        problems = manual_entry_problems(self)
        if problems:
            raise ValueError("manual entry form: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "callKinds": [k.to_dict() for k in self.call_kinds],
            "fields": [f.to_dict() for f in self.fields],
        }
        if self.url_placeholder is not None:
            d["urlPlaceholder"] = self.url_placeholder
        return d
