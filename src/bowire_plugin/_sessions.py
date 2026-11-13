# Copyright 2026 Küstenlogik
# SPDX-License-Identifier: Apache-2.0
"""Which session owns which running stream.

Over stdio the pipe is the host, and everything the sidecar runs belongs to
it. Over http several hosts share one sidecar, and each names itself with a
session id (the ``Bowire-Session`` request header). A stream belongs to the
session of the request that started it: its notifications go to that session
only, and it is stopped when that session ends. See "Sessions over http" in
the Bowire repo's ``docs/architecture/sidecar-plugins.md``.

The id is opaque: compared, never parsed. ``None`` is the unnamed session,
which holds every request that carried no id, and all of stdio.
"""
from __future__ import annotations

import threading

#: A session id as the host sent it, or ``None`` for the unnamed session.
SessionId = str | None


class _Stream:
    """One running ``invokeStream``: the flag that tells its pump to stop,
    and the thread the pump runs on."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None


class _SessionStreams:
    """The streams running right now, by owning session.

    Ending a session only raises the stop flag of its streams. A stream
    leaves the registry when its pump is done (:meth:`discard`), so a pump
    that is still writing its last frame can be waited for, and ending a
    session a second time finds nothing left to do.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[SessionId, set[_Stream]] = {}
        self._all_ended = False

    def open(self, session: SessionId) -> _Stream:
        """Register a new stream for ``session``. After :meth:`end_all` the
        stream comes back already stopped."""
        stream = _Stream()
        with self._lock:
            if self._all_ended:
                stream.stopped.set()
            self._by_session.setdefault(session, set()).add(stream)
        return stream

    def discard(self, session: SessionId, stream: _Stream) -> None:
        """Forget a stream whose pump is done."""
        with self._lock:
            streams = self._by_session.get(session)
            if streams is None:
                return
            streams.discard(stream)
            if not streams:
                del self._by_session[session]

    def end(self, session: SessionId) -> None:
        """Stop the streams of one session; every other session runs on."""
        with self._lock:
            for stream in self._by_session.get(session, ()):
                stream.stopped.set()

    def end_all(self) -> None:
        """Stop every stream, and every stream opened from now on: the
        sidecar itself is stopping."""
        with self._lock:
            self._all_ended = True
            for streams in self._by_session.values():
                for stream in streams:
                    stream.stopped.set()

    def threads(self) -> list[threading.Thread]:
        """The pump threads still running, for a caller that waits for them."""
        with self._lock:
            return [
                stream.thread
                for streams in self._by_session.values()
                for stream in streams
                if stream.thread is not None
            ]
