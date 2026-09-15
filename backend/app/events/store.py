"""Durable storage for Hack Mode event records — SQLite, stdlib only.

    Recorder -> EventStore -> SQLite -> (Phase 2E) Metric Computation

WHY SQLITE, AND WHY NOTHING ELSE. The trainer deploys to a Raspberry Pi
serving a classroom, and the thing that must survive is a small, append-only
log of what students did. `sqlite3` ships with Python, needs no server, no
driver, and no migration tool, and gives durable ordered rows out of one
file. An ORM would add a dependency and a mapping layer to store three flat
row shapes that never change during a session — see `app/events/records.py`.

APPEND-ONLY BY USE. There is no update or delete path here beyond stamping a
session's `ended_at`. An event log that can be rewritten is not evidence, and
Phase 2E grades from these rows.

RECORDING MUST NEVER BREAK A TERMINAL. Storage is the *secondary* copy of an
event; `HackEventRecorder` also keeps it in memory. A full disk, a
read-only filesystem, or a locked database must degrade to "this session was
not persisted", never to a dropped WebSocket mid-exercise. So the recorder
catches `StoreError` around every call into this module (see
app/events/recorder.py) and the methods here raise it rather than leaking
`sqlite3` exception types upward.

THREADING. One connection, created with `check_same_thread=False` and
guarded by a `threading.Lock`. Two different threads genuinely reach this:
the event loop thread (command dispatch) and `asyncio.to_thread` workers.
The lock serialises them, which is correct and costs nothing at a
classroom's write rate. WAL journaling keeps a single INSERT in the
sub-millisecond range, which is why these calls are made synchronously from
the dispatch path rather than being pushed onto another thread.

STILL NOT A SHELL, AND NOT A QUERY SURFACE. Every statement below is a
literal with bound `?` parameters. No SQL is ever built from student input,
no identifier is interpolated, and nothing in this module reads a client
frame. `app/commands/parser.py` remains the only thing that looks at what a
student typed.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from app import config
from app.events.clock import from_iso, to_iso
from app.events.records import (
    HackCommandRecord,
    HackEventRecord,
    HackSessionRecord,
    decode_data,
    encode_data,
)

logger = logging.getLogger(__name__)

#: In-memory database target, for tests and for a deployment that explicitly
#: opts out of persistence. Held as a constant so callers do not spell the
#: sqlite3 magic string themselves.
MEMORY_PATH = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hack_sessions (
    session_id  TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS hack_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    name        TEXT,
    argv        TEXT NOT NULL,
    exit_code   INTEGER NOT NULL,
    handled     INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hack_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    data        TEXT NOT NULL,
    command     TEXT,
    exit_code   INTEGER,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hack_commands_session
    ON hack_commands (session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_hack_events_session
    ON hack_events (session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_hack_events_type
    ON hack_events (session_id, event_type);
"""


class StoreError(RuntimeError):
    """Persistence failed. Raised instead of leaking a `sqlite3` error type.

    Callers on the dispatch path treat this as "not persisted" and carry on —
    see `app/events/recorder.py`.
    """


