"""Time-to-Exploitation (TTE) — final manuscript, Section 3.10.1.

    TTE = Timestamp of Successful Exploitation - Hack Mode Session Start Timestamp

SERVER-SIDE TIMESTAMPS ONLY. `session.started_at` and every event's
`occurred_at` are `app/events/clock.py::utc_now()` stamps — timezone-aware
UTC, generated on the backend at the moment each thing happened (Phase 2B).
This function never reads a client/browser clock; there is none to read,
since the WebSocket protocol has no client-timestamp field at all (see
`app/websocket.py` — the wire protocol carries only `occurred_at`/`sequence`
stamped server-side).

SUCCESS EVENT. `ScenarioEventType.ATTACK_COMPLETED` — see
`app/metrics/eac.py`'s module docstring for why this event, not
`spoof_succeeded`, is the manuscript's "successful exploitation" boundary:
it is the scenario's own full-objective-completion signal, shared and
scenario-agnostic, emitted at most once per session.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.metrics.eac import SUCCESS_EVENT_TYPE
from app.metrics.results import MetricValue

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.events.records import HackEventRecord, HackSessionRecord


def compute_tte(
    session: "HackSessionRecord", events: Sequence["HackEventRecord"]
) -> MetricValue:
    """TTE for one session, given its header and its recorded event log.

    `events` may be any collection — this filters to `session.session_id`
    itself (defence in depth: a success event recorded under a different
    session, e.g. from an unscoped or accidentally-merged list, must never
    be attributed to this one) and then to sequence order, so the FIRST
    matching success event in the *scoped* list is used — also the
    chronologically first should more than one ever exist (the scenario
    engine itself emits the event at most once per session, so this is a
    defensive floor, not the expected path). Typical usage passes
    `SqliteEventStore.events_for_session(session_id)`, already scoped and
    ordered, making both steps a no-op in practice.
    """
    scoped = sorted(
        (event for event in events if event.session_id == session.session_id),
        key=lambda event: event.sequence,
    )
    success = next(
        (event for event in scoped if event.event_type == SUCCESS_EVENT_TYPE), None
    )
    if success is None:
        if session.ended_at is None:
            return MetricValue.not_yet_computable(
                "session is still in progress and has not yet reached "
                "successful exploitation"
            )
        return MetricValue.not_applicable(
            "session ended without reaching successful exploitation"
        )

    delta = (success.occurred_at - session.started_at).total_seconds()
    if delta < 0:
        # Cannot legitimately happen with two `utc_now()` stamps from the
        # same monotonic wall clock, in order, on the same session — but a
        # hand-built record (a test, a hand-edited row) could violate that,
        # and a negative "time to exploit" is not a number to report as if
        # it meant something.
        return MetricValue.not_applicable(
            "recorded success timestamp precedes the session start timestamp"
        )

    return MetricValue.computed(
        delta, detail=f"successful exploitation {delta:.3f}s after session start"
    )
