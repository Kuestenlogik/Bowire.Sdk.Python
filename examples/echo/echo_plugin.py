#!/usr/bin/env python3
# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""A minimal Bowire sidecar plugin in Python.

Discovers one "Echo" service with two methods:
  - echo   (Unary)          → echoes the request payload back
  - ticker (ServerStreaming) → streams 5 ticks

Run it standalone for a smoke test:
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python echo_plugin.py

Or package echo_plugin.py + sidecar.json into a zip and install:
    bowire plugin install --file echo-sidecar.zip
    bowire --url echo://demo
"""
from __future__ import annotations

from collections.abc import Iterator

from bowire_plugin import BowirePlugin, InvokeResult, MethodInfo, ServiceInfo, run


class EchoPlugin(BowirePlugin):
    id = "echo"
    name = "Echo (Python)"
    icon_svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16"><path d="M4 12h6l3-8 4 16 3-8h0"/></svg>'

    def discover(self, server_url: str, show_internal: bool) -> list[ServiceInfo]:
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
    ) -> InvokeResult:
        payload = json_messages[0] if json_messages else ""
        return InvokeResult(
            response=f"echo: {payload}",
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
    ) -> Iterator[str]:
        for i in range(1, 6):
            yield f'{{"tick":{i}}}'


if __name__ == "__main__":
    run(EchoPlugin())