class SqliteEventStore:
    """The Hack Mode event log, backed by one SQLite file.

    Deliberately NOT foreign-keyed between `hack_sessions` and the two child
    tables. A session row is written when a WebSocket connects, but a
    `HackSession` built directly (in a test, or by a future replay harness)
    has no such row, and a constraint there would turn "the session header is
    missing" into "the student's actions were silently discarded". The log is
    evidence: a row with an unknown session id is still worth keeping, and
    `session_started_at` simply returns None for it.
    """

    def __init__(self, path: str | Path = MEMORY_PATH) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        if self._path != MEMORY_PATH:
            parent = Path(self._path).expanduser().resolve().parent
            parent.mkdir(parents=True, exist_ok=True)
            self._path = str(Path(self._path).expanduser().resolve())
        try:
            self._connection = sqlite3.connect(self._path, check_same_thread=False)
        except sqlite3.Error as error:
            raise StoreError(f"could not open the event store: {error}") from error
        self._connection.row_factory = sqlite3.Row
        self._prepare()

    # -- lifecycle ---------------------------------------------------------

    def _prepare(self) -> None:
        """Create the schema and choose durability settings.

        WAL lets a reader (a future Dashboard query) run while a session is
        writing, and keeps an append sub-millisecond. `synchronous=NORMAL`
        is the standard WAL pairing: it survives a process crash, and trades
        only the case of losing the last transactions in a sudden power cut
        for not fsync-ing on the command dispatch path. An in-memory store
        has no journal to configure, so those pragmas are skipped.
        """
        with self._lock:
            try:
                if self._path != MEMORY_PATH:
                    self._connection.execute("PRAGMA journal_mode=WAL")
                    self._connection.execute("PRAGMA synchronous=NORMAL")
                self._connection.executescript(_SCHEMA)
                self._connection.commit()
            except sqlite3.Error as error:
                raise StoreError(f"could not prepare the event store: {error}") from error

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    @property
    def path(self) -> str:
        return self._path

    # -- writes ------------------------------------------------------------

    def _write(self, statement: str, parameters: tuple) -> None:
        with self._lock:
            try:
                self._connection.execute(statement, parameters)
                self._connection.commit()
            except sqlite3.Error as error:
                raise StoreError(f"event store write failed: {error}") from error

    def open_session(self, record: HackSessionRecord) -> None:
        """Write the session header. Re-opening an existing id is a no-op."""
        self._write(
            "INSERT OR IGNORE INTO hack_sessions "
            "(session_id, scenario_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
            (
                record.session_id,
                record.scenario_id,
                to_iso(record.started_at),
                None if record.ended_at is None else to_iso(record.ended_at),
            ),
        )

    def close_session(self, session_id: str, ended_at: datetime) -> None:
        """Stamp when a session ended.

        `ended_at IS NULL` in the WHERE clause makes this idempotent: the
        first close wins, so a teardown path that runs twice cannot move a
        session's end time.
        """
        self._write(
            "UPDATE hack_sessions SET ended_at = ? "
            "WHERE session_id = ? AND ended_at IS NULL",
            (to_iso(ended_at), session_id),
        )

    def append_command(self, record: HackCommandRecord) -> None:
        """Append one submitted command."""
        self._write(
            "INSERT INTO hack_commands "
            "(session_id, sequence, name, argv, exit_code, handled, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.session_id,
                record.sequence,
                record.name,
                encode_data({"argv": list(record.argv)}),
                record.exit_code,
                1 if record.handled else 0,
                to_iso(record.occurred_at),
            ),
        )

    def append_event(self, record: HackEventRecord) -> None:
        """Append one scenario domain event."""
        self._write(
            "INSERT INTO hack_events "
            "(session_id, sequence, event_type, message, data, command, exit_code, "
            "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.session_id,
                record.sequence,
                record.event_type,
                record.message,
                encode_data(record.data),
                record.command,
                record.exit_code,
                to_iso(record.occurred_at),
            ),
        )

    # -- reads -------------------------------------------------------------
    #
    # Everything below orders by `sequence` first, which is the per-session
    # monotonic counter the recorder assigns, and by the autoincrement `id`
    # second as a stable tie-break. Ordering by `occurred_at` would be
    # subtly wrong: two rows can share a timestamp, and only the sequence
    # says which the student caused first.

    def _read(self, statement: str, parameters: tuple) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._connection.execute(statement, parameters).fetchall())
            except sqlite3.Error as error:
                raise StoreError(f"event store read failed: {error}") from error

    def session(self, session_id: str) -> HackSessionRecord | None:
        """The session header, or None if this id was never opened."""
        rows = self._read(
            "SELECT session_id, scenario_id, started_at, ended_at "
            "FROM hack_sessions WHERE session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return HackSessionRecord(
            session_id=row["session_id"],
            scenario_id=row["scenario_id"],
            started_at=from_iso(row["started_at"]),
            ended_at=None if row["ended_at"] is None else from_iso(row["ended_at"]),
        )

    def events_for_session(self, session_id: str) -> tuple[HackEventRecord, ...]:
        """Every domain event recorded for one session, in order."""
        rows = self._read(
            "SELECT session_id, sequence, event_type, message, data, command, "
            "exit_code, occurred_at FROM hack_events "
            "WHERE session_id = ? ORDER BY sequence ASC, id ASC",
            (session_id,),
        )
        return tuple(
            HackEventRecord.create(
                session_id=row["session_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                message=row["message"],
                occurred_at=from_iso(row["occurred_at"]),
                data=decode_data(row["data"]),
                command=row["command"],
                exit_code=row["exit_code"],
            )
            for row in rows
        )

    def commands_for_session(self, session_id: str) -> tuple[HackCommandRecord, ...]:
        """Every command submitted in one session, in order."""
        rows = self._read(
            "SELECT session_id, sequence, name, argv, exit_code, handled, occurred_at "
            "FROM hack_commands WHERE session_id = ? ORDER BY sequence ASC, id ASC",
            (session_id,),
        )
        return tuple(
            HackCommandRecord(
                session_id=row["session_id"],
                sequence=row["sequence"],
                name=row["name"],
                argv=tuple(decode_data(row["argv"]).get("argv", ())),
                exit_code=row["exit_code"],
                handled=bool(row["handled"]),
                occurred_at=from_iso(row["occurred_at"]),
            )
            for row in rows
        )

    def timeline_for_session(
        self, session_id: str
    ) -> tuple[HackCommandRecord | HackEventRecord, ...]:
        """Commands and events merged into the one order they happened in.

        This is the shape Phase 2E reads: a command followed by whichever
        transitions it caused. The shared per-session sequence is what makes
        the merge exact — see `app/events/records.py`. A command sorts before
        the events it produced because the recorder assigns its number first.
        """
        merged = [*self.commands_for_session(session_id), *self.events_for_session(session_id)]
        merged.sort(key=lambda record: record.sequence)
        return tuple(merged)

    def session_ids(self) -> tuple[str, ...]:
        """Every session that has a header row, oldest first."""
        rows = self._read(
            "SELECT session_id FROM hack_sessions ORDER BY started_at ASC, rowid ASC",
            (),
        )
        return tuple(row["session_id"] for row in rows)


# --- process-wide default --------------------------------------------------
#
# Resolved lazily, so merely importing this module never touches the disk —
# which is what lets `tests/conftest.py` install an in-memory store before
# anything records, and keeps a test run from writing to the real classroom
# database.

_default_store: SqliteEventStore | None = None
_default_lock = threading.Lock()


def get_default_store() -> SqliteEventStore:
    """The process-wide event store, opened on first use."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = SqliteEventStore(config.EVENT_DB_PATH)
            logger.info("hack event store opened: %s", _default_store.path)
        return _default_store


def set_default_store(store: SqliteEventStore | None) -> None:
    """Replace the process-wide store. For tests and for app startup."""
    global _default_store
    with _default_lock:
        _default_store = store
