"""Hack Mode session lifecycle.

One session per WebSocket connection, held in memory for the lifetime of that
connection. Sessions are isolated: nothing is shared between connections, and
a disconnect removes the session entirely.

A session records who it is, when it started, the terminal geometry the
client reported, its own `Scenario`, and — since Phase 2A — its own
`SerialTransport` for real I/O with the attached ESP32. The scenario is
the per-session simulated target: creating it via a `default_factory` means
every session gets an independent instance, so no two sessions can observe or
mutate each other's scenario state. Command history, evaluation metrics, and
durable storage still belong to later phases.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config
from app.hardware import SerialTransport
from app.scenarios import Scenario, create_default_scenario


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HackSession:
    """State for a single connected Hack Mode terminal."""

    session_id: str
    created_at: datetime = field(default_factory=_utc_now)
    cols: int = config.DEFAULT_TERMINAL_COLS
    rows: int = config.DEFAULT_TERMINAL_ROWS
    #: The per-session simulated target. Isolation lives here: a fresh
    #: instance per session means one student's scenario is unreachable from
    #: another's. Command handlers reach it via `CommandContext.scenario`.
    scenario: Scenario = field(default_factory=create_default_scenario)
    #: This session's own link to the physical ESP32 (Phase 2A). A fresh
    #: transport per session, for the same isolation reason `scenario` is
    #: per-session: one student's serial stream is unreachable from another's.
    #:
    #: Created CLOSED and stays closed until a serial command opens it, so
    #: merely entering Hack Mode never claims the port or resets the board.
    #: `app/websocket.py` closes it unconditionally on disconnect, so no
    #: reader thread or open port outlives the connection that made it.
    serial: SerialTransport = field(default_factory=SerialTransport)

    def resize(self, cols: int, rows: int) -> None:
        """Record the client's terminal geometry.

        Geometry is stored only. Phase 2A has no PTY to resize — a later
        phase will forward these dimensions to whatever backs the terminal.
        """
        self.cols = cols
        self.rows = rows


class SessionManager:
    """In-memory registry of live sessions.

    Async-locked because a single event loop can interleave connect and
    disconnect handling across many concurrent WebSocket connections.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, HackSession] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> HackSession:
        """Create and register a session with a fresh unique id."""
        session = HackSession(session_id=str(uuid.uuid4()))
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> HackSession | None:
        """Return the session with this id, or None if it is not live."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove(self, session_id: str) -> HackSession | None:
        """Unregister a session. Safe to call for an already-removed id."""
        async with self._lock:
            return self._sessions.pop(session_id, None)

    def discard(self, session_id: str) -> HackSession | None:
        """Unregister without awaiting. The teardown path.

        WebSocket cleanup runs on a task the server has already cancelled,
        where every `await` — including acquiring `_lock` — raises
        immediately (see `SerialTransport.release` for the same problem and
        the same reasoning). A registry entry that could not be removed
        would keep a finished session, and its scenario, alive for the life
        of the process.

        Dropping the lock is safe here rather than merely expedient: a dict
        `pop` contains no await point, so under asyncio it cannot interleave
        with `create`/`get`/`remove`. The lock exists to make multi-step
        async sequences atomic, and this is a single step.
        """
        return self._sessions.pop(session_id, None)

    async def count(self) -> int:
        """Number of live sessions (used by /health and by tests)."""
        async with self._lock:
            return len(self._sessions)


#: Process-wide session registry used by the WebSocket endpoint.
session_manager = SessionManager()
