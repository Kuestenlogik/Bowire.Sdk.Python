# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Data models mirroring Bowire's discovery / invocation shapes.

The host (a .NET process) deserializes these as camelCase JSON — the
same `BowireServiceInfo` / `BowireMethodInfo` / `InvokeResult` shapes the
in-tree .NET plugins emit. The ``to_dict`` methods produce exactly that
camelCase wire form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldInfo:
    """One field of a request/response message (mirrors BowireFieldInfo)."""

    name: str
    number: int = 0
    type: str = "string"
    label: str = "optional"
    is_map: bool = False
    is_repeated: bool = False
    required: bool = False
    description: str | None = None
    source: str | None = None  # REST-style: path/query/header/body

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "number": self.number,
            "type": self.type,
            "label": self.label,
            "isMap": self.is_map,
            "isRepeated": self.is_repeated,
            "messageType": None,
            "enumValues": None,
            "required": self.required,
        }
        if self.description is not None:
            d["description"] = self.description
        if self.source is not None:
            d["source"] = self.source
        return d


@dataclass
class MessageInfo:
    """A request or response message type (mirrors BowireMessageInfo)."""

    name: str
    full_name: str = ""
    fields: list[FieldInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fullName": self.full_name or self.name,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class MethodInfo:
    """A single method within a service (mirrors BowireMethodInfo)."""

    name: str
    full_name: str = ""
    method_type: str = "Unary"  # Unary | ServerStreaming | ClientStreaming | Duplex
    input_type: MessageInfo | None = None
    output_type: MessageInfo | None = None
    client_streaming: bool = False
    server_streaming: bool = False
    summary: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "fullName": self.full_name or self.name,
            "clientStreaming": self.client_streaming,
            "serverStreaming": self.server_streaming,
            "inputType": (self.input_type or MessageInfo(self.name + "Input")).to_dict(),
            "outputType": (self.output_type or MessageInfo(self.name + "Output")).to_dict(),
            "methodType": self.method_type,
        }
        if self.summary is not None:
            d["summary"] = self.summary
        if self.description is not None:
            d["description"] = self.description
        return d


@dataclass
class ServiceInfo:
    """A discovered service + its methods (mirrors BowireServiceInfo)."""

    name: str
    package: str = ""
    methods: list[MethodInfo] = field(default_factory=list)
    source: str = "sidecar"
    origin_url: str | None = None
    description: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "package": self.package,
            "methods": [m.to_dict() for m in self.methods],
            "source": self.source,
        }
        if self.origin_url is not None:
            d["originUrl"] = self.origin_url
        if self.description is not None:
            d["description"] = self.description
        if self.version is not None:
            d["version"] = self.version
        return d


@dataclass
class InvokeResult:
    """Result of a unary invocation (mirrors InvokeResult)."""

    response: str | None = None
    duration_ms: int = 0
    status: str = "OK"
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "durationMs": self.duration_ms,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class PluginSetting:
    """A setting the plugin contributes to the Bowire Settings dialog."""

    key: str
    label: str
    description: str | None = None
    type: str = "bool"
    default_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "type": self.type,
            "defaultValue": self.default_value,
        }
