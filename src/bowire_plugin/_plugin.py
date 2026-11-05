# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""The base class a Python sidecar plugin subclasses."""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from ._manual_entry import ManualEntryForm
from ._models import InvokeResult, PluginSetting, ServiceInfo
from ._settings import Settings


class BowirePlugin:
    """Subclass this and implement the methods you need, then hand an
    instance to :func:`bowire_plugin.run`. The runtime drives the
    JSON-RPC-over-stdio contract; you only write protocol logic.

    Set :attr:`id`, :attr:`name`, and (optionally) :attr:`icon_svg` as
    class attributes. The ``id`` must match the ``protocol.id`` in the
    plugin's ``sidecar.json`` manifest.

    Every call receives ``settings``: the values set for what
    :meth:`settings` declared, read fresh for that call (see
    :class:`~bowire_plugin.Settings`).
    """

    #: Short protocol id (e.g. ``"zenoh"``). Must match sidecar.json.
    id: str = ""
    #: Display name shown in the workbench tab (e.g. ``"Zenoh"``).
    name: str = ""
    #: Inline SVG for the protocol tab. Optional — the host falls back
    #: to a generic plug icon when empty.
    icon_svg: str = ""

    def settings(self) -> list[PluginSetting]:
        """Settings this plugin contributes to the Settings dialog.

        Read once per process, for the ``initialize`` reply. The values come back
        as the ``settings`` argument of every call, and the defaults
        declared here are what its accessors return for a key nobody set.
        """
        return []

    def manual_entry(self) -> ManualEntryForm | None:
        """How a call to this plugin is entered by hand (Kuestenlogik/Bowire#1003).

        Sent in the ``initialize`` reply as ``manualEntry``; the workbench
        renders a Compose form from it. Default: none.
        """
        return None

    def discover(
        self, server_url: str, show_internal: bool, settings: Settings
    ) -> Iterable[ServiceInfo]:
        """Enumerate services + methods for ``server_url``.

        Return an empty list to decline the URL (the host then tries
        other plugins). Default: nothing discovered.
        """
        return []

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
        """Perform a unary call and return the result. Default: an error
        result noting the plugin didn't implement invoke."""
        return InvokeResult(status="invoke not implemented by sidecar")

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
        """Server-streaming call: yield each message (a JSON string) as
        it arrives. The runtime forwards every yielded value to the host
        as a ``$/stream/data`` notification and sends ``$/stream/end``
        when the generator finishes. Default: an empty stream."""
        return iter(())

    def shutdown(self) -> None:
        """Optional cleanup hook, called when the host asks the sidecar
        to exit. Default: no-op."""
        return
