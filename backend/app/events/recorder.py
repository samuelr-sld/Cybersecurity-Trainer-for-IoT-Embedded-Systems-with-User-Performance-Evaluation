"""The per-session event recorder — where a fact becomes a timestamped row.

    Student action -> Frontend -> FastAPI -> Command Router -> RECORDER -> SQLite

One recorder per `HackSession`, created with it and owned by it (see
app/sessions.py). That ownership is the whole isolation story: a recorder
only ever knows one session id, holds only that session's rows, and has no
way to reach another's. There is no global event list anywhere in this
package — `SqliteEventStore` is shared storage, but every row in it carries
the session that produced it and every read is scoped by session id.

THE BACKEND DECIDES WHAT HAPPENED. Nothing here inspects terminal text. The
inputs are the router's own facts — the parsed command name, the exit code,
and the `ScenarioEvent`s the scenario engine chose to emit — so an event
exists exactly when a real backend state transition occurred. A student who
types a plausible-looking `mosquitto_pub` at the wrong topic gets
`spoof_attempted` and `spoof_rejected` from the engine, and this recorder
writes those; it has no path by which that could become `spoof_succeeded`.

TWO COPIES, ONE AUTHORITATIVE FOR THE LIVE SESSION. Records are built and
appended in memory first, then written through to the store. The in-memory
list is what the live WebSocket renders from, so a storage failure costs the
durable copy and nothing else — it can never drop an `event` frame, break a
command, or end a session. Store failures are logged once per session and
then counted, because a failing disk should not also flood the log.

TIMESTAMPS COME FROM `app/events/clock.py`, always, and never from a client.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Sequence, TYPE_CHECKING

from app.events.clock import utc_now
from app.events.records import (
    HackCommandRecord,
    HackEventRecord,
    HackSessionRecord,
    clip_argv,
)
from app.events.store import SqliteEventStore, StoreError, get_default_store

if TYPE_CHECKING:  # pragma: no cover
    from app.scenarios.events import ScenarioEvent

logger = logging.getLogger(__name__)


class HackEventRecorder:
    """Records one Hack Mode session's commands and domain events.

    `store` is injected so a test — or a future replay harness — can record
    into an in-memory database without touching the classroom one. Left as
    None it resolves the process-wide store lazily, on first write, which is
    what keeps merely constructing a `HackSession` free of I/O.
    """

    def __init__(
        self,
        session_id: str,
        scenario_id: str = "scenario",
        started_at: datetime | None = None,
        store: SqliteEventStore | None = None,
    ) -> None:
        self._session_id = session_id
        self._scenario_id = scenario_id
        self._started_at = started_at if started_at is not None else utc_now()
        self._store = store
        self._sequence = 0
        self._events: list[HackEventRecord] = []
        self._commands: list[HackCommandRecord] = []
        self._started = False
        self._finished = False
        self._store_failures = 0

    # -- identity and live reads -------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def started_at(self) -> datetime:
        """When this session began — Phase 2E's TTE/TTR anchor."""
        return self._started_at

    @property
    def events(self) -> tuple[HackEventRecord, ...]:
        """Domain events recorded so far, in order, for this session only."""
        return tuple(self._events)

    @property
    def commands(self) -> tuple[HackCommandRecord, ...]:
        """Commands recorded so far, in order, for this session only."""
        return tuple(self._commands)

    @property
    def timeline(self) -> tuple[HackCommandRecord | HackEventRecord, ...]:
        """Commands and events merged into the order they happened in."""
        merged: list[HackCommandRecord | HackEventRecord] = [
            *self._commands,
            *self._events,
        ]
        merged.sort(key=lambda record: record.sequence)
        return tuple(merged)

    @property
    def store_failures(self) -> int:
        """How many writes could not be persisted. 0 on a healthy backend."""
        return self._store_failures

    # -- storage plumbing ---------------------------------------------------

    def _resolve_store(self) -> SqliteEventStore | None:
        if self._store is None:
            try:
                self._store = get_default_store()
            except StoreError:
                self._note_failure("open the event store")
                return None
        return self._store

    def _note_failure(self, what: str) -> None:
        """Count a persistence failure, and log only the first per session."""
        self._store_failures += 1
        if self._store_failures == 1:
            logger.warning(
                "hack event persistence unavailable (could not %s) for session=%s; "
                "this session's log is live-only",
                what,
                self._session_id,
                exc_info=True,
            )

    def _persist(self, what: str, action) -> None:
        """Run one store write, absorbing failure.

        A recorded fact that could not be written must not become an
        exception on the command dispatch path — see this module's docstring.
        """
        store = self._resolve_store()
        if store is None:
            return
        try:
            action(store)
        except StoreError:
            self._note_failure(what)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Open this session in the log. Idempotent.

        Called when the WebSocket connects, so `started_at` is the moment the
        student actually entered Hack Mode rather than the moment they first
        typed something — which is the interval TTE is defined over.
        """
        if self._started:
            return
        self._started = True
        record = HackSessionRecord(
            session_id=self._session_id,
            scenario_id=self._scenario_id,
            started_at=self._started_at,
        )
        self._persist("open the session", lambda store: store.open_session(record))

    def finish(self) -> None:
        """Stamp this session as ended. Idempotent, synchronous, never raises.

        Called from the WebSocket teardown, which runs on an already-cancelled
        task where any `await` would abandon the rest of the cleanup — see the
        `finally` block in `app/websocket.py` and `SessionManager.discard` for
        the same constraint. This is a plain SQLite UPDATE with no await point,
        so it completes even there.
        """
        if self._finished:
            return
        self._finished = True
        self.start()
        ended_at = utc_now()
        self._persist(
            "close the session",
            lambda store: store.close_session(self._session_id, ended_at),
        )

    # -- recording ----------------------------------------------------------

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def record_command(
        self,
        name: str | None,
        argv: Sequence[str],
        exit_code: int,
        handled: bool,
        occurred_at: datetime | None = None,
    ) -> HackCommandRecord:
        """Record one submitted command line.

        `occurred_at` lets the router pass the moment dispatch *began* rather
        than the moment it returned, so a long-running handler does not push
        the student's action later than it happened. It is still a
        server-generated stamp from `app/events/clock.py`; no caller may
        supply a time that came from a client.
        """
        self.start()
        record = HackCommandRecord(
            session_id=self._session_id,
            sequence=self._next_sequence(),
            name=name,
            argv=clip_argv(tuple(argv)),
            exit_code=exit_code,
            handled=handled,
            occurred_at=occurred_at if occurred_at is not None else utc_now(),
        )
        self._commands.append(record)
        self._persist("record a command", lambda store: store.append_command(record))
        return record

    def record_scenario_events(
        self,
        events: Iterable["ScenarioEvent"],
        command: str | None = None,
        exit_code: int | None = None,
    ) -> tuple[HackEventRecord, ...]:
        """Record the domain events one command caused, in the engine's order.

        The engine decides *whether* a transition happened; this only stamps
        and stores what it emitted. Each event keeps the scenario's own type
        string verbatim (`ScenarioEventType` values — `firmware_extracted`,
        `spoof_rejected`, `attack_completed`), so the recorded vocabulary and
        the scenario vocabulary cannot drift apart.

        Every event gets its own sequence number and its own timestamp, which
        is what lets Phase 2E measure the gap between, say, `spoof_succeeded`
        and `attack_completed` even when one command produced both.
        """
        recorded: list[HackEventRecord] = []
        for event in events:
            self.start()
            record = HackEventRecord.create(
                session_id=self._session_id,
                sequence=self._next_sequence(),
                event_type=event.type.value,
                message=event.message,
                occurred_at=utc_now(),
                data=event.data,
                command=command,
                exit_code=exit_code,
            )
            self._events.append(record)
            self._persist("record an event", lambda store, r=record: store.append_event(r))
            recorded.append(record)
        return tuple(recorded)
