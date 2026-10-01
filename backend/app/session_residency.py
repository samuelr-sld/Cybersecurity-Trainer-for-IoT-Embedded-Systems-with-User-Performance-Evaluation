"""Who is serving a session right now, and how long an orphan may wait.

A Hack or Build session used to live and die with its WebSocket. A browser
reload is not the end of a student's work, so the socket and the session are
now separate lifetimes: the socket *attaches* to a session, and losing the
socket *detaches* it. A detached session keeps every piece of backend state
(scenario, workspace, recorder, compiled artifact) and waits a bounded grace
period for a client to resume it; if none does, the manager's own end-of-
session cleanup runs exactly as a disconnect used to run it.

This module is only the bookkeeping for that — an owner token per session and
one timer per detached session. It knows nothing about Hack or Build, so both
managers share one implementation and neither grows a second lifecycle.

Everything here is synchronous on purpose. The WebSocket teardown that calls
`detach` runs on an already-cancelled task, where any `await` raises
immediately (see `app/websocket.py`); `loop.call_later` needs none.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app import config

logger = logging.getLogger(__name__)


class Residency:
    def __init__(self, grace_seconds: float | None = None) -> None:
        self._grace = config.SESSION_RESUME_GRACE_SECONDS if grace_seconds is None else grace_seconds
        self._owners: dict[str, object] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def claim(self, session_id: str) -> object:
        """Make the caller the session's one serving connection.

        Cancels any pending expiry. Returns the caller's token; a previous
        holder's token stops being current, which is how a stale connection
        (an old tab, a socket not yet noticed dead) learns it must not tear
        the session down when it finally closes.
        """
        self._cancel_timer(session_id)
        token = object()
        self._owners[session_id] = token
        return token

    def is_current(self, session_id: str, token: object) -> bool:
        return self._owners.get(session_id) is token

    def is_detached(self, session_id: str) -> bool:
        return session_id in self._timers

    def detach(self, session_id: str, token: object, on_expire: Callable[[], None]) -> bool:
        """The serving connection is gone: keep the session, start the clock.

        Returns False (and does nothing) when `token` is no longer current —
        someone else has resumed the session or it was ended.
        """
        if not self.is_current(session_id, token):
            return False
        del self._owners[session_id]
        self._cancel_timer(session_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop: nothing can resume it either
            on_expire()
            return True
        self._timers[session_id] = loop.call_later(self._grace, self._expire, session_id, on_expire)
        return True

    def forget(self, session_id: str) -> None:
        """The session is over (explicitly or by expiry): drop all bookkeeping."""
        self._owners.pop(session_id, None)
        self._cancel_timer(session_id)

    def _expire(self, session_id: str, on_expire: Callable[[], None]) -> None:
        self._timers.pop(session_id, None)
        self._owners.pop(session_id, None)
        try:
            on_expire()
        except Exception:  # a cleanup bug must not kill the loop's timer callback
            logger.warning("session expiry cleanup failed: %s", session_id, exc_info=True)

    def _cancel_timer(self, session_id: str) -> None:
        timer = self._timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()
