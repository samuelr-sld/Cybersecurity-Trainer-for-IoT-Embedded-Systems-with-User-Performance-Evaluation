"""Phase 2D.3 — scenario selection: `scenario_id` -> `Scenario` implementation.

    PanelPackage.scenario_id -> ScenarioRegistry.create -> a fresh Scenario

Verified here:

A. The default seam is preserved: `create_default_scenario()` still returns a
   fresh `EnvironmentalMonitoringScenario`, and now resolves through the
   registry.
B. Panel 1's declared `scenario_id` resolves to an existing implementation.
C. An unknown `scenario_id` fails cleanly, naming the unresolved id.
D. Extensibility: an alternate scenario with a new id resolves through the
   SAME registry algorithm, with no change to the selector's code.
E. Selection is passive and data-driven — no dynamic import, eval/exec, and
   no subprocess/serial/build side effects; the package stays the one source
   of truth for which scenario a panel declares.

Nothing here starts an experiment, touches hardware, or provisions firmware:
building a `Scenario` constructs an in-memory simulation and nothing else.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.panels import default_panel_package_loader
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    EnvironmentalMonitoringScenario,
    SmartHomeMQTTScenario,
    Scenario,
    ScenarioRegistry,
    UnknownScenarioError,
    build_default_scenario_registry,
    create_default_scenario,
    default_scenario_registry,
)
from app.scenarios.base import ScenarioOutcome
from app.scenarios.state import ScenarioState

SCENARIOS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "scenarios"
PANEL_ONE_PACKAGE_ID = "smart-home-mqtt-control"


# --- A: default behaviour preserved -----------------------------------------


def test_create_default_scenario_still_returns_environmental() -> None:
    scenario = create_default_scenario()
    assert isinstance(scenario, EnvironmentalMonitoringScenario)
    assert scenario.scenario_id == DEFAULT_SCENARIO_ID == "legacy-environmental-monitoring"


def test_create_default_scenario_returns_a_fresh_instance_each_call() -> None:
    # Per-session isolation: no two sessions share scenario state.
    first = create_default_scenario()
    second = create_default_scenario()
    assert first is not second
    assert first.state is not second.state


def test_default_id_resolves_through_the_shared_registry() -> None:
    assert DEFAULT_SCENARIO_ID in default_scenario_registry
    resolved = default_scenario_registry.create(DEFAULT_SCENARIO_ID)
    assert isinstance(resolved, EnvironmentalMonitoringScenario)


def test_environmental_monitoring_id_resolves_to_no_scenario() -> None:
    """`environmental-monitoring` is Panel 2's PANEL id, not a scenario id.

    The legacy MQTT/BME280 default used to answer to it; it was renamed
    (`legacy-environmental-monitoring`) so the id is free for Panel 2 and can
    never silently resolve to that simulation.
    """
    assert "environmental-monitoring" not in default_scenario_registry
    assert "environmental-monitoring" not in default_scenario_registry.scenario_ids()
    with pytest.raises(UnknownScenarioError):
        default_scenario_registry.create("environmental-monitoring")


# --- B: Panel 1 resolves ----------------------------------------------------


def test_panel_one_declared_scenario_id_resolves_to_an_implementation() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_PACKAGE_ID)

    scenario = default_scenario_registry.create_for_package(package)

    assert isinstance(scenario, Scenario)
    # Since Phase 2D.5 Panel 1 has its own dedicated scenario; the earlier
    # temporary mapping to EnvironmentalMonitoringScenario is gone.
    assert isinstance(scenario, SmartHomeMQTTScenario)
    assert not isinstance(scenario, EnvironmentalMonitoringScenario)


def test_create_for_package_reads_only_the_declared_scenario_id() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_PACKAGE_ID)
    # create_for_package must delegate to create(scenario_id); prove it by
    # showing the id it uses is exactly the package's declared one.
    assert package.scenario_id in default_scenario_registry
    direct = default_scenario_registry.create(package.scenario_id)
    via_package = default_scenario_registry.create_for_package(package)
    assert type(direct) is type(via_package)


# --- C: unknown scenario id -------------------------------------------------


def test_unknown_scenario_id_raises_a_clear_error() -> None:
    with pytest.raises(UnknownScenarioError) as excinfo:
        default_scenario_registry.create("no-such-scenario")
    assert "no-such-scenario" in str(excinfo.value)


def test_unknown_scenario_error_is_a_lookup_error() -> None:
    # Catchable alongside the KeyError/IndexError family, and unmistakably a
    # failed lookup rather than a crash.
    assert issubclass(UnknownScenarioError, LookupError)


def test_create_for_package_surfaces_unknown_ids() -> None:
    class _Pkg:
        scenario_id = "totally-unregistered"

    with pytest.raises(UnknownScenarioError):
        default_scenario_registry.create_for_package(_Pkg())  # type: ignore[arg-type]


# --- D: extensibility -------------------------------------------------------


class AlternateScenario(Scenario):
    """A minimal second `Scenario`, used only to prove the selector is not
    tied to any one implementation. It has no attack behaviour of its own."""

    scenario_id = "alternate-test-scenario"

    def __init__(self) -> None:
        self._state = ScenarioState()

    @property
    def state(self) -> ScenarioState:
        return self._state

    @property
    def events(self):
        return ()

    def extract_firmware(self) -> ScenarioOutcome:
        return ScenarioOutcome.ok()

    def analyze_firmware(self, search: str | None = None) -> ScenarioOutcome:
        return ScenarioOutcome.ok()

    def scan(self, host, port) -> ScenarioOutcome:
        return ScenarioOutcome.ok()

    def observe(self, host, port, topic) -> ScenarioOutcome:
        return ScenarioOutcome.ok()

    def publish(self, host, port, topic, message) -> ScenarioOutcome:
        return ScenarioOutcome.ok()

    def snapshot(self) -> dict:
        return {"scenario_id": self.scenario_id}


def test_an_alternate_scenario_resolves_through_the_same_algorithm() -> None:
    """A new scenario is a registration, not a code change: the SAME
    `ScenarioRegistry.create` resolves it with no selector edit."""
    registry = build_default_scenario_registry()
    registry.register(AlternateScenario.scenario_id, AlternateScenario)

    resolved = registry.create(AlternateScenario.scenario_id)

    assert isinstance(resolved, AlternateScenario)
    # The built-ins still resolve unchanged alongside it.
    assert isinstance(registry.create(DEFAULT_SCENARIO_ID), EnvironmentalMonitoringScenario)


def test_a_fresh_registry_can_be_built_from_only_an_alternate() -> None:
    registry = ScenarioRegistry({AlternateScenario.scenario_id: AlternateScenario})
    assert registry.scenario_ids() == (AlternateScenario.scenario_id,)
    assert isinstance(registry.create(AlternateScenario.scenario_id), AlternateScenario)


def test_registering_a_duplicate_id_is_rejected() -> None:
    registry = build_default_scenario_registry()
    with pytest.raises(ValueError):
        registry.register(DEFAULT_SCENARIO_ID, AlternateScenario)


@pytest.mark.parametrize("bad_id", ["", "   ", None])
def test_registering_a_blank_id_is_rejected(bad_id) -> None:
    with pytest.raises(ValueError):
        ScenarioRegistry().register(bad_id, AlternateScenario)


def test_registering_a_non_callable_factory_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScenarioRegistry().register("x-scenario", object())  # type: ignore[arg-type]


def test_a_fresh_default_registry_does_not_leak_registrations() -> None:
    """Each `build_default_scenario_registry()` is independent, so a test
    registration cannot bleed into another test or the shared registry."""
    one = build_default_scenario_registry()
    one.register(AlternateScenario.scenario_id, AlternateScenario)
    two = build_default_scenario_registry()
    assert AlternateScenario.scenario_id in one
    assert AlternateScenario.scenario_id not in two
    assert AlternateScenario.scenario_id not in default_scenario_registry


# --- E: selection is passive and data-driven --------------------------------


def test_selection_does_no_dynamic_import_or_dynamic_execution() -> None:
    """The registry resolves ids from a table — never by importing a module
    named after the id, and never via eval/exec/compile/__import__."""
    source = (SCENARIOS_DIR / "registry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {
                    "eval",
                    "exec",
                    "compile",
                    "__import__",
                }, "registry.py uses dynamic execution"
            if isinstance(func, ast.Attribute):
                assert func.attr not in {
                    "import_module",
                    "system",
                    "popen",
                    "spawn",
                }, f"registry.py calls {func.attr}"


def test_registry_imports_nothing_that_executes_or_flashes() -> None:
    tree = ast.parse((SCENARIOS_DIR / "registry.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = (
        "subprocess",
        "importlib",
        "serial",
        "app.build",
        "app.hardware.serial_transport",
        "app.commands",
    )
    offenders = sorted(
        name
        for name in imported
        for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"registry.py imports {offenders}"


def test_building_a_scenario_has_no_side_effects_on_state() -> None:
    """Instantiation is selection, not execution: a freshly selected scenario
    is in its untouched initial state — no discovery, no attack, no impact."""
    scenario = default_scenario_registry.create(DEFAULT_SCENARIO_ID)
    state = scenario.state
    assert not state.discovery.firmware_extracted
    assert not state.attack.spoof_attempted
    assert not state.completion.attack_successful
    assert scenario.events == ()
