"""What Hack Mode tells a student about the scenario they were dropped into.

THE ONE PLACE A PANEL PACKAGE BECOMES FRONTEND-READY TEXT. A student's Hack
Mode screen needs to know, the instant the terminal opens, which scenario this
is, what its objectives are, what the guide says and which hints apply. All of
that is already declared by the attached panel's package
(`backend/panels/<id>/panel.json`: `scenario`, `learning`, `evaluation`,
`hack`), so this module only reshapes that data into the plain JSON the
`session` frame carries (`app/models/messages.py::SessionMessage.scenario`).
The frontend therefore holds no objective wording, no hint, no guide text and
no panel id: a new panel is a new package, never a branch in the page.

PURE AND PASSIVE. A function of a `PanelPackage`, nothing else. It reads no
file, resolves no device, runs no command, emits no `ScenarioEvent` and sends
nothing — so building it can never be mistaken for an action and a connect
stays the lookup `app/scenario_selection.py` promises. The result is data, and
`tests/test_hack_briefing.py` asserts statically that this module imports no
process, shell or I/O facility.

THREE KINDS, AND THE THIRD IS HONEST, NOT A FALLBACK TO ANOTHER PANEL'S TEXT.

    activity     the package declares evaluation objectives: a real training
                 activity (Panel 1).
    foundation   a package that declares none (Panel 2): the page says so
                 instead of showing an empty or invented objective list.
    unspecified  no package named this session's scenario (no board, an
                 unregistered board, a broken package): there is no scenario
                 information to show, and none is guessed.

`objectives` carry their `required_events`, and the page derives each one's
done / in-progress / pending state from the EVENTS the backend recorded — the
same rule Attack Completion Rate counts by — so progress is never a flag the
page infers from a snapshot or a claim a hint makes.

NO TARGET FACTS. Nothing here states a broker address, a topic, a credential
or a reading. Those belong to the executable scenario and are revealed
through its own state as the student discovers them (see `Scenario.snapshot`).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.panels.models import PanelPackage


class BriefingKind(str, Enum):
    """Which of the three honest situations a session is in. See module doc."""

    ACTIVITY = "activity"
    FOUNDATION = "foundation"
    UNSPECIFIED = "unspecified"


def unspecified_briefing() -> dict[str, Any]:
    """The briefing for a session no package named. Says nothing, invents nothing."""
    return {
        "kind": BriefingKind.UNSPECIFIED.value,
        "panel_id": None,
        "scenario_id": None,
        "title": None,
        "objectives": [],
        "outcomes": [],
        "hints": [],
        "guide": [],
    }


def briefing_for(package: "PanelPackage | None") -> dict[str, Any]:
    """The JSON-serialisable scenario briefing for an attached panel's package.

    `None` (no package named this session's scenario) yields the
    `unspecified` briefing. Otherwise every field is read straight off the
    package; the only decision made here is which `kind` the declared
    objectives imply.
    """
    if package is None:
        return unspecified_briefing()

    objectives = [
        {
            "id": objective.objective_id,
            "label": objective.description,
            "required_events": list(objective.required_events),
        }
        for objective in package.evaluation.objectives
    ]
    hack = package.hack
    return {
        "kind": (BriefingKind.ACTIVITY if objectives else BriefingKind.FOUNDATION).value,
        "panel_id": package.panel_id,
        "scenario_id": package.scenario_id,
        "title": package.title,
        "objectives": objectives,
        # `learning.objectives` are the broader learning outcomes, declared "purely
        # for display" (see `ObjectiveDeclaration`); they are NOT the measurable
        # objectives above and are never counted as progress.
        "outcomes": list(package.learning.objectives),
        "hints": [
            {"id": hint.hint_id, "text": hint.text, "objective_id": hint.objective_id}
            for hint in (hack.hints if hack else ())
        ],
        "guide": [
            {"heading": section.heading, "paragraphs": list(section.paragraphs)}
            for section in (hack.guide if hack else ())
        ],
    }
