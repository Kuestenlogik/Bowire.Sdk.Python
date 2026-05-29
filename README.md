# bowire-plugin (Python SDK)

[![PyPI](https://img.shields.io/pypi/v/bowire-plugin)](https://pypi.org/project/bowire-plugin/)
[![Python](https://img.shields.io/pypi/pyversions/bowire-plugin)](https://pypi.org/project/bowire-plugin/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Write a [Bowire](https://github.com/Kuestenlogik/Bowire) protocol plugin **in Python**.

Bowire's first-party plugins are .NET assemblies, but any executable can be a plugin by speaking JSON-RPC 2.0 over its stdin/stdout — a *sidecar*. This SDK hides the wire: subclass `BowirePlugin`, implement the methods you need, call `run()`. Perfect when the best client for your protocol lives in the Python ecosystem (`paho-mqtt`, the SciPy/ML stack, vendor SDKs, …).

See the full wire spec: [`docs/architecture/sidecar-plugins.md`](https://github.com/Kuestenlogik/Bowire/blob/main/docs/architecture/sidecar-plugins.md).

## Install

```bash
pip install bowire-plugin
```

## Write a plugin

```python
from bowire_plugin import BowirePlugin, ServiceInfo, MethodInfo, InvokeResult, run

class MyPlugin(BowirePlugin):
    id = "myproto"          # must match sidecar.json's protocol.id
    name = "MyProtocol"
    icon_svg = "<svg/>"     # optional

    def discover(self, server_url, show_internal):
        return [ServiceInfo("Things", methods=[
            MethodInfo("get", full_name="Things/get", method_type="Unary"),
        ])]

    def invoke(self, server_url, service, method, json_messages, show_internal, metadata):
        payload = json_messages[0] if json_messages else ""
        return InvokeResult(response=f"got {payload}", status="OK")

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata):
        for i in range(5):
            yield f'{{"tick":{i}}}'   # each yield → a $/stream/data frame

if __name__ == "__main__":
    run(MyPlugin())
```

`run()` blocks, handling the contract (`initialize` / `discover` / `invoke` / `invokeStream` / `ping` / `shutdown`) until the host disconnects.

## Ship it

Put the script next to a `sidecar.json` manifest:

```json
{
  "packageId": "Acme.Bowire.Sidecar.MyProto",
  "protocol": { "id": "myproto", "name": "MyProtocol" },
  "executable": "python3",
  "args": ["my_plugin.py"],
  "version": "1.0.0"
}
```

Zip the folder and install into Bowire:

```bash
bowire plugin install --file my-proto-sidecar.zip
bowire --url myproto://your-server
```

`bowire plugin list` tags it `[sidecar: myproto]`.

## API surface

| Override | When it's called | Return |
|----------|------------------|--------|
| `discover(server_url, show_internal)` | discovery | `list[ServiceInfo]` (empty = decline the URL) |
| `invoke(server_url, service, method, json_messages, show_internal, metadata)` | unary call | `InvokeResult` |
| `invoke_stream(...)` | server-streaming call | iterator of JSON strings (each → one frame) |
| `settings()` | startup | `list[PluginSetting]` |
| `shutdown()` | host exit | `None` (cleanup hook) |

Class attributes: `id` (required, matches the manifest), `name`, `icon_svg`.

Models — `ServiceInfo`, `MethodInfo`, `MessageInfo`, `FieldInfo`, `InvokeResult`, `PluginSetting` — are dataclasses that serialize to the camelCase shapes the host expects.

## Example

[`examples/echo`](examples/echo) is a runnable plugin (unary echo + a 5-tick stream). Smoke-test it as a plain process:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python examples/echo/echo_plugin.py
```

## Not in scope

Sidecars implement **protocols**, not host extensions (auth providers, UI widgets, mock emitters stay .NET). Full duplex channels (`open_channel`) are on the roadmap; today expose long-lived streams via `invoke_stream`.

## License

Apache-2.0.
