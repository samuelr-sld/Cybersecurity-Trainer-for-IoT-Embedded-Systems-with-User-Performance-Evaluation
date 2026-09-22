"""The per-session Build Mode attempt recorder — Phase 2E.3.

    compile_workspace / _finish_flash / (future) validate -> RECORDER -> SQLite

Mirrors `app/events/recorder.py::HackEventRecorder` at Build Mode's own
granularity (see `app/build/records.py`): one row per completed compile,
flash, or validation attempt, rather than a command/event pair, because
Build Mode has no command router — `BuildService` already IS the one place
that knows a compile or flash just finished and how it ended.

ONE RECORDER PER `BuildSession`, OWNED BY IT — the same isolation story
`HackEventRecorder` tells: a recorder only ever knows one session id, so two
Build Mode sessions can never share or leak attempt history.

TWO COPIES, ONE AUTHORITATIVE FOR THE LIVE SESSION, same discipline as Hack
Mode's recorder: attempts are appended in memory first, then written through
to the shared store; a storage failure degrades to "this session's evidence
was not persisted", never to a broken compile/flash/session.

NOT WIRED TO A DEDICATED SQLITE FILE OF ITS OWN. `app/events/store.py`'s
`SqliteEventStore` already owns the one on-disk database this backend
writes activity evidence to (Hack Mode's tables); this recorder reuses that
same store and connection for two more tables (`build_sessions`,
`build_attempts`) rather than opening a second file — "reuse the existing
event/attempt recording... do not duplicate" from the Phase 2E.3 brief,
applied at the storage layer.

DELIBERATELY A SEPARATE MODULE FROM `app/build/__init__.py`'S IMPORTS. This
module imports `app.events` (for the shared clock and store); `app/events/
store.py` in turn imports `app.build.records` (the plain row shapes, not
this module) to speak the Build Mode schema. Keeping this recorder OUT of
`app/build/__init__.py`'s own import list is what keeps that cycle acyclic
in practice: merely importing the `app.build` package must never require
`app.events` to already exist, so callers reach this module by its own
path — `from app.build.recorder import BuildEventRecorder` — never via
`from app.build import BuildEventRecorder`.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.build.records import BuildAttemptRecord, BuildAttemptType, BuildSessionRecord
from app.events.clock import utc_now
from app.events.store import SqliteEventStore, StoreError, get_default_store

logger = logging.getLogger(__name__)


class BuildEventRecorder:
    """Records one Build Mode session's compile/flash/validation attempts.

    `store` is injected so a test can record into an in-memory database
    without touching the classroom one — the same convention
    `HackEventRecorder` follows. Left as None it resolves the process-wide
    store lazily, on first write.
    """

    def __init__(
        self,
        session_id: str,
        started_at: datetime | None = None,
        store: SqliteEventStore | None = None,
        panel_id: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._started_at = started_at if started_at is not None else utc_now()
        self._panel_id = panel_id
        self._store = store
        self._sequence = 0
        self._attempts: list[BuildAttemptRecord] = []
        self._started = False
        self._finished = False
        self._store_failures = 0

    # -- identity and live reads ---------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def started_at(self) -> datetime:
        """When this Build Mode session began — TTR's anchor."""
        return self._started_at

    @property
    def panel_id(self) -> str | None:
        return self._panel_id

    @property
    def attempts(self) -> tuple[BuildAttemptRecord, ...]:
        """Attempts recorded so far, in order, for this session only."""
        return tuple(self._attempts)

    @property
    def store_failures(self) -> int:
        return self._store_failures

    # -- storage plumbing -----------------------------------------------------

    def _resolve_store(self) -> SqliteEventStore | None:
        if self._store is None:
            try:
                self._store = get_default_store()
            except StoreError:
                self._note_failure("open the event store")
                return None
        return self._store

    def _note_failure(self, what: str) -> None:
        self._store_failures += 1
        if self._store_failures == 1:
            logger.warning(
                "build attempt persistence unavailable (could not %s) for session=%s; "
                "this session's log is live-only",
                what,
                self._session_id,
                exc_info=True,
            )

    def _persist(self, what: str, action) -> None:
        store = self._resolve_store()
        if store is None:
            return
        try:
            action(store)
        except StoreError:
            self._note_failure(what)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Open this session in the log. Idempotent.

        Called when the Build Mode WebSocket connects (via
        `BuildSessionManager.create`), so `started_at` is TTR's Build Mode
        session start — never the moment of the first compile.
        """
        if self._started:
            return
        self._started = True
        record = BuildSessionRecord(
            session_id=self._session_id,
            started_at=self._started_at,
            panel_id=self._panel_id,
        )
        self._persist("open the build session", lambda store: store.open_build_session(record))

    def finish(self) -> None:
        """Stamp this session as ended. Idempotent, never raises."""
        if self._finished:
            return
        self._finished = True
        self.start()
        ended_at = utc_now()
        self._persist(
            "close the build session",
            lambda store: store.close_build_session(self._session_id, ended_at),
        )

    # -- recording ----------------------------------------------------------

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def record_attempt(
        self,
        attempt_type: BuildAttemptType,
        success: bool,
        detail: str = "",
        occurred_at: datetime | None = None,
    ) -> BuildAttemptRecord:
        """Record one completed compile, flash, or validation attempt.

        `success` and `detail` are supplied by the caller (`BuildService`),
        which already knows the real outcome — `CompileOutcome.success` /
        `FlashOutcome.success`, or a future real validation result. This
        method makes no judgement of its own about what counts as success.
        """
        self.start()
        record = BuildAttemptRecord(
            session_id=self._session_id,
            sequence=self._next_sequence(),
            attempt_type=attempt_type,
            success=success,
            occurred_at=occurred_at if occurred_at is not None else utc_now(),
            detail=detail,
        )
        self._attempts.append(record)
        self._persist(
            "record a build attempt", lambda store: store.append_build_attempt(record)
        )
        return record
