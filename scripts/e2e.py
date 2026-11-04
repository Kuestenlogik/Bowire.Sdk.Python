# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Install the echo example into a real Bowire host and ask it to answer.

Every contract defect this SDK carried — a discover reply the host reads as
"no services", parameter names nothing sends, a manifest the installer
rejects, an ``executable`` that only resolved on Unix — was invisible to a
suite that only ever talked to itself. This script does what an operator
does: zip the example, install it as a sidecar plugin, and discover through
the host. ``bowire discover`` exits 0 only when a plugin actually returned a
service, so its exit code is the assertion.

Then the settings round trip (Kuestenlogik/Bowire#693), which the CLI cannot
show — plugin setting values are stored per workspace, and only the
workbench serves one. The script starts the workbench, calls the example
once with its declared defaults, sets its three settings through the same
endpoint the Settings dialog uses, and calls again: the second answer has to
carry the new values, from the same sidecar process, without a restart.

It is Python rather than shell so the same steps run on Linux and Windows:
the manifest names its interpreter, and a manifest that only works on one
platform is the failure this exists to catch.

    python scripts/e2e.py --bowire <path-to-bowire> --work <empty-temp-dir>

The interpreter the host spawns is whatever ``python`` the caller's ``PATH``
resolves — the SDK has to be installed there, the way a plugin author
installs it.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLE = _REPO / "examples" / "echo"
_PLUGIN_FILES = ("sidecar.json", "echo_plugin.py")
_SERVER_URL = "echo@http://localhost:1"
_BANNER = re.compile(r"Bowire is running at:\s+(\S+)")

# Loopback only; a proxy from the runner's environment has no business here.
_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _package(work: Path) -> Path:
    # The zip's root has to carry sidecar.json — that is what marks the
    # archive as a sidecar plugin.
    archive = work / "echo-python.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _PLUGIN_FILES:
            zf.write(_EXAMPLE / name, arcname=name)
    print(f"packaged {archive}: {', '.join(_PLUGIN_FILES)}", flush=True)
    return archive


def _bowire(bowire: str, plugin_dir: Path, *args: str) -> str:
    cmd = [bowire, "--plugin-dir", str(plugin_dir), *args]
    print("$ " + " ".join(cmd), flush=True)
    # check=False: the output is printed before the exit code is judged, so a
    # failing step still shows what the host said.
    done = subprocess.run(cmd, check=False, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    if done.returncode != 0:
        raise SystemExit(f"bowire {args[0]} exited {done.returncode}")
    return done.stdout


def _serve(bowire: str, plugin_dir: Path) -> tuple[subprocess.Popen[str], str]:
    """Start the workbench and return it with the URL it bound.

    The host prints its banner only once Kestrel reports the socket bound,
    so reading stdout up to that line is the readiness handshake: no port is
    guessed (``--port 0`` lets the OS pick) and nothing is retried.
    """
    cmd = [bowire, "--plugin-dir", str(plugin_dir), "--port", "0", "--no-browser"]
    print("$ " + " ".join(cmd), flush=True)
    # cwd outside the checkout: the workbench takes it as its content root.
    proc = subprocess.Popen(cmd, cwd=plugin_dir.parent, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        match = _BANNER.search(line)
        if match:
            # Keep draining, or a chatty host blocks on a full pipe.
            threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()
            return proc, match.group(1).rstrip("/")
    raise SystemExit(f"the workbench exited ({proc.wait()}) before it was listening")


def _drain(stream: IO[str]) -> None:
    for line in stream:
        sys.stdout.write(line)


def _request(method: str, url: str, body: Any = None) -> bytes:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    print(f"> {method} {url}", flush=True)
    with _HTTP.open(req, timeout=60) as resp:
        payload: bytes = resp.read()
    return payload


def _sse_data(url: str) -> Iterator[dict[str, Any]]:
    """Yield the ``data:`` frames of an SSE stream until its ``done`` event."""
    print(f"> GET {url}", flush=True)
    with _HTTP.open(url, timeout=60) as resp:
        event = "message"
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                if event == "done":
                    return
                if event == "error":
                    raise SystemExit(f"stream failed: {line[5:].strip()}")
                yield json.loads(line[5:])
            elif not line:
                event = "message"
    raise SystemExit("stream ended without its done event")


def _settings_round_trip(bowire: str, plugin_dir: Path, storage: Path) -> None:
    storage.mkdir(parents=True, exist_ok=True)
    proc, base = _serve(bowire, plugin_dir)
    try:
        # The workspace scope every per-workspace endpoint reads; an explicit
        # storage root keeps the values out of the runner's ~/.bowire.
        scope = {"workspaceId": "e2e", "storageRoot": str(storage)}
        query = urllib.parse.urlencode({"serverUrl": _SERVER_URL, **scope})

        services = json.loads(_request("GET", f"{base}/api/services?{query}"))
        if not any(s.get("name") == "Echo" for s in services):
            raise SystemExit(f"workbench discovery found no Echo service: {services}")

        def echo() -> str:
            body = {"protocol": "echo", "service": "Echo", "method": "Echo/echo",
                    "messages": ["ping"]}
            reply = json.loads(_request("POST", f"{base}/api/invoke?{query}", body))
            print(f"< {reply.get('response')!r}", flush=True)
            return str(reply.get("response"))

        def ticks() -> int:
            stream = urllib.parse.urlencode({"protocol": "echo", "service": "Echo",
                                             "method": "Echo/ticker", "messages": '["{}"]',
                                             "serverUrl": _SERVER_URL, **scope})
            count = sum(1 for _ in _sse_data(f"{base}/api/invoke/stream?{stream}"))
            print(f"< {count} ticks", flush=True)
            return count

        # Nobody has set anything yet: the declared defaults answer.
        if echo() != "echo: ping":
            raise SystemExit("before any setting was set, invoke did not use the defaults")
        if ticks() != 5:
            raise SystemExit("before any setting was set, the ticker did not stream 5 ticks")

        for key, value in (("prefix", "from-e2e"), ("letterCase", "upper"), ("ticks", "2")):
            _request("PUT", f"{base}/api/plugins/settings/echo/{key}?"
                     + urllib.parse.urlencode(scope), {"value": value})

        # Same sidecar process, new values: they travel with each call.
        if echo() != "FROM-E2E: PING":
            raise SystemExit("invoke did not see the settings set for it")
        if ticks() != 2:
            raise SystemExit("invokeStream did not see the settings set for it")
    finally:
        proc.terminate()
        proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bowire", required=True, help="the bowire executable to drive")
    parser.add_argument("--work", required=True, type=Path,
                        help="an empty directory for the zip and the plugin tree")
    opts = parser.parse_args()

    work: Path = opts.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    plugin_dir = work / "plugins"

    archive = _package(work)
    _bowire(opts.bowire, plugin_dir, "plugin", "install", "--file", str(archive))
    _bowire(opts.bowire, plugin_dir, "plugin", "list", "--verbose")

    out = _bowire(opts.bowire, plugin_dir, "discover", "--url", "echo@http://localhost:1")
    if "Echo" not in out:
        raise SystemExit("discover succeeded but listed no Echo service")

    _settings_round_trip(opts.bowire, plugin_dir, work / "workspace")

    print("e2e: ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
