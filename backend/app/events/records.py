"""What gets recorded: the three row shapes of the Hack Mode event log.

    HackSessionRecord   one Hack Mode session      (when it began / ended)
    HackCommandRecord   one command the student submitted, and how it ended
    HackEventRecord     one scenario domain transition that actually occurred

WHY COMMANDS AND EVENTS ARE SEPARATE ROWS. They answer different questions,
and collapsing them would lose one of the two.

A `HackEventRecord` says *the target changed*: the firmware really was
analyzed, the spoof really was accepted. Its vocabulary is
`ScenarioEventType` (app/scenarios/events.py) and it is emitted only by the
scenario engine, only on a real state transition — a command that connects to
the wrong host changes nothing and therefore produces no event row.

A `HackCommandRecord` says *the student tried something*: which tool, with
which arguments, and what it exited with. Most attempts are not transitions,
and the ones that fail are exactly the ones an evaluator needs — a student
who reached the objective in three commands did not do the same thing as one
who reached it in thirty.

`app/scenarios/events.py` already draws this line in prose ("Command-level
facts ... are NOT scenario events: they are available at the router seam as
the command name plus the `CommandResult.exit_code`"). These records are that
seam made durable.

ONE SEQUENCE ACROSS BOTH. Commands and events share a single per-session
counter, so merging the two tables recovers the true interleaved order of a
session even when several rows land inside the same clock tick. Timestamps
alone are not enough for that: a command and the event it caused can easily
share a microsecond-resolution stamp, and only the sequence says which came
first. See `app/events/recorder.py`, which owns the counter.

NOTHING HERE IS SCORED. These are facts, not metrics. ACR / RE / EAC / TTE
are Phase 2E's to compute from these rows; this module deliberately contains
no arithmetic, no thresholds, and no notion of success beyond what the
scenario engine and the exit code already stated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from app.events.clock import to_iso

#: Cap on a stored argument token. The parser already bounds a whole command
#: line (MAX_COMMAND_CHARS) and strips control characters, so this is not a
#: safety boundary — it only keeps one pathological token from dominating a
#: row. Arguments are stored because a spoof payload IS the evidence that the
#: attack happened; truncating a very long one loses nothing an evaluator
#: needs.
MAX_STORED_ARG_CHARS = 256

#: Cap on how many argument tokens are stored for one command. The parser
#: allows 64; beyond a handful, further tokens say nothing about what the
#: student was attempting.
MAX_STORED_ARGS = 24


def _freeze(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Take an immutable snapshot, so a stored record cannot be edited later."""
    return MappingProxyType(dict(data) if data else {})


def _clip(token: str) -> str:
    if len(token) <= MAX_STORED_ARG_CHARS:
        return token
    return token[:MAX_STORED_ARG_CHARS] + "..."


def clip_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Bound a command's stored argument list in both length and width."""
    return tuple(_clip(token) for token in argv[:MAX_STORED_ARGS])


@dataclass(frozen=True)
class HackSessionRecord:
    """One Hack Mode session's lifetime.

    `started_at` is the Phase 2E anchor for TTE: time-to-exploitation is
    measured from here to the first `spoof_succeeded` / `attack_completed`
    event of the same session, which is why this row is written when the
    WebSocket connects rather than when the first command arrives.

    `ended_at` is None while the session is live.

    `participant_id` is the Phase 2E.2 EAC seam, filled since the Evaluation
    phase with the registered student number the frontend passes on connect
    (`app/participants.py`), or None when none was supplied or it is not a
    registered participant. Historical context follows. Exploitation Attempt Count
    (the final manuscript's actual name for the metric the task brief calls
    "EAC" — see `app/metrics/eac.py`) counts SESSIONS by the same participant
    at the same learning activity, and nothing in this backend identifies a
    participant anywhere: there is no login, no auth, and the frontend's
    student roster (`src/data.js`) is fabricated prototype data that never
    reaches this WebSocket (see that module's own docstring). Inventing an
    identity here to make the formula work is exactly what Phase 2E.2 was
    told not to do, so this field stays honestly nullable — None for every
    session today, since no caller has a real identity to supply — and is
    the seam a future authentication phase fills in. `scenario_id` already
    serves as "learning activity" (a MAC resolves through a panel to exactly
    one scenario id), so no second field is needed for that half of the
    grouping key.
    """

    session_id: str
    scenario_id: str
    started_at: datetime
    ended_at: datetime | None = None
    participant_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scenario_id": self.scenario_id,
            "started_at": to_iso(self.started_at),
            "ended_at": None if self.ended_at is None else to_iso(self.ended_at),
            "participant_id": self.participant_id,
        }


