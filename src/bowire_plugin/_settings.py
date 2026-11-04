# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""The setting values the host sends with each call (Kuestenlogik/Bowire#693).

A plugin declares its settings once, in :meth:`BowirePlugin.settings`; the
host renders them in its Settings dialog and, on every ``discover`` /
``invoke`` / ``invokeStream``, puts the values somebody actually set in a
``settings`` object on the params. This module is the other half: what a
plugin reads those values through.

Three properties of the wire shape the API:

- **Per call, not per process.** The values travel with each request, so a
  changed setting takes effect on the next call without a restart. The
  runtime builds a fresh :class:`Settings` for every call.
- **Strings, whatever the declared type.** The dialog sends what a form
  control produced; the plugin parses what it declared. The typed accessors
  do that parse, with the same rules the host's own .NET accessors use.
- **Only keys that are set.** An absent key means "use the default you
  declared" — so the accessors fall back to the declared ``default_value``,
  and a plugin whose settings nobody touched sees an empty mapping.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, TypeVar

from ._models import PluginSetting

_T = TypeVar("_T")

# ASCII digits only: Python's int()/float() also accept other Unicode digits,
# underscores, "nan" and "inf", none of which a form control produces and none
# of which the host's invariant-culture parse accepts.
_INT = re.compile(r"[+-]?[0-9]+", re.ASCII)
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


class _Unset:
    """Marks "no explicit default given" — ``None`` is a legal default."""

    def __repr__(self) -> str:
        return "<declared default>"


_UNSET = _Unset()


def _parse_bool(text: str) -> bool | None:
    t = text.strip().lower()
    if t == "true":
        return True
    if t == "false":
        return False
    return None


def _parse_int(text: str) -> int | None:
    t = text.strip()
    return int(t) if _INT.fullmatch(t) else None


def _parse_float(text: str) -> float | None:
    t = text.strip()
    if not _FLOAT.fullmatch(t):
        return None
    value = float(t)
    return value if math.isfinite(value) else None


# The declared default is any JSON value the plugin wrote, so it is coerced
# rather than parsed: ``default_value=5`` for a number setting is an int, not
# the string "5". A bool is never taken as a number, although Python's bool
# is an int — ``True`` as a probe window is a mistake, not ``1``.

def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return _parse_bool(value) if isinstance(value, str) else None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    return _parse_int(value) if isinstance(value, str) else None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_float(value) if isinstance(value, str) else None


class Settings(Mapping[str, str]):
    """The values set for this plugin's declared settings, for one call.

    A read-only mapping of the raw strings the host sent — ``settings["key"]``,
    ``"key" in settings``, ``settings.get("key")`` — plus typed accessors
    that parse the string and fall back to the default the plugin declared::

        def invoke(self, server_url, service, method, json_messages,
                   show_internal, metadata, settings):
            window = settings.get_float("probeWindow")   # 5.0 until somebody sets it
            verbose = settings.get_bool("verbose")

    Each accessor resolves in this order:

    1. the value the host sent, when it parses as the requested type;
    2. ``default``, when the caller passed one;
    3. the ``default_value`` declared for that key in
       :meth:`BowirePlugin.settings`;
    4. otherwise :class:`KeyError` — the key has no value and nothing to fall
       back to, which is a bug in the plugin rather than in its input.

    A value that does not parse falls back exactly like an absent one, which
    is what the host's .NET accessors do: an operator's typo in a probe
    window degrades to the default instead of failing every discovery.
    """

    def __init__(
        self,
        values: Mapping[str, Any] | None = None,
        declared: Iterable[PluginSetting] = (),
    ) -> None:
        # Strings on the wire; a number or bool is tolerated the way the host
        # tolerates one in a hand-edited settings file. Anything else has no
        # text form and is dropped rather than guessed at.
        self._values: dict[str, str] = {}
        for key, value in (values or {}).items():
            text = _coerce_str(value)
            if isinstance(key, str) and text is not None:
                self._values[key] = text
        self._defaults: dict[str, Any] = {s.key: s.default_value for s in declared}

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Settings({self._values!r})"

    def get_str(self, key: str, default: str | _Unset = _UNSET) -> str:
        """The value as text. An empty string is a value someone set, not an
        absent one."""
        return self._resolve(key, _coerce_str, default, "a string")

    def get_bool(self, key: str, default: bool | _Unset = _UNSET) -> bool:
        """The value as a flag: ``true`` / ``false``, case-insensitive."""
        return self._resolve(key, _coerce_bool, default, "a bool")

    def get_int(self, key: str, default: int | _Unset = _UNSET) -> int:
        """The value as a whole number (ASCII digits, optional sign)."""
        return self._resolve(key, _coerce_int, default, "an int")

    def get_float(self, key: str, default: float | _Unset = _UNSET) -> float:
        """The value as a finite number, decimal point and exponent allowed."""
        return self._resolve(key, _coerce_float, default, "a float")

    def _resolve(
        self,
        key: str,
        convert: Callable[[Any], _T | None],
        default: _T | _Unset,
        kind: str,
    ) -> _T:
        raw = self._values.get(key)
        if raw is not None:
            parsed = convert(raw)
            if parsed is not None:
                return parsed
        if not isinstance(default, _Unset):
            return default
        if key in self._defaults:
            declared = convert(self._defaults[key])
            if declared is not None:
                return declared
        raise KeyError(
            f"setting '{key}' has no value that reads as {kind} and declares no default "
            f"that does; declare one in settings() or pass default="
        )
