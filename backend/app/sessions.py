"""Hack Mode session lifecycle.

One session per WebSocket connection, held in memory for the lifetime of that
connection. Sessions are isolated: nothing is shared between connections, and
a disconnect removes the session entirely.

A session records who it is, when it started, the terminal geometry the
client reported, its own `Scenario`, its own `SerialTransport` for real I/O
with the attached ESP32 (Phase 2A), and — since Phase 2B — its own
`HackEventRecorder`. The scenario is the per-session simulated target:
every session gets an independent instance, so no two sessions can observe
or mutate each other's scenario state. The recorder follows the same rule
for the same reason, and is what finally makes a session's activity
durable. Evaluation metrics still belong to Phase 2E.

WHICH SCENARIO A SESSION RUNS IS DECIDED ELSEWHERE (Phase 2D.4). A caller
may inject an already-constructed `Scenario`; the connection lifecycle in
`app/websocket.py` does exactly that, passing whatever the attached panel's
package declared (see `app/scenario_selection.py`). Nothing in this module
identifies a panel, reads a package, or knows that panels exist: a session
knows its scenario, not why it is that one. Omitting the argument keeps the
long-standing default, so a session built without one — in a test, or in
any pre-2D.4 caller — behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config
from app.events import HackEventRecorder
from app.hardware import FirmwareArtifact, SerialTransport
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
    #:
    #: Injectable since Phase 2D.4 — pass the instance the attached panel's
    #: package selected. The `default_factory` remains the fallback for the
    #: no-panel development flow and for every existing caller.
    scenario: Scenario = field(default_factory=create_default_scenario)
    #: The Phase 2E.2 EAC seam (see `HackSessionRecord`). Since the
    #: Evaluation phase `/ws/hack` fills it with the registered participant
    #: the client named on connect (`app/participants.py`); it stays None
    #: when none was named or the id is not registered — never invented.
    participant_id: str | None = None
    #: This session's own link to the physical ESP32 (Phase 2A). A fresh
    #: transport per session, for the same isolation reason `scenario` is
    #: per-session: one student's serial stream is unreachable from another's.
    #:
    #: Created CLOSED and stays closed until a serial command opens it, so
    #: merely entering Hack Mode never claims the port or resets the board.
    #: `app/websocket.py` closes it unconditionally on disconnect, so no
    #: reader thread or open port outlives the connection that made it.
    serial: SerialTransport = field(default_factory=SerialTransport)
    #: This session's own event recorder (Phase 2B). Built in `__post_init__`
    #: rather than by a `default_factory`, because unlike the scenario and the
    #: transport it is not independent of the session: it needs this
    #: session's id, this session's scenario id, and this session's
    #: `created_at` as the timeline anchor Phase 2E measures TTE from.
    #:
    #: Constructing it performs no I/O and opens no database — the store is
    #: resolved lazily on the first write (see `app/events/recorder.py`), so
    #: a `HackSession` built in a test is as cheap as it was before.
    recorder: HackEventRecorder = field(init=False)
    #: Real bytes captured by a successful hardware-backed `esptool.py
    #: read_flash` (Phase 2H.1) — see `app/hardware/flash_reader.py`. None
    #: until (and unless) that command actually reads a real, physically
    #: attached board for THIS session; `strings`/`grep` fall back to the
    #: scenario's simulated string table whenever this is None, which is
    #: every session today with no hardware attached. Deliberately held
    #: here rather than on `scenario`: a `Scenario` must stay a pure logical
    #: simulation (see `app/scenarios/base.py`), and real hardware bytes are
    #: exactly the kind of fact it must never hold or reason about.
    firmware_artifact: FirmwareArtifact | None = None

    def __post_init__(self) -> None:
        self.recorder = HackEventRecorder(
            session_id=self.session_id,
            scenario_id=self.scenario.scenario_id,
            started_at=self.created_at,
            participant_id=self.participant_id,
        )

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

    async def create(
        self, scenario: Scenario | None = None, participant_id: str | None = None
    ) -> HackSession:
        """Create and register a session with a fresh unique id.

        The event log is opened here, at connect, rather than when the first
        command arrives: `started_at` is the interval Phase 2E's TTE is
        measured over, so it must mean "entered Hack Mode", not "got as far
        as typing something". A session that ends without a single command
        is still a session that happened, and the log says so.

        `scenario` is dependency injection, and injection is the whole point
        (Phase 2D.4): the caller that owns the connection lifecycle decides
        which experiment this session runs — by then the attached panel has
        already been identified and its package's declared scenario id
        resolved, in `app/scenario_selection.py` — and hands the constructed
        object in. Neither this manager nor `HackSession` looks at a MAC, a
        panel id, a package or `panel.json` to make that decision, and
        neither has an opinion about which scenario is "right".

        Omitting it keeps the pre-2D.4 behaviour exactly: the session falls
        back to `HackSession`'s own `create_default_scenario` factory, so
        every existing caller and test is unaffected.

        `participant_id` is the Evaluation phase's owner of this session —
        already resolved to a registered participant (or None) by the
        connection lifecycle via `app/participants.py`. It is stored, never
        looked up, here.
        """
        session = HackSession(
            session_id=str(uuid.uuid4()),
            scenario=scenario if scenario is not None else create_default_scenario(),
            participant_id=participant_id,
        )
        session.recorder.start()
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

    def is_live(self, session_id: str) -> bool:
        """Whether this process is serving the session now (Evaluation read).

        A single dict membership test with no await point — safe without the
        lock for the same reason `discard` is.
        """
        return session_id in self._sessions

    async def count(self) -> int:
        """Number of live sessions (used by /health and by tests)."""
        async with self._lock:
            return len(self._sessions)


#: Process-wide session registry used by the WebSocket endpoint.
session_manager = SessionManager()
