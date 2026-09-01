"""Hack Mode session lifecycle.

One session per WebSocket connection, held in memory for the lifetime of that
connection. Sessions are isolated: nothing is shared between connections, and
a disconnect removes the session entirely.

Deliberately minimal for Phase 2A — a session records who it is, when it
started, and the terminal geometry the client reported. Scenario state,
command history, evaluation metrics, and durable storage belong to later
phases; the manager is the seam they will attach to.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HackSession:
    """State for a single connected Hack Mode terminal."""

    session_id: str
    created_at: datetime = field(default_factory=_utc_now)
    cols: int = config.DEFAULT_TERMINAL_COLS
    rows: int = config.DEFAULT_TERMINAL_ROWS

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

    async def count(self) -> int:
        """Number of live sessions (used by /health and by tests)."""
        async with self._lock:
            return len(self._sessions)


#: Process-wide session registry used by the WebSocket endpoint.
session_manager = SessionManager()
