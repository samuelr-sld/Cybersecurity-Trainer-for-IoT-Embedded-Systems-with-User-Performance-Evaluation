"""Which `Scenario` implementation a declared `scenario_id` selects.

Position in the architecture (Phase 2D.3):

    PanelPackage.scenario.scenario_id   (app/panels/, the source of truth for
              |                          WHICH scenario a panel declares)
              v
    ScenarioRegistry.create(scenario_id)   (this module)
              |
              v
    a fresh Scenario implementation instance   (app/scenarios/environmental.py, ...)

THE ENGINE PROVIDES THE TOOLS; THE CONNECTED PANEL PROVIDES THE EXPERIMENT.
Phase 2D.1/2D.2 made a panel declare its experiment as data; this is the one
seam that turns that declared `scenario_id` into the executable `Scenario`
the generic Hack Engine already drives. The engine, the command registry and
the scenario classes are all unchanged — this only chooses among them.

SELECTION IS DATA, NOT CODE. A `scenario_id` is a KEY looked up in an
explicit table, exactly as a MAC is a key looked up in `PanelRegistry`. It is
never turned into an import path, `eval`d, `exec`d, or used to construct a
module name — an unknown id is a clean `UnknownScenarioError`, never a
dynamic import. `tests/test_scenario_selection.py` asserts that statically.
No `if panel == 1` / `elif scenario_id == ...` chain exists or is permitted;
adding a scenario is one `register()` call (or one built-in table entry),
never a new branch.

SELECTION IS PASSIVE. Building a `Scenario` constructs an in-memory object
and nothing more: it does not flash firmware, invoke arduino-cli, open a
serial port, touch hardware, start MQTT, run a shell command, begin the
experiment, or mutate any persistent evaluation data. Every `Scenario`
implementation is already a pure in-memory simulation (see
`app/scenarios/__init__.py`'s security boundary), so instantiation is
selection, not execution.

ONE SOURCE OF TRUTH PER FACT. The PACKAGE remains the authority on *which*
scenario a panel uses. This registry is the authority on *which class
implements* a given scenario id. Those are two different facts, so the
`scenario_id` string appearing as a key here is not a second copy of the
package's declaration — it is the other half of the mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Callable

from app.scenarios.base import Scenario
from app.scenarios.environmental import EnvironmentalMonitoringScenario
from app.scenarios.smart_home import SmartHomeMQTTScenario

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime coupling
    # Imported for the type hint alone. Keeping it behind TYPE_CHECKING is
    # what lets the scenario layer offer a package-aware convenience without
    # taking a runtime dependency on `app.panels` — and it preserves the
    # one-directional rule that `app.panels` imports no scenario code, which
    # `tests/test_panel_packages.py` asserts.
    from app.panels.models import PanelPackage


#: A zero-argument builder that returns a fresh, independent `Scenario`. A
#: class object satisfies this (calling it constructs an instance), which is
#: why the built-in table registers classes directly.
ScenarioFactory = Callable[[], Scenario]


class UnknownScenarioError(LookupError):
    """No `Scenario` implementation is registered for a `scenario_id`.

    A `LookupError` because that is exactly what this is — a failed lookup in
    the registry table — and because it lets a caller catch it alongside the
    `KeyError`/`IndexError` family without catching unrelated failures. The
    message always names the unresolved id so a misconfigured package is
    obvious in a log.
    """


class ScenarioRegistry:
    """Resolves `scenario_id -> Scenario`. Table-driven and side-effect-free.

    An instance is a small mutable table so a test — or a future scenario
    package shipped by a third party — can register an implementation without
    editing this module. `create()` is the only thing that builds a scenario,
    and it builds a *new* instance every call, so no two Hack sessions can
    ever share scenario state (the same isolation guarantee
    `create_default_scenario` has always provided).
    """

    def __init__(self, factories: Mapping[str, ScenarioFactory] | None = None) -> None:
        self._factories: dict[str, ScenarioFactory] = {}
        if factories:
            for scenario_id, factory in factories.items():
                self.register(scenario_id, factory)

    def register(self, scenario_id: str, factory: ScenarioFactory) -> None:
        """Bind a `scenario_id` to a factory. Rejects blanks and duplicates.

        Duplicates raise rather than silently overwrite: two implementations
        claiming one id is a configuration mistake that must fail loudly, not
        a last-writer-wins race whose outcome depends on import order.
        """
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError(f"scenario id must be a non-empty string, got {scenario_id!r}")
        if not callable(factory):
            raise ValueError(f"scenario factory for {scenario_id!r} must be callable")
        if scenario_id in self._factories:
            raise ValueError(f"scenario id already registered: {scenario_id!r}")
        self._factories[scenario_id] = factory

    def create(self, scenario_id: str) -> Scenario:
        """A fresh `Scenario` for this id, or `UnknownScenarioError`.

        Passive: this constructs an in-memory simulation object and does
        nothing else. It does not start the experiment or touch any device.
        """
        factory = self._factories.get(scenario_id)
        if factory is None:
            raise UnknownScenarioError(
                f"no scenario implementation registered for scenario id {scenario_id!r}; "
                f"registered ids are {', '.join(self.scenario_ids()) or '(none)'}"
            )
        return factory()

    def create_for_package(self, package: "PanelPackage") -> Scenario:
        """The `Scenario` a loaded `PanelPackage` declares.

        Reads only `package.scenario_id` — the package stays the source of
        truth for which scenario a panel uses, and `panel.json` is never
        re-read here. This is a thin convenience over `create`; all the
        selection logic (and the unknown-id failure) lives in one place.
        """
        return self.create(package.scenario_id)

    def scenario_ids(self) -> tuple[str, ...]:
        """Every registered id, sorted, for help/diagnostics."""
        return tuple(sorted(self._factories))

    def __contains__(self, scenario_id: object) -> bool:
        return scenario_id in self._factories


#: The scenario every fresh Hack Mode session starts in, and the one
#: `create_default_scenario` returns. Read from the class so this constant
#: and the implementation cannot disagree about the id.
DEFAULT_SCENARIO_ID = EnvironmentalMonitoringScenario.scenario_id


def build_default_scenario_registry() -> ScenarioRegistry:
    """The registry the backend ships with.

    Built at call time (like `default_panel_registry` / `build_default_registry`)
    so a test gets an independent table and cannot leak a registration into
    another test.

    Two ids resolve, each to its own dedicated implementation:

      * ``environmental-monitoring`` — the default id `create_default_scenario`
        returns, resolving to `EnvironmentalMonitoringScenario` (a simulated
        BME280 telemetry target whose vulnerability is an unauthenticated
        publish topic).
      * ``smart-home-mqtt-control`` — the id Panel 1's package declares,
        resolving to `SmartHomeMQTTScenario` (Phase 2D.5). Panel 1 is the
        Smart Home MQTT Control System: an ESP32 motor controller whose
        vulnerability is missing per-command AUTHORIZATION on an
        authenticated broker — a genuinely different security model from the
        Environmental target, which is why it is its own class rather than a
        reuse. Phase 2D.5 replaced the earlier temporary mapping (which
        pointed this id at `EnvironmentalMonitoringScenario` to prove the
        selection wiring) with this dedicated scenario; the change is exactly
        the "re-point this one entry" the wiring was built to allow — no
        selector, registry-algorithm, or generic-engine change.
    """
    registry = ScenarioRegistry()
    registry.register(DEFAULT_SCENARIO_ID, EnvironmentalMonitoringScenario)
    registry.register("smart-home-mqtt-control", SmartHomeMQTTScenario)
    return registry
