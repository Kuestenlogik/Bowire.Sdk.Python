# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""A minimal Bowire sidecar plugin in Python.

Discovers one "Echo" service with two methods:
  - echo   (Unary)          → echoes the request payload back
  - ticker (ServerStreaming) → streams a few ticks

Three settings shape the answers, one of each kind the dialog renders as
free input or a choice: ``prefix`` (string), ``letterCase`` (select) and
``ticks`` (number). Change one in Bowire's Settings dialog and the next call
uses it — no restart.

Run it standalone for a smoke test:
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python echo_plugin.py

Or package echo_plugin.py + sidecar.json into a zip and install:
    bowire plugin install --file echo-sidecar.zip
    bowire --url echo://demo

The manifest names the interpreter (``"executable": "python"``) and passes
this file as its argument, so the script needs no shebang and no executable
bit — it is a module the interpreter runs, on every platform alike.
"""
from __future__ import annotations

from collections.abc import Iterator

from bowire_plugin import (
    BowirePlugin,
    InvokeResult,
    ManualCallKind,
    ManualEntryForm,
    MethodInfo,
    PluginSetting,
    ServiceInfo,
    SettingOption,
    Settings,
    run,
)


class EchoPlugin(BowirePlugin):
    id = "echo"
    name = "Echo (Python)"
    icon_svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16"><path d="M4 12h6l3-8 4 16 3-8h0"/></svg>'

    def settings(self) -> list[PluginSetting]:
        return [
            PluginSetting(
                key="prefix",
                label="Reply prefix",
                description="Put in front of every echoed payload.",
                type="string",
                default_value="echo",
            ),
            PluginSetting(
                key="letterCase",
                label="Letter case",
                type="select",
                default_value="as-is",
                options=[
                    SettingOption("as-is", "As sent"),
                    SettingOption("upper", "Upper case"),
                ],
            ),
            PluginSetting(
                key="ticks",
                label="Ticker length",
                description="How many ticks the ticker streams.",
                type="number",
                default_value=5,
            ),
        ]

    def manual_entry(self) -> ManualEntryForm:
        # How the two methods are called by hand, without discovery
        # (Kuestenlogik/Bowire#1003): the workbench renders a form from this.
        return ManualEntryForm(
            call_kinds=[
                ManualCallKind("echo", "Unary", label="Echo", service="Echo", method="echo",
                               body_example='{"text": "hello"}'),
                ManualCallKind("ticker", "ServerStreaming", label="Ticker", service="Echo", method="ticker"),
            ]
        )

    def discover(
        self, server_url: str, show_internal: bool, settings: Settings
    ) -> list[ServiceInfo]:
        return [
            ServiceInfo(
                name="Echo",
                package="echo",
                source="echo",
                origin_url=server_url,
                description="Demo Python sidecar plugin.",
                methods=[
                    MethodInfo("echo", full_name="Echo/echo", method_type="Unary"),
                    MethodInfo(
                        "ticker",
                        full_name="Echo/ticker",
                        method_type="ServerStreaming",
                        server_streaming=True,
                    ),
                ],
            )
        ]

    def invoke(
        self,
        server_url: str,
        service: str,
        method: str,
        json_messages: list[str],
        show_internal: bool,
        metadata: dict[str, str],
        settings: Settings,
    ) -> InvokeResult:
        payload = json_messages[0] if json_messages else ""
        reply = f"{settings.get_str('prefix')}: {payload}"
        if settings.get_str("letterCase") == "upper":
            reply = reply.upper()
        return InvokeResult(
            response=reply,
            status="OK",
            metadata={"service": service, "method": method},
        )

    def invoke_stream(
        self,
        server_url: str,
        service: str,
        method: str,
        json_messages: list[str],
        show_internal: bool,
        metadata: dict[str, str],
        settings: Settings,
    ) -> Iterator[str]:
        for i in range(1, max(settings.get_int("ticks"), 0) + 1):
            yield f'{{"tick":{i}}}'


if __name__ == "__main__":
    run(EchoPlugin())
