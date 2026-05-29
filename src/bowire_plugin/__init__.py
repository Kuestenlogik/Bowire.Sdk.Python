# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""bowire-plugin — write a Bowire protocol plugin in Python.

A *sidecar* plugin is any executable that speaks JSON-RPC 2.0 over its
stdin/stdout to the Bowire host. This package hides the wire: subclass
:class:`BowirePlugin`, implement the protocol methods you need, and call
:func:`run`.

    from bowire_plugin import BowirePlugin, ServiceInfo, MethodInfo, InvokeResult, run

    class MyPlugin(BowirePlugin):
        id = "myproto"
        name = "MyProtocol"

        def discover(self, server_url, show_internal):
            return [ServiceInfo("Things", methods=[MethodInfo("get")])]

        def invoke(self, server_url, service, method, json_messages, show_internal, metadata):
            return InvokeResult(response="hello", status="OK")

    if __name__ == "__main__":
        run(MyPlugin())

Ship the script + a ``sidecar.json`` manifest in a zip and install it
with ``bowire plugin install --file my-plugin.zip``.
"""
from ._models import (
    FieldInfo,
    InvokeResult,
    MessageInfo,
    MethodInfo,
    PluginSetting,
    ServiceInfo,
)
from ._http import run_http
from ._plugin import BowirePlugin
from ._runtime import run

__version__ = "0.2.0"

__all__ = [
    "BowirePlugin",
    "ServiceInfo",
    "MethodInfo",
    "MessageInfo",
    "FieldInfo",
    "InvokeResult",
    "PluginSetting",
    "run",
    "run_http",
    "__version__",
]
