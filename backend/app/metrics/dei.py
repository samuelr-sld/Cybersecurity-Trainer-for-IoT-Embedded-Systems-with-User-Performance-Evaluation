"""Debugging Efficiency Index (DEI) — final manuscript, Section 3.10.1.

    DEI_session = SUM((Segment End Time - Segment Start Time))
                  / Number of Debugging Segments in the Session

Algebraically the average duration of one resolved debugging segment: each
term in the sum is divided by the same constant (the segment count), so
summing first and dividing once, as implemented below, is exactly the given
formula — not a simplification of it.

SEGMENTATION RULE (given, unchanged): "A debugging segment begins with a
failed compile, flash, or validation attempt and ends with the next
successful attempt of the SAME TYPE. Multiple failures before success
remain within the same segment. Unresolved segments are excluded."

"Same type" is what makes this a per-`BuildAttemptType` walk: a failed
COMPILE only ever closes on the next successful COMPILE, never on an
intervening flash or validation attempt (they are a different kind of
action entirely — closing a compile-debugging segment on, say, a
successful flash would credit "fixed it" to an attempt that never touched
the compile error). So this function processes each of the three
`BuildAttemptType` values as its own independent chronological sequence,
then pools every resolved segment's duration into the one session-wide
average the formula describes — "Number of Debugging Segments in the
Session" is not scoped per type in the manuscript's wording, only the
segment BOUNDARY is.

UNRESOLVED SEGMENTS ARE EXCLUDED, NOT ZERO-FILLED. A failure with no later
success of the same type (including every failure in a session that is
still open) never contributes a duration — there is no "end time" to
subtract, and inventing one (session end, "now") would fabricate a number
the definition does not describe. A session with only unresolved failures,
or with no failures at all, has NO debugging segments; DEI is then
undefined (0 segments in a divisor), not a silent 0 — the same
"never fake an absent value" discipline every other metric in this package
follows.

DEI CAN BE COMPUTED FROM A LIVE (STILL-OPEN) SESSION. Unlike TTR/AID, which
each need the session's whole story (a single final success, or a fixed
total duration) to mean anything, DEI aggregates independent, already-
CLOSED episodes — a resolved segment's duration is fixed the moment the
matching success attempt lands, regardless of what the session does next.
So this function does not gate on `session.ended_at` at all; segments still
open when computed are simply excluded, exactly as the definition says.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.build.records import BuildAttemptType
from app.metrics.results import MetricValue

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.build.records import BuildAttemptRecord, BuildSessionRecord


def _resolved_segment_durations(
    attempts: Sequence["BuildAttemptRecord"],
) -> list[float]:
    """Every CLOSED failed->next-success-of-the-same-type segment's duration.

    Processes each `BuildAttemptType` as its own chronological sequence
    (already sorted by the caller) — see the module docstring for why "same
    type" requires this rather than one merged timeline.
    """
    durations: list[float] = []
    for attempt_type in BuildAttemptType:
        segment_start = None
        for attempt in attempts:
            if attempt.attempt_type is not attempt_type:
                continue
            if not attempt.success:
                if segment_start is None:
                    segment_start = attempt.occurred_at
                # else: still inside an open segment — multiple failures
                # before success do not start a new one.
            else:
                if segment_start is not None:
                    durations.append(
                        (attempt.occurred_at - segment_start).total_seconds()
                    )
                    segment_start = None
                # else: a success with no preceding failure of this type —
                # not a debugging segment at all, nothing to close.
        # A `segment_start` still set here is an unresolved segment
        # (trailing failure(s) with no later success of the same type) —
        # excluded, per the definition.
    return durations


def compute_dei(
    session: "BuildSessionRecord", attempts: Sequence["BuildAttemptRecord"]
) -> MetricValue:
    """DEI for one Build Mode session, given its header and attempt log.

    `attempts` may be any collection — filtered to `session.session_id`
    itself, then to sequence order, matching every other metric function's
    defence-in-depth convention. `session` is taken only for that
    filtering, consistent with `compute_ttr`/`compute_aid`'s signatures.
    """
    scoped = sorted(
        (attempt for attempt in attempts if attempt.session_id == session.session_id),
        key=lambda attempt: attempt.sequence,
    )
    durations = _resolved_segment_durations(scoped)
    if not durations:
        return MetricValue.not_applicable(
            "no resolved debugging segment (failure followed by a "
            "same-type success) was recorded for this session"
        )

    dei = sum(durations) / len(durations)
    return MetricValue.computed(
        dei,
        detail=f"{len(durations)} resolved debugging segment(s), average {dei:.3f}s",
    )
