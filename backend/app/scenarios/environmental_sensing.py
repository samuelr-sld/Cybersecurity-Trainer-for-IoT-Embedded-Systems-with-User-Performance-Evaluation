"""The Environmental Monitoring System scenario — Panel 2's neutral foundation target.

Panel 2 is a FOUNDATION module: its package establishes the board's identity
and its real firmware (`backend/panels/environmental-monitoring/`) and
deliberately declares no training activity. This scenario is the executable
half of that same statement, and nothing more:

    "Environmental Monitoring System — foundation, no objectives yet."

WHY IT EXISTS. A package's `scenario_id` is looked up in the scenario registry
(`app/scenarios/registry.py`). Before this class existed Panel 2's id
(`environmental-sensing`) was registered to nothing, so the selector fell back
to the engine's default development scenario — the simulated MQTT/BME280
telemetry target — and a student sitting at Panel 2 was silently handed a
different panel's experiment. An unregistered id is now impossible for a real
panel, and registering an honest, empty target is what closes that: the engine
resolves the id to THIS class and never to the default.

WHAT IT IS NOT. It defines no vulnerability, no remediation, no security
region, no objectives, no guided tasks, no learning outcome and no evaluation
criterion, and it invents no target facts. It is therefore not a subclass of
(or built on) any other scenario, and it imports none of their state: the
legacy target's `ScenarioState` carries a BME280 telemetry topic and an
address, and Panel 1's carries a motor controller, and a neutral target must
not be able to leak either. Its state is its own, and it is empty.

EVERY OPERATION IS AN HONEST "NOTHING TO DO". The generic engine offers one
global toolbox to every panel (`app/commands/`), so each of the five
`Scenario` operations is still callable here. Each answers that no target is
defined, as a failure outcome with no events and no `fields_correct` verdict
(`None` is "not assessed": a student cannot have got a required field wrong
for a target that has none). The failure outcome is also the truthful one for
the tool: with no board attached there is nothing to read, scan or publish to,
and with a board attached the firmware handlers use their own REAL hardware
path and consult the scenario only for events — of which this emits none.

A LATER PHASE REPLACES THIS ENTRY, NOT THE ENGINE. When Panel 2 gets a real
activity, the one registry row for `environmental-sensing` is re-pointed at
its own class — exactly how Panel 1's id moved from a temporary mapping to
`SmartHomeMQTTScenario` — with no selector, router or handler change.

PURE AND IN-MEMORY, like every `Scenario` (`app/scenarios/__init__.py`'s
security boundary): no subprocess, no serial port, no network, no hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.scenarios.base import Scenario, ScenarioOutcome
from app.scenarios.events import ScenarioEvent

#: The id Panel 2's package declares (`panel.json` -> `scenario.scenario_id`).
#: The package stays the authority on WHICH scenario the panel uses; this is
#: the registry-side half of that mapping, the same way the legacy default's
#: id is read off its class.
SCENARIO_ID = "environmental-sensing"

#: The one sentence this scenario has to say about itself.
FOUNDATION_NOTICE = (
    "the Environmental Monitoring System is a foundation module; "
    "no training activity is defined for it yet"
)


@dataclass(frozen=True)
class EnvironmentalSensingState:
    """The neutral scenario's state: there is none, on purpose.

    A scenario's state is what a student has discovered, attempted or
    achieved. A foundation module has no objectives, so there is nothing to
    discover and no stage to be at; giving this class fields would be
    inventing them. It exists as a type (rather than `None`) so `Scenario.state`
    keeps returning an object and so a later activity has an obvious home for
    real state to grow into.
    """


def _nothing_to_do(tool: str) -> ScenarioOutcome:
    """The one answer every operation gives. See the module docstring."""
    return ScenarioOutcome.failed(f"{tool}: {FOUNDATION_NOTICE}.")


class EnvironmentalSensingScenario(Scenario):
    """The empty, honest scenario Panel 2 runs until it has an activity."""

    scenario_id = SCENARIO_ID

    def __init__(self) -> None:
        self._state = EnvironmentalSensingState()

    # -- interface: introspection -----------------------------------------

    @property
    def state(self) -> EnvironmentalSensingState:
        return self._state

    @property
    def events(self) -> tuple[ScenarioEvent, ...]:
        # Nothing a student does here is a domain transition, so nothing is
        # ever emitted — and the recorder therefore stores commands only.
        return ()

    # -- interface: the five operations -----------------------------------

    def extract_firmware(self) -> ScenarioOutcome:
        return _nothing_to_do("esptool.py")

    def analyze_firmware(self, search: str | None = None) -> ScenarioOutcome:
        # What the real tools say about a file that was never produced. With a
        # real capture on this session the handlers print the real bytes'
        # strings and ignore this outcome's lines; without one this is the
        # truthful answer, and it carries no hint about what to do next.
        return ScenarioOutcome.failed("firmware.bin: No such file or directory")

    def scan(self, host: str | None, port: int | None) -> ScenarioOutcome:
        return _nothing_to_do("nmap")

    def observe(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        return _nothing_to_do("mosquitto_sub")

    def publish(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        return _nothing_to_do("mosquitto_pub")

    # -- interface: state serialisation -----------------------------------

    def snapshot(self) -> dict[str, Any]:
        """What the frontend's target panel is told: this is a foundation.

        Deliberately has none of the keys the other scenarios' snapshots share
        (`target`, `discovery`, `attack`, `completion`, `stage`): those describe
        an activity, and this scenario has none. `objectives` is an explicit
        empty list rather than an absent key so "none defined" is stated, not
        implied.
        """
        return {
            "scenario_id": self.scenario_id,
            "foundation": True,
            "objectives": [],
            "summary": FOUNDATION_NOTICE[0].upper() + FOUNDATION_NOTICE[1:] + ".",
        }