@dataclass(frozen=True)
class HackCommandRecord:
    """One submitted command line and how the backend resolved it.

    `name` is the command token as registered (`nmap`, `mosquitto_pub`), or
    None when the line could not be parsed at all — a syntax error is still
    an action the student took, and dropping it would understate their effort.

    `argv` is the parsed, already control-character-free token list, clipped
    by `clip_argv`. The raw line is deliberately NOT stored: for a parsed
    command `argv` reconstructs it, and for an unparsed one the raw text is
    the only string in this pipeline that never passed the parser's filter,
    so it is the one thing not worth keeping.

    `exit_code` follows the shell convention the router already uses — 0 ok,
    1 failure, 2 usage, 127 unknown command — which is precisely what
    `app/commands/base.py` says it is for.

    `handled` is True when a registered handler actually ran. It separates
    "the tool ran and reported failure" (handled, non-zero exit) from "there
    was no such tool" (not handled), which read identically from the exit
    code alone.

    `fields_correct` is the Phase 2E.2 Reconnaissance Efficiency seam —
    `ScenarioOutcome.fields_correct` (app/scenarios/base.py) forwarded
    verbatim through `CommandResult` and the router. None means "not
    assessed / not applicable" (an unrecognized command, or a tool with no
    target-fact fields to get wrong); True/False is the scenario's own,
    ground-truth-backed verdict for a tool that has such fields.
    """

    session_id: str
    sequence: int
    name: str | None
    argv: tuple[str, ...]
    exit_code: int
    handled: bool
    occurred_at: datetime
    fields_correct: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "name": self.name,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "handled": self.handled,
            "occurred_at": to_iso(self.occurred_at),
            "fields_correct": self.fields_correct,
        }


@dataclass(frozen=True)
class HackEventRecord:
    """One scenario domain transition, timestamped and placed in a session.

    `event_type` / `message` / `data` are carried verbatim from the
    `ScenarioEvent` the engine emitted — this record adds a session, a
    sequence, a server timestamp, and the command that caused it, and changes
    nothing about what the event means. The scenario vocabulary is not
    extended, renamed, or reinterpreted here.

    `command` is the tool whose dispatch produced this transition, kept so an
    evaluator can attribute a discovery to the action that achieved it
    without re-deriving it from timestamps.
    """

    session_id: str
    sequence: int
    event_type: str
    message: str
    occurred_at: datetime
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    command: str | None = None
    exit_code: int | None = None

    @classmethod
    def create(
        cls,
        session_id: str,
        sequence: int,
        event_type: str,
        message: str,
        occurred_at: datetime,
        data: Mapping[str, Any] | None = None,
        command: str | None = None,
        exit_code: int | None = None,
    ) -> "HackEventRecord":
        """Build a record with an immutable snapshot of `data`."""
        return cls(
            session_id=session_id,
            sequence=sequence,
            event_type=event_type,
            message=message,
            occurred_at=occurred_at,
            data=_freeze(data),
            command=command,
            exit_code=exit_code,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "event": self.event_type,
            "message": self.message,
            "data": dict(self.data),
            "command": self.command,
            "exit_code": self.exit_code,
            "occurred_at": to_iso(self.occurred_at),
        }


@dataclass(frozen=True)
class ParticipantRecord:
    """One registered participant — the identity that owns Hack/Build sessions.

    `participant_id` is the student number the Student Access screen already
    collects; it is what `HackSessionRecord.participant_id` and
    `BuildSessionRecord.participant_id` store. There is deliberately no
    password: the platform's established access model is a student number
    (registered once together with a full name, then used alone to sign in) on
    a closed, offline classroom network, and this record makes that identity
    durable rather than inventing an authentication scheme.
    """

    participant_id: str
    full_name: str
    registered_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "full_name": self.full_name,
            "registered_at": to_iso(self.registered_at),
        }


def encode_data(data: Mapping[str, Any]) -> str:
    """Serialise an event's detail mapping for the database's TEXT column.

    `default=str` rather than a failure: an event's `data` is diagnostic
    detail, and a value that happens not to be JSON-native must not be able
    to abort the recording of the event it belongs to.
    """
    return json.dumps(dict(data), default=str)


def decode_data(raw: str) -> dict[str, Any]:
    """Parse a `data` column back, tolerating a row written by other means."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):  # pragma: no cover - hand-edited row
        return {}
    return value if isinstance(value, dict) else {}
