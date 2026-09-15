"""The one source of time for recorded events.

WHY THIS MODULE EXISTS AT ALL. Phase 2B's whole point is a trustworthy
timeline, and a timeline is only trustworthy if every stamp on it was made
by the same clock in the same format. Two rules follow, and this module is
how they are enforced rather than merely intended:

1. TIMESTAMPS ARE SERVER-SIDE. A student's browser clock is not evidence:
   it can be wrong, in another timezone, or deliberately changed. Nothing in
   the event pipeline ever accepts a timestamp from a client frame — every
   recorded stamp comes from `utc_now()` here, called on the backend at the
   moment the thing happened.

2. ONE REPRESENTATION. Always timezone-aware UTC, serialised as ISO 8601.
   Naive datetimes are rejected on the way to storage (see `to_iso`) instead
   of being silently assumed to be local time, which is the classic way a
   log ends up with two incompatible clocks in one column.

Phase 2E computes TTE and TTR by subtracting two of these stamps, so an
hour-off or naive value here is a wrong grade later.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """The current instant, timezone-aware, in UTC.

    Every event, command and session stamp in the backend originates here.
    """
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Serialise an aware datetime as ISO 8601 UTC, for storage and the wire.

    A naive datetime raises rather than being guessed at: assuming it meant
    UTC (or local time) is exactly the silent error this module exists to
    prevent. An aware non-UTC value is converted, so the stored column is
    uniformly UTC and string ordering matches chronological ordering.
    """
    if moment.tzinfo is None:
        raise ValueError("refusing to store a naive timestamp; use utc_now()")
    return moment.astimezone(timezone.utc).isoformat()


def from_iso(raw: str) -> datetime:
    """Parse a stamp written by `to_iso` back into an aware datetime."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:  # pragma: no cover - only reachable on a hand-edited row
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
