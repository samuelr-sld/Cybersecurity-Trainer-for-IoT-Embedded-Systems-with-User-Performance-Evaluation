"""Exploitation Attempt Count (EAC) — final manuscript, Section 3.10.1.

The task brief that commissioned this phase labelled this metric "Exploit
Action Completeness"; the manuscript itself — verified directly against the
one capstone manuscript file present in this environment (see the *Manuscript
verification* note in the Phase 2E.2 report) — names it "Exploitation
Attempt Count" everywhere it appears (pages 13, 28, 34, 38, 44, 71, 80, 84,
85 of the source PDF). The acronym `EAC` and the formula are unchanged
either way; only the expanded name is corrected here to match the
manuscript, per the "zero metric reinterpretation" rule. The pre-existing
`EvaluationMetric.EAC` enum member (`app/panels/models.py`, Phase 2D.1) is
untouched.

    EAC = number of sessions attempted at the activity, from first entry
          until the session that ends in success.

EAC is SESSION-LEVEL. Five MQTT publish attempts inside one session that
eventually succeeds is still EAC = 1 — this module counts `HackSessionRecord`
rows, never `HackCommandRecord` or `HackEventRecord` rows, so a session with
many internal attempts cannot inflate the count structurally, not merely by
convention.

CROSS-SESSION GROUPING NEEDS A PARTICIPANT IDENTITY THIS BACKEND DOES NOT
HAVE. EAC requires grouping sessions by "same participant, same learning
activity". "Same learning activity" already exists — `HackSessionRecord.
scenario_id` (a MAC resolves through exactly one panel to exactly one
scenario id). "Same participant" does not: there is no login, no
authentication, and no student identity anywhere on `/ws/hack` today (see
`HackSessionRecord.participant_id`'s docstring). This function therefore
takes `participant_id` as a REQUIRED, caller-supplied argument rather than
inventing a grouping key from a session id (which the brief explicitly
forbids: two different students' sessions must never collapse into one
sequence just because nothing else was available) — see the Phase 2E.2
report's Limitations section for the concrete consequence: this metric is
fully implemented and tested, but has no real caller supplying a
participant id anywhere in the product today.

SUCCESS BOUNDARY. `ScenarioEventType.ATTACK_COMPLETED` (app/scenarios/
events.py) is the canonical, scenario-agnostic "the activity's full
objective was achieved" event — both `SmartHomeMQTTScenario` and
`EnvironmentalMonitoringScenario` emit it, each only once, only when their
own completion conjunction is satisfied (never merely "an exploit command
ran" — see `SmartHomeMQTTScenario._recompute_completion`). It is shared
event-vocabulary infrastructure, not panel-specific data, so treating it as
EAC's (and TTE's) success signal is not a hardcoded panel branch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from app.metrics.results import MetricValue
from app.scenarios.events import ScenarioEventType

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.events.records import HackEventRecord, HackSessionRecord

#: The event that marks a session as the successful end of an attempt
#: sequence — see the module docstring's "Success boundary" section.
SUCCESS_EVENT_TYPE = ScenarioEventType.ATTACK_COMPLETED.value


def _session_succeeded(events: Sequence["HackEventRecord"]) -> bool:
    return any(event.event_type == SUCCESS_EVENT_TYPE for event in events)


def compute_eac(
    sessions: Sequence["HackSessionRecord"],
    events_by_session: Mapping[str, Sequence["HackEventRecord"]],
    *,
    participant_id: str | None,
    scenario_id: str,
) -> MetricValue:
    """EAC for one (participant, learning activity) pair.

    `sessions` may be every known session (any order) — this function does
    its own filtering and chronological sort. `events_by_session` maps a
    session id to that session's recorded events; a session with no entry is
    treated as having no recorded events (never yet succeeded).

    `participant_id=None` cannot be grouped against anything — see the
    module docstring — so it always yields NOT_APPLICABLE rather than
    guessing a group of one, which would silently misrepresent "we don't
    know who this is" as "this session's own, real attempt count".
    """
    if participant_id is None:
        return MetricValue.not_applicable(
            "no participant identity is available to group sessions by "
            "(this backend has no authentication on the Hack Mode session)"
        )

    group = sorted(
        (
            session
            for session in sessions
            if session.participant_id == participant_id
            and session.scenario_id == scenario_id
        ),
        key=lambda session: session.started_at,
    )
    if not group:
        return MetricValue.not_applicable(
            f"no sessions recorded for participant {participant_id!r} "
            f"on activity {scenario_id!r}"
        )

    for count, session in enumerate(group, start=1):
        if _session_succeeded(events_by_session.get(session.session_id, ())):
            return MetricValue.computed(
                count,
                detail=(
                    f"session {session.session_id!r} (attempt {count}) "
                    "is the first to reach successful exploitation"
                ),
            )

    return MetricValue.not_yet_computable(
        f"none of {len(group)} recorded attempt(s) has reached successful "
        "exploitation yet"
    )
