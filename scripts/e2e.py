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
import subprocess
import sys
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLE = _REPO / "examples" / "echo"
_PLUGIN_FILES = ("sidecar.json", "echo_plugin.py")


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

    print("e2e: ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
