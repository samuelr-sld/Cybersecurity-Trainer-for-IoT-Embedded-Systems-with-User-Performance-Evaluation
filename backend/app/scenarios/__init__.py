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
    registry.py       `ScenarioRegistry` — `scenario_id -> Scenario`
                      selection (Phase 2D.3), the seam a `PanelPackage`'s
                      declared `scenario_id` resolves through

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
from app.scenarios.smart_home import SmartHomeMQTTScenario
from app.scenarios.events import ScenarioEvent, ScenarioEventType
from app.scenarios.registry import (
    DEFAULT_SCENARIO_ID,
    ScenarioFactory,
    ScenarioRegistry,
    UnknownScenarioError,
    build_default_scenario_registry,
)
from app.scenarios.state import ScenarioStage, ScenarioState

#: The process-wide selection table (Phase 2D.3). Built once here so the
#: default seam below and any caller resolving a package's `scenario_id`
#: share one registry, exactly as the command registry is shared.
default_scenario_registry = build_default_scenario_registry()


def create_default_scenario() -> Scenario:
    """Build the scenario a fresh Hack Mode session starts in.

    One call, one independent instance with its own state — this is what
    `HackSession` uses as its per-session default, so no two sessions can ever
    share scenario state.

    As of Phase 2D.3 this resolves through `default_scenario_registry` rather
    than naming a class directly, so the default is chosen the same way a
    panel's declared scenario is. Behaviour is unchanged: it still returns a
    fresh `EnvironmentalMonitoringScenario` (`DEFAULT_SCENARIO_ID`).
    """
    return default_scenario_registry.create(DEFAULT_SCENARIO_ID)


__all__ = [
    "DEFAULT_SCENARIO_ID",
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_USAGE",
    "EnvironmentalMonitoringScenario",
    "SmartHomeMQTTScenario",
    "Scenario",
    "ScenarioEvent",
    "ScenarioEventType",
    "ScenarioFactory",
    "ScenarioOutcome",
    "ScenarioRegistry",
    "ScenarioStage",
    "ScenarioState",
    "UnknownScenarioError",
    "build_default_scenario_registry",
    "create_default_scenario",
    "default_scenario_registry",
]
