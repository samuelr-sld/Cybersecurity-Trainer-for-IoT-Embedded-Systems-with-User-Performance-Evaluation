"""Time-to-Resolution (TTR) — final manuscript, Section 3.10.1.

    TTR = Timestamp of Validated Successful Fix - Build Mode Session Start Timestamp

Mirrors `app/metrics/tte.py` structurally — same shape of question, same
defensive session-scoping, same NOT_YET_COMPUTABLE-vs-NOT_APPLICABLE split
— over Build Mode's own record shapes (`app/build/records.py`) instead of
Hack Mode's.

SUCCESSFUL-FIX EVENT. The Phase 2E.3 brief is explicit that a compile
succeeding, or a flash succeeding, is NOT successful remediation evidence —
only a VALIDATION success is. This function therefore looks for the first
`BuildAttemptRecord` with `attempt_type is BuildAttemptType.VALIDATION and
success is True`, never a compile or flash attempt, regardless of how many
of either preceded it. See `app/build/service.py::
BuildService.record_validation_attempt` for where such a record can come
from — no real validation mechanism exists in this codebase yet (see that
method's docstring), so today this will not find one during ordinary use;
this function is correct and ready for when it can.

SESSION START IS BUILD MODE'S OWN. `session.started_at` here is
`BuildSessionRecord.started_at` — captured at Build Mode session creation
(`app/build_sessions.py::BuildSessionManager.create`), never a Hack Mode
session's start. The two are different sessions over different activities;
conflating them would silently redefine what TTR measures.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.build.records import BuildAttemptType
from app.metrics.results import MetricValue

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.build.records import BuildAttemptRecord, BuildSessionRecord


def compute_ttr(
    session: "BuildSessionRecord", attempts: Sequence["BuildAttemptRecord"]
) -> MetricValue:
    """TTR for one Build Mode session, given its header and attempt log.

    `attempts` may be any collection — this filters to `session.session_id`
    itself (defence in depth, same reasoning as `app/metrics/tte.py`) and
    then to sequence order, so the FIRST successful validation attempt in
    the scoped list is used. Typical usage passes
    `SqliteEventStore.build_attempts_for_session(session_id)`, already
    scoped and ordered, making both steps a no-op in practice.
    """
    scoped = sorted(
        (attempt for attempt in attempts if attempt.session_id == session.session_id),
        key=lambda attempt: attempt.sequence,
    )
    success = next(
        (
            attempt
            for attempt in scoped
            if attempt.attempt_type is BuildAttemptType.VALIDATION and attempt.success
        ),
        None,
    )
    if success is None:
        if session.ended_at is None:
            return MetricValue.not_yet_computable(
                "Build Mode session is still in progress and has not yet "
                "reached a validated successful fix"
            )
        return MetricValue.not_applicable(
            "Build Mode session ended without a validated successful fix"
        )

    delta = (success.occurred_at - session.started_at).total_seconds()
    if delta < 0:
        return MetricValue.not_applicable(
            "recorded validation-success timestamp precedes the Build Mode "
            "session start timestamp"
        )

    return MetricValue.computed(
        delta, detail=f"validated successful fix {delta:.3f}s after Build Mode session start"
    )
