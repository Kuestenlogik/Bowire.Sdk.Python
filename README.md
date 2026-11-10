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

    def discover(self, server_url, show_internal, settings):
        return [ServiceInfo("Things", methods=[
            MethodInfo("get", full_name="Things/get", method_type="Unary"),
        ])]

    def invoke(self, server_url, service, method, json_messages, show_internal, metadata,
               settings):
        payload = json_messages[0] if json_messages else ""
        return InvokeResult(response=f"got {payload}", status="OK")

    def invoke_stream(self, server_url, service, method, json_messages, show_internal, metadata,
                      settings):
        for i in range(5):
            yield f'{{"tick":{i}}}'   # each yield → a $/stream/data frame

if __name__ == "__main__":
    run(MyPlugin())
```

`run()` blocks, handling the contract (`initialize` / `discover` / `invoke` / `invokeStream` / `ping` / `shutdown`) until the host disconnects.

## Settings

A plugin declares settings once; Bowire renders them in its Settings dialog,
under the plugin's own section (Bowire 2.8 or later). The values come back on
every call as the `settings` argument.

**Declare** them by overriding `settings()`:

```python
from bowire_plugin import PluginSetting, SettingOption

class MyPlugin(BowirePlugin):
    def settings(self):
        return [
            PluginSetting("probeWindow", "Probe window", type="number", default_value=5,
                          description="Seconds to listen before answering discovery."),
            PluginSetting("verbose", "Verbose", type="bool", default_value=False),
            PluginSetting("mode", "Mode", type="select", default_value="fast", options=[
                SettingOption("fast", "Fast"),
                SettingOption("thorough", "Thorough"),
            ]),
        ]
```

`type` is `"bool"` (the default), `"string"`, `"number"` or `"select"`. A
`select` needs `options`; declaring one without them raises `ValueError`.

**Read** them from the `settings` argument every call receives:

```python
    def discover(self, server_url, show_internal, settings):
        window = settings.get_float("probeWindow")   # 5.0 until somebody sets it
        if settings.get_bool("verbose"):
            ...
        thorough = settings.get_str("mode") == "thorough"
```

What the accessors guarantee:

- **Fresh per call.** The host sends the current values with each request, so
  a changed setting applies from the next call — no restart. Read the
  argument; don't copy it into the plugin at startup.
- **Typed, with the declared default.** `get_str` / `get_bool` / `get_int` /
  `get_float` parse the value the host sent (always a string on the wire) and
  return the `default_value` you declared when the key is absent — the host
  only sends keys somebody actually set. Pass `default=` to override it for
  one read.
- **A value that does not parse falls back** to the default, as the host's own
  .NET accessors do: `get_bool` accepts `true` / `false` in any case,
  `get_int` ASCII digits with an optional sign, `get_float` a finite decimal
  with an optional exponent. A key with neither a usable value nor a default
  raises `KeyError`.
- `settings` is also a read-only mapping of exactly what the host sent:
  `dict(settings)`, `"verbose" in settings`, `settings.get("mode")`.

## Hand-entry form

A user can call your plugin without discovery, from the workbench's freeform
form or Compose, and only your plugin knows what such a call needs. Declare it
in `manual_entry()`. The SDK sends it in the `initialize` reply, and the
workbench renders a Compose form from it and offers exactly these method types
in the freeform form (Bowire 2.11 or later).

```python
from bowire_plugin import ManualCallKind, ManualEntryForm, ManualField

def manual_entry(self) -> ManualEntryForm:
    return ManualEntryForm(
        url_placeholder="mybroker://localhost:9000",
        call_kinds=[
            ManualCallKind("produce", "Unary", label="Produce", service="{topic}", method="produce"),
            ManualCallKind("consume", "ServerStreaming", label="Consume", service="{topic}", method="consume"),
        ],
        fields=[
            ManualField("topic", label="Topic", placement="template", required=True),
            ManualField("key", label="Key", call_kinds=["produce"]),
        ],
    )
