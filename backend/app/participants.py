"""Participant identity — who owns a Hack/Build session (Evaluation phase).

    Student Access -> POST /api/participants(/sign-in) -> participants table
    /ws/hack?participant=<id>, /ws/build?participant=<id>
        -> resolve_participant() -> HackSession / BuildSession.participant_id
        -> hack_sessions / build_sessions.participant_id -> Evaluation

THE SMALLEST IDENTITY THE PLATFORM ALREADY HAD. The Student Access screen has
always asked for a student number and a full name, and nothing else; before
this module that pair lived only in React state and never reached the
backend, so every recorded session had `participant_id = None` and EAC could
never be computed. This module makes that same pair durable in the existing
SQLite store. It adds no password, token, or authentication framework: the
trainer runs on an isolated, offline classroom network, and the identity's
job is attributing recorded sessions to the right student, not access
control. A client could name another registered student's number — exactly
as it could type it into the existing sign-in form — and that limitation is
stated rather than hidden.

NEVER GUESSED. `resolve_participant` returns a participant id only when the
supplied value is well-formed AND registered. Anything else — absent,
malformed, unknown, or a store failure — yields None, the same honest
"unknown owner" every session had before, and never breaks a connection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.events.clock import utc_now
from app.events.records import ParticipantRecord
from app.events.store import SqliteEventStore, StoreError, get_default_store

logger = logging.getLogger(__name__)

#: A student number: letters, digits and hyphens (e.g. `2021-04213`,
#: `TEST-STUDENT-01`). Bounded and free of whitespace/separators so it is safe
#: to carry in a WebSocket query string and to store verbatim.
PARTICIPANT_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9-]{0,31}"

MAX_FULL_NAME_CHARS = 80


class ParticipantError(ValueError):
    """A registration or sign-in request that cannot be honoured."""


class ParticipantExistsError(ParticipantError):
    """Registration named a student number that is already registered."""


class ParticipantNotFoundError(ParticipantError):
    """Sign-in named no registered participant with that number and name."""


def normalize_participant_id(raw: object) -> str | None:
    """The canonical student number, or None if `raw` is not one."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value if re.fullmatch(PARTICIPANT_ID_PATTERN, value) else None


def normalize_full_name(raw: object) -> str | None:
    """A trimmed, single-spaced printable name, or None if `raw` is not one."""
    if not isinstance(raw, str):
        return None
    value = " ".join(raw.split())
    if not value or len(value) > MAX_FULL_NAME_CHARS or not value.isprintable():
        return None
    return value


@dataclass(frozen=True)
class ParticipantService:
    """Register / sign in / resolve participants against one store.

    `store` is injected for tests; left None it uses the process-wide store,
    the same one the Hack/Build recorders write sessions to.
    """

    store: SqliteEventStore | None = None

    def _store(self) -> SqliteEventStore:
        return self.store if self.store is not None else get_default_store()

    def register(self, participant_id: object, full_name: object) -> ParticipantRecord:
        pid = normalize_participant_id(participant_id)
        name = normalize_full_name(full_name)
        if pid is None:
            raise ParticipantError(
                "student number must be 1-32 letters, digits or hyphens"
            )
        if name is None:
            raise ParticipantError(
                f"full name is required (at most {MAX_FULL_NAME_CHARS} characters)"
            )
        record = ParticipantRecord(
            participant_id=pid, full_name=name, registered_at=utc_now()
        )
        if not self._store().register_participant(record):
            raise ParticipantExistsError("that student number is already registered")
        return record

    def sign_in(self, participant_id: object, full_name: object) -> ParticipantRecord:
        pid = normalize_participant_id(participant_id)
        name = normalize_full_name(full_name)
        record = None if pid is None else self._store().participant(pid)
        if record is None or name is None or record.full_name.lower() != name.lower():
            raise ParticipantNotFoundError(
                "no registered student matches that student number and full name"
            )
        return record

    def get(self, participant_id: object) -> ParticipantRecord | None:
        pid = normalize_participant_id(participant_id)
        return None if pid is None else self._store().participant(pid)

    def all(self) -> tuple[ParticipantRecord, ...]:
        return self._store().participants()

    def resolve(self, raw: object) -> str | None:
        """The registered participant id `raw` names, or None. Never raises."""
        if raw is None or raw == "":
            return None
        try:
            record = self.get(raw)
        except StoreError:
            logger.warning("participant lookup failed; session will be unattributed")
            return None
        if record is None:
            logger.info("connection named an unregistered participant; left unattributed")
            return None
        return record.participant_id


def resolve_participant(raw: object) -> str | None:
    """Connect-time helper for `/ws/hack` and `/ws/build`. Never raises."""
    return ParticipantService().resolve(raw)
