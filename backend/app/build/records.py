"""What gets recorded for Build Mode: session header + attempt outcomes.

Phase 2E.3. Mirrors `app/events/records.py`'s Hack Mode row shapes, at the
granularity Build Mode actually has. Hack Mode splits "what was submitted"
(`HackCommandRecord`) from "what changed" (`HackEventRecord`) because a
command router accepts arbitrary attempts, most of which cause nothing.
Build Mode has no command router: a compile, a flash, and a validation are
each already one discrete, backend-initiated ACTION with exactly one
terminal outcome (`app/build/service.py::compile_workspace`/`_finish_flash`
already collapse "started" and "finished" into a single caller-visible
result). So one row per completed attempt — `BuildAttemptRecord` — is the
whole model; there is no separate "command" table to keep in sync with it.

WHY THIS LIVES IN `app/build/`, NOT `app/events/`. Hack Mode and Build Mode
stay separate execution environments (their own session types, their own
event vocabularies — `app/scenarios/events.py` vs `app/build/events.py`).
This module is Build Mode's own row shapes, following that same split. What
IS shared is the low-level plumbing: server timestamps
(`app/events/clock.py`) and the one SQLite file/connection
(`app/events/store.py`, extended with two more tables below) — reusing
those is exactly "do not duplicate the generic event architecture", not a
merger of the two modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class BuildAttemptType(str, Enum):
    """The three categories AID/DEI (final manuscript, Section 3.10.1) count.

    A closed, generic vocabulary — not panel-specific, exactly like
    `ScenarioEventType` is for Hack Mode. Every Build Mode action that can
    fail and be retried is one of these three; nothing here names a panel,
    a tool, or a file.
    """

    COMPILE = "compile"
    FLASH = "flash"
    VALIDATION = "validation"


@dataclass(frozen=True)
class BuildSessionRecord:
    """One Build Mode session's lifetime.

    `started_at` is TTR's anchor — the "Build Mode Session Start Timestamp"
    the final manuscript's formula names, written when the session is
    created (mirroring `HackSessionRecord.started_at`, and deliberately NOT
    the Hack Mode session's own start: the two are different clocks over
    different activities, and TTR must never be measured from the wrong
    one). `ended_at` is None while the session is live.

    `panel_id` is the Phase 2E.3 analogue of `HackSessionRecord.
    participant_id`: identity Build Mode CAN resolve (unlike a participant,
    the attached panel is read from the shared, already-existing hardware/
    panel-resolution layer — see `app/build_panel_resolution.py`), captured
    passively at session start. None whenever no panel was resolved (no
    board attached, unidentified, unregistered, or no package) — the honest
    "unknown at the time" state, never guessed.
    """

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    panel_id: str | None = None
    #: The registered participant who owns this session (Evaluation phase —
    #: see `app/participants.py`), or None when the connection named none.
    participant_id: str | None = None


@dataclass(frozen=True)
class BuildAttemptRecord:
    """One completed compile, flash, or validation attempt.

    `success` is the attempt's own outcome — `CompileOutcome.success` /
    `FlashOutcome.success` for the two kinds Build Mode already performs for
    real, or whatever a (future, real) validation check decided. It carries
    no claim beyond "the attempt of this type this session made, at this
    moment, ended this way" — in particular a successful COMPILE or FLASH
    attempt is NOT successful remediation evidence (see `app/metrics/ttr.py`
    and the "no validation exists yet" note in `app/build/service.py`).

    `detail` is a short, backend-authored note (a failure category, a
    validation description) — diagnostic text, never parsed by anything
    downstream, exactly like `HackEventRecord.message`.
    """

    session_id: str
    sequence: int
    attempt_type: BuildAttemptType
    success: bool
    occurred_at: datetime
    detail: str = ""