```

- A **call kind** names how it runs: `Unary` is `invoke`, `ServerStreaming`
  is `invoke_stream`, and `ClientStreaming` or `Duplex` opens a channel.
  `service` and `method` are templates, in which `{key}` is the value of the
  field with that key.
- A **field** with `placement="template"` only feeds the templates. The
  default, `"metadata"`, goes into the call's metadata under its `key`, which
  is where your `invoke` reads it.
- A form the host would refuse raises `ValueError` where it is built, naming
  the problem. Examples are an unknown method type, a placeholder no field
  declares, and a `select` without options. `manual_entry_problems(form)`
  runs the same check.

## Ship it

Put the script next to a `sidecar.json` manifest:

```json
{
  "packageId": "Acme.Bowire.Sidecar.MyProto",
  "protocol": { "id": "myproto", "name": "MyProtocol" },
  "executable": "python",
  "args": ["my_plugin.py"],
  "version": "1.0.0"
}
```

The manifest names the interpreter and passes the script as its argument, so
the script needs no shebang and no executable bit. The host first looks for
`executable` in the plugin directory, then — for a bare name — on `PATH`
(Bowire 2.8 or later). The SDK has to be installed into the interpreter that
name resolves to.

Why `python` and not `python3`: no single name exists on every machine, and
`python` is the one that does wherever this SDK is installed in practice.

| Where the plugin runs | `python` | `python3` |
|---|---|---|
| Windows, python.org installer or `actions/setup-python` | yes | no — resolves to the Microsoft Store alias, which exits without running anything |
| Any virtual environment (`venv` on PATH), Linux, macOS or Windows | yes | Linux / macOS only — a Windows venv has no `python3.exe` |
| Linux / macOS system interpreter, no venv | only where the distribution ships it (`python-is-python3`) | yes |

The last row is also the one where `pip install bowire-plugin` is refused by
default on current distributions (PEP 668), so a plugin there already needs a
venv. If you ship a plugin only for Linux or macOS system interpreters, name
`python3` instead.

Zip the folder and install into Bowire:

```bash
bowire plugin install --file my-proto-sidecar.zip
bowire --url myproto://your-server
```

`bowire plugin list` tags it `[sidecar: myproto]`.

## Transports: stdio (default) or HTTP/SSE

The same `BowirePlugin` runs over either wire — like MCP's stdio +
streamable-HTTP. Default is stdio (the examples above). For a
long-running / remote service, serve it over HTTP instead:

```python
from bowire_plugin import run_http
run_http(MyPlugin(), host="127.0.0.1", port=8770)
```

`run_http` exposes one path: `POST` for JSON-RPC requests (the response
body is the reply), `GET` as the SSE stream that carries server
notifications (`$/stream/data`, …). Point the manifest at it with
`transport: "http"`:

```json
{
  "packageId": "Acme.Bowire.Sidecar.MyProto",
  "protocol": { "id": "myproto", "name": "MyProtocol" },
  "transport": "http",
  "url": "http://127.0.0.1:8770/"
}
```

| | stdio | http |
|---|---|---|
| Lifecycle | Bowire spawns/kills the process | you run the service; Bowire is just a client |
| Best for | local plugin shipped as a zip | hosted / remote / shared by many hosts |
| Deps | none | none (stdlib `http.server`) |

An http sidecar outlives the hosts that use it. Every connected host gets
every notification on its own SSE stream. A host's `shutdown` is answered,
and the server keeps serving. `run_http` stops on SIGTERM (what a container
manager sends) or Ctrl-C, ends the SSE streams hosts hold open, and returns
0. Pass `port=0` and `on_listening=` to let the OS pick a free port and
learn which one it picked:

```python
run_http(MyPlugin(), port=0, on_listening=lambda port: print(f"listening on {port}"))
```

The echo example runs this way with `python echo_plugin.py --http
127.0.0.1:0`. `scripts/e2e.py --transport http` drives it through a real
host, and CI runs both transports.

## API surface

| Override | When it's called | Return |
|----------|------------------|--------|
| `discover(server_url, show_internal, settings)` | discovery | `list[ServiceInfo]` (empty = decline the URL) |
| `invoke(server_url, service, method, json_messages, show_internal, metadata, settings)` | unary call | `InvokeResult` |
| `invoke_stream(...)` | server-streaming call | iterator of JSON strings (each → one frame) |
| `settings()` | once, for the `initialize` reply | `list[PluginSetting]` |
| `manual_entry()` | for the `initialize` reply | `ManualEntryForm`, or `None` for no form |
| `shutdown()` | host exit | `None` (cleanup hook) |

Class attributes: `id` (required, matches the manifest), `name`, `icon_svg`.

Models — `ServiceInfo`, `MethodInfo`, `MessageInfo`, `FieldInfo`, `InvokeResult`, `PluginSetting`, `SettingOption`, `ManualEntryForm`, `ManualCallKind`, `ManualField` — are dataclasses that serialize to the camelCase shapes the host expects. `Settings` is the per-call argument described under [Settings](#settings).

## Example

[`examples/echo`](examples/echo) is a runnable plugin (unary echo + a ticker stream) that declares and reads three settings: a reply `prefix` (string), a `letterCase` (select) and the ticker length `ticks` (number). Smoke-test it as a plain process:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python examples/echo/echo_plugin.py
```

Or install it into a real Bowire host, discover through it, and round-trip its settings through the workbench — what CI runs on Linux and Windows:

```bash
python scripts/e2e.py --bowire "$(command -v bowire)" --work "$(mktemp -d)"
```

## Releasing

Pushing a tag `vX.Y.Z` that matches `version` in `pyproject.toml` publishes
`bowire-plugin` to PyPI (`.github/workflows/release.yml`). The workflow tests
that commit, builds the sdist and the wheel, and uploads them through PyPI
Trusted Publishing from the `pypi` environment, so no API token is stored. A
version already on PyPI is skipped. To publish an existing tag again, run the
workflow by hand with that tag.

## Not in scope

Sidecars implement **protocols**, not host extensions (auth providers, UI widgets, mock emitters stay .NET). Full duplex channels (`open_channel`) are on the roadmap; today expose long-lived streams via `invoke_stream`.

## License

Apache-2.0.
