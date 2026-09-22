"""Attempt and Iteration Density (AID) — final manuscript, Section 3.10.1.

    AID_session = (Total Compile Attempts + Flash Attempts + Validation Attempts)
                  / Session Duration (minutes)

NAME CORRECTION, FORMULA UNCHANGED. The task brief that commissioned this
metric's implementation (Phase 2E.3) labelled it "Attempt-to-Iteration
Depth"; the manuscript itself — verified directly against the one capstone
manuscript file present in this environment (Phase 2E.2's manuscript
verification, re-checked for this phase) — names it "Attempt and Iteration
Density" everywhere it appears (7 occurrences across the source PDF). The
acronym `AID` and the formula above are unchanged either way; only the
expanded name is corrected here to match the manuscript, per the same
"zero metric reinterpretation" rule applied to EAC in `app/metrics/eac.py`.
"Density" (a per-minute rate) also matches the formula's actual shape far
better than "Depth" would.

TOTAL ATTEMPTS. The sum of every recorded `BuildAttemptRecord` for the
session, regardless of `attempt_type` or outcome — a failed compile counts
exactly as much as a successful one, matching "Total ... Attempts" read
literally. `app/build/service.py` records one row per completed compile,
flash, or (once a real check exists) validation attempt; this function does
not distinguish success from failure, only counts.

SESSION DURATION. Only well-defined once the session has actually ended —
an in-progress session's eventual total duration is not yet known, and a
"live" density computed against elapsed-so-far time would change on every
call and would not be a stored, reproducible figure. So AID is
NOT_YET_COMPUTABLE for an open session, exactly like TTR/TTE's treatment of
an unresolved outcome, and COMPUTED (including a real `0.0` for zero
attempts) once `ended_at` is set — a zero-attempt session that closes has a
real, measured density of zero, not a gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.metrics.results import MetricValue

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.build.records import BuildAttemptRecord, BuildSessionRecord


def compute_aid(
    session: "BuildSessionRecord", attempts: Sequence["BuildAttemptRecord"]
) -> MetricValue:
    """AID for one Build Mode session, given its header and attempt log.

    `attempts` may be any collection — filtered to `session.session_id`
    itself, matching every other metric function's defence-in-depth
    convention.
    """
    if session.ended_at is None:
        return MetricValue.not_yet_computable(
            "Build Mode session is still in progress; total duration is not "
            "yet known"
        )

    duration_minutes = (session.ended_at - session.started_at).total_seconds() / 60
    if duration_minutes <= 0:
        return MetricValue.not_applicable(
            "Build Mode session has zero or negative recorded duration"
        )

    total_attempts = sum(
        1 for attempt in attempts if attempt.session_id == session.session_id
    )
    density = total_attempts / duration_minutes
    return MetricValue.computed(
        density,
        detail=(
            f"{total_attempts} attempt(s) over {duration_minutes:.3f} minute(s)"
        ),
    )
