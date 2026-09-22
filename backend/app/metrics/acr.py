"""Attack Completion Rate (ACR) — final manuscript, Section 3.10.1.

    ACR_session = (Objectives Completed in Session / Total Objectives for Module) x 100%

GENERIC BY CONSTRUCTION. This module knows nothing about MQTT, motors, or
any panel. It reads two pieces of already-recorded data: a `PanelPackage`'s
declared `evaluation.objectives` (`ObjectiveDeclaration`, see
`app/panels/models.py`) and one session's recorded `HackEventRecord`s
(`app/events/records.py`). Both are plain data; nothing here dispatches a
command, touches a scenario instance, or imports one.

OBJECTIVE COMPLETION IS NOT SUCCESS-CONDITION COMPLETION. `PanelPackage`
already had a `SuccessCondition` concept before this phase (an evaluator's
overall pass/fail conjunction) with a different count and a different
purpose than "the module's declared learning objectives". Reusing it for
ACR would have been exactly the "success conditions == objectives" mistake
the Phase 2E.2 brief explicitly warned against, so `ObjectiveDeclaration` is
its own, separate seam — see that class's docstring for the full reasoning.

`evaluation.objectives` ARE THE ACTIVITY'S MEASURABLE GOALS, NOT ITS BROADER
LEARNING OUTCOMES. `LearningContent.objectives` (a package's OTHER,
separate objectives field) is free-text academic/conceptual material shown
to a student — "explain why X" — and is never read by this module. The
Phase 2E.2 correction that introduced `ObjectiveDeclaration` initially
mirrored the two REFLECTIVE entries from that academic list into
`evaluation.objectives` as well, which produced empty-`required_events`
objectives that could never auto-complete and capped every real session's
ACR below 100% even for a perfect run. That was a modelling mistake, not a
manuscript requirement: `evaluation.objectives` are supposed to be the
activity's own measurable milestones (e.g. "extract the firmware", "observe
the MQTT communication"), each with an observable event signature, and a
correctly-authored package's `evaluation.objectives` should therefore all be
completable — see `backend/panels/smart-home-mqtt-control/panel.json` for
the corrected five.

WHY THE `required_events = ()` CASE STILL EXISTS. It remains a generic
architectural safety valve, not something any shipped package currently
uses: `ObjectiveDeclaration.required_events` can legitimately be empty for a
future package's objective that turns out to have no technical detection
method, and this function is explicit about the consequence — such an
objective NEVER auto-completes (not vacuously true, not silently dropped
from the total) and honestly caps that module's automated ACR ceiling below
100%, rather than the alternative (shrinking the denominator, or guessing a
proxy event) that the manuscript-fidelity rule forbids. See
`ObjectiveDeclaration`'s own docstring in `app/panels/models.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.metrics.results import MetricValue

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.events.records import HackEventRecord
    from app.panels.models import PanelPackage


def compute_acr(
    package: "PanelPackage", session_id: str, events: Sequence["HackEventRecord"]
) -> MetricValue:
    """ACR for one session, given its package and its recorded event log.

    `events` may be any collection of recorded events — this function filters
    to `session_id` itself (defence in depth: a completion recorded under a
    different session must never inflate this one's count, even if a caller
    passed an unscoped list). Order does not matter — this only asks "did
    this event type ever occur in THIS session", not "when" or "in what
    order". Typical usage passes `SqliteEventStore.events_for_session(session_id)`,
    which is already scoped, making the filter a no-op in practice.

    Always COMPUTED — the manuscript is explicit that ACR is reported even
    for an unsuccessful or incomplete session, including 0% — UNLESS the
    module declares no objectives at all, which is a package-authoring gap
    (a 0/0 division), not a normal session outcome.
    """
    declared = package.evaluation.objectives
    total = len(declared)
    if total == 0:
        return MetricValue.not_applicable(
            f"module {package.panel_id!r} declares no objectives for ACR"
        )

    occurred = {
        event.event_type for event in events if event.session_id == session_id
    }
    completed = sum(
        1
        for objective in declared
        if objective.required_events and set(objective.required_events) <= occurred
    )

    percentage = (completed / total) * 100
    return MetricValue.computed(
        percentage,
        detail=f"{completed}/{total} declared objectives completed",
    )
