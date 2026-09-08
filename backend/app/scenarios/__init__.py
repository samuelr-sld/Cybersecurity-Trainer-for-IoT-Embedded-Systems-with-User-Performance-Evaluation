"""Scenario engine for the Hack Mode simulated targets.

Position in the pipeline:

    WebSocket -> Session -> Command Router -> Command Handler
                                                    |
                                                    v
                                              Scenario (this package)
                                                    |
                                                    v
                                          simulated Environmental target

Responsibility split:

    base.py           the `Scenario` interface + `ScenarioOutcome`
    state.py          passive per-session dataclasses (`ScenarioState`)
    events.py         `ScenarioEvent` domain events (Phase 2E seam)
    payloads.py       safe parsing of MQTT publish payloads
    environmental.py  `EnvironmentalMonitoringScenario` — the simulation

SECURITY BOUNDARY — inherited and non-negotiable. Nothing here executes an
operating-system action. There is no `subprocess`, `os.system`/`os.popen`,
`shell=True`, `eval`/`exec`, PTY, real nmap, or real MQTT client. Every result
is computed from in-memory state. A student's command is data the simulation
inspects, never a process it spawns.

TRANSPORT + UI INDEPENDENCE. A `Scenario` returns plain text lines and
structured events/state; it imports neither FastAPI/WebSocket nor xterm/React.
`snapshot()` is the Phase 2D seam for rendering the target device panel, and
`events` is the Phase 2E seam for evaluation.
"""

from app.scenarios.base import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    Scenario,
    ScenarioOutcome,
)
from app.scenarios.environmental import EnvironmentalMonitoringScenario
from app.scenarios.events import ScenarioEvent, ScenarioEventType
from app.scenarios.state import ScenarioStage, ScenarioState


def create_default_scenario() -> Scenario:
    """Build the scenario a fresh Hack Mode session starts in.

    One call, one independent instance with its own state — this is what
    `HackSession` uses as its per-session default, so no two sessions can ever
    share scenario state. A future scenario selector would branch here.
    """
    return EnvironmentalMonitoringScenario()


__all__ = [
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_USAGE",
    "EnvironmentalMonitoringScenario",
    "Scenario",
    "ScenarioEvent",
    "ScenarioEventType",
    "ScenarioOutcome",
    "ScenarioStage",
    "ScenarioState",
    "create_default_scenario",
]
