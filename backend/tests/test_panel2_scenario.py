"""Panel 2 (Environmental Monitoring System) — the NEUTRAL foundation scenario.

Panel 2's package declares `scenario_id = environmental-sensing` and nothing
implemented it, so the selector fell back to the engine's default development
scenario: the simulated MQTT/BME280 telemetry target of a different panel.
`EnvironmentalSensingScenario` (`app/scenarios/environmental_sensing.py`) is the
honest, empty target that makes Panel 2 resolve to itself.

Verified here:

A. `environmental-sensing` resolves to the neutral scenario — never to the
   legacy default — through the registry, through the real selector over the
   real Panel 2 package, and through the real `/ws/hack` lifecycle.
B. The legacy default, Panel 1's scenario and the legacy-id mapping are
   unchanged.
C. No Panel 1 / MQTT / BME280 content can reach a Panel 2 session: not through
   the class, its state, its snapshot, its command output, or its source.
D. The neutral scenario defines no objective, vulnerability, event or
   evaluation fact, and a Panel 2 session records no scenario event.
E. Mode preparation (fakes only) now READIES Hack Mode for Panel 2, which used
   to fail at `LOADING_SCENARIO` because the id resolved to nothing.

Hardware-free throughout: the board is a constructed identification or a fake
adapter, and the real event database is replaced by the autouse in-memory store.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.hardware.panel_identification import PanelIdentification, PanelIdentificationStatus
from app.hardware.panels import default_panel_registry
from app.main import app
from app.mode_preparation import SessionMode
from app.panels import (
    PanelResourceService,
    PanelResourceStatus,
    default_panel_package_loader,
)
from app.scenario_selection import ScenarioSource, SessionScenarioSelector
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    EnvironmentalMonitoringScenario,
    EnvironmentalSensingScenario,
    ScenarioRegistry,
    ScenarioState,
    SmartHomeMQTTScenario,
    UnknownScenarioError,
    build_default_scenario_registry,
    create_default_scenario,
)
from app.scenarios.base import EXIT_FAILURE, Scenario
from app.scenarios.environmental_sensing import FOUNDATION_NOTICE
from app.session_panel_guard import resume_panel_matches
from app.sessions import session_manager
from tests.test_mode_preparation import Bench

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"
MODULE = APP_DIR / "scenarios" / "environmental_sensing.py"

PANEL_TWO_ID = "environmental-monitoring"
PANEL_TWO_MAC = "20:50:0d:4d:4e:a8"
PANEL_TWO_SCENARIO = "environmental-sensing"
PANEL_ONE_ID = "smart-home-mqtt-control"
LEGACY_ID = "legacy-environmental-monitoring"

#: Vocabulary that belongs to the legacy MQTT/BME280 target or to Panel 1 (and,
#: with `spoof`/`attack`/`vulnerab`, to a security activity). None of it may
#: appear in anything a Panel 2 student can see.
FORBIDDEN = re.compile(
    r"mqtt|bme280|broker|telemetry|motor|smart.?home|spoof|attack|vulnerab|"
    r"authoriz|remediat|sensors/|192\.168\.|cybertrainer|\bstart\b|\bstop\b",
    re.IGNORECASE,
)


class _FixedIdentification:
    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


def panel_two_identification() -> PanelIdentification:
    panel = default_panel_registry().resolve(PANEL_TWO_MAC).panel
    assert panel is not None and panel.panel_id == PANEL_TWO_ID
    return PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED, port="COM3", mac=PANEL_TWO_MAC, panel=panel
    )


def panel_two_selector(registry: ScenarioRegistry | None = None) -> SessionScenarioSelector:
    return SessionScenarioSelector(
        resources=PanelResourceService(
            identification=_FixedIdentification(panel_two_identification()),
            loader=default_panel_package_loader(),
        ),
        registry=registry,
    )


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open(ws) -> str:
    frame = ws.receive_json()
    assert frame["type"] == "session"
    assert ws.receive_json()["type"] == "output"
    return frame["session_id"]


def _every_operation(scenario: Scenario):
    """One outcome from each of the five operations, with plausible arguments."""
    return {
        "extract_firmware": scenario.extract_firmware(),
        "analyze_firmware (all)": scenario.analyze_firmware(None),
        "analyze_firmware (search)": scenario.analyze_firmware("anything"),
        "scan": scenario.scan("192.168.50.1", 1883),
        "scan (no host)": scenario.scan(None, None),
        "observe": scenario.observe("192.168.50.1", 1883, "some/topic"),
        "publish": scenario.publish("192.168.50.1", 1883, "some/topic", "payload"),
    }


# =============================================================================
# A. environmental-sensing resolves to the neutral scenario, never the default
# =============================================================================


def test_the_declared_id_is_registered_to_the_neutral_scenario() -> None:
    registry = build_default_scenario_registry()

    assert PANEL_TWO_SCENARIO in registry
    scenario = registry.create(PANEL_TWO_SCENARIO)
    assert isinstance(scenario, EnvironmentalSensingScenario)
    assert scenario.scenario_id == PANEL_TWO_SCENARIO


def test_the_declared_id_does_not_resolve_to_the_legacy_scenario() -> None:
    scenario = build_default_scenario_registry().create(PANEL_TWO_SCENARIO)

    assert not isinstance(scenario, EnvironmentalMonitoringScenario)
    assert not isinstance(scenario, SmartHomeMQTTScenario)
    assert scenario.scenario_id != DEFAULT_SCENARIO_ID
    # Not a subclass relationship in either direction either: the neutral
    # target is built on nothing that carries another panel's facts.
    assert not issubclass(EnvironmentalSensingScenario, EnvironmentalMonitoringScenario)
    assert not issubclass(EnvironmentalSensingScenario, SmartHomeMQTTScenario)
    assert not issubclass(EnvironmentalMonitoringScenario, EnvironmentalSensingScenario)


def test_the_package_declares_the_id_the_registry_resolves() -> None:
    """The package is the source of truth for WHICH scenario; the registry only
    says which class implements it. They must agree for the real Panel 2."""
    package = default_panel_package_loader().load(PANEL_TWO_ID)
    scenario = build_default_scenario_registry().create_for_package(package)

    assert package.scenario_id == PANEL_TWO_SCENARIO
    assert isinstance(scenario, EnvironmentalSensingScenario)


def test_every_creation_is_a_fresh_independent_instance() -> None:
    registry = build_default_scenario_registry()
    assert registry.create(PANEL_TWO_SCENARIO) is not registry.create(PANEL_TWO_SCENARIO)


def test_the_real_selector_picks_the_neutral_scenario_for_panel_two() -> None:
    """The full MAC -> panel -> package -> scenario_id -> Scenario chain."""
    selection = panel_two_selector().select()

    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.from_panel
    assert selection.panel_status is PanelResourceStatus.READY
    assert selection.panel_id == PANEL_TWO_ID
    assert selection.scenario_id == PANEL_TWO_SCENARIO
    assert isinstance(selection.scenario, EnvironmentalSensingScenario)
    # The regression itself: this used to be the DEFAULT source with the
    # legacy scenario and a "no implementation registered" detail.
    assert selection.source is not ScenarioSource.DEFAULT
    assert not isinstance(selection.scenario, EnvironmentalMonitoringScenario)
    assert selection.detail == ""


def test_without_the_registration_the_selector_would_fall_back_to_the_legacy_default() -> None:
    """The control for the test above: it proves the registration, not the
    selector, is what changed Panel 2's outcome."""
    legacy_only = ScenarioRegistry({DEFAULT_SCENARIO_ID: EnvironmentalMonitoringScenario})
    selection = panel_two_selector(legacy_only).select()

    assert selection.source is ScenarioSource.DEFAULT
    assert isinstance(selection.scenario, EnvironmentalMonitoringScenario)


def test_a_session_over_the_websocket_runs_the_neutral_scenario(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.websocket.select_session_scenario", panel_two_selector().select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        session = session_manager._sessions[session_id]

        assert isinstance(session.scenario, EnvironmentalSensingScenario)
        assert not isinstance(session.scenario, EnvironmentalMonitoringScenario)
        assert session.scenario.scenario_id == PANEL_TWO_SCENARIO


def test_the_recorded_session_names_the_neutral_scenario(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, isolated_event_store
) -> None:
    """The evidence row says which experiment the student was actually given."""
    monkeypatch.setattr("app.websocket.select_session_scenario", panel_two_selector().select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        row = isolated_event_store.session(session_id)
        assert row is not None
        assert row.scenario_id == PANEL_TWO_SCENARIO


# =============================================================================
# B. Nothing else moved
# =============================================================================


def test_the_default_scenario_is_still_the_legacy_one() -> None:
    scenario = create_default_scenario()
    assert isinstance(scenario, EnvironmentalMonitoringScenario)
    assert scenario.scenario_id == LEGACY_ID == DEFAULT_SCENARIO_ID


def test_the_legacy_and_panel_one_ids_still_resolve_to_their_own_scenarios() -> None:
    registry = build_default_scenario_registry()
    assert type(registry.create(LEGACY_ID)) is EnvironmentalMonitoringScenario
    assert type(registry.create(PANEL_ONE_ID)) is SmartHomeMQTTScenario


def test_the_panel_id_is_still_no_scenario_id() -> None:
    """`environmental-monitoring` is Panel 2's PANEL id; only `environmental-sensing`
    is its scenario id, and the old collision stays closed."""
    with pytest.raises(UnknownScenarioError):
        build_default_scenario_registry().create(PANEL_TWO_ID)


def test_the_registry_holds_exactly_the_three_expected_ids() -> None:
    assert build_default_scenario_registry().scenario_ids() == tuple(
        sorted([LEGACY_ID, PANEL_ONE_ID, PANEL_TWO_SCENARIO])
    )


def test_the_legacy_fallback_for_no_board_is_unchanged(client: TestClient) -> None:
    """The ordinary no-hardware flow still lands on the default scenario."""
    from app.hardware import device_monitor

    device_monitor.reset()
    try:
        with client.websocket_connect("/ws/hack") as ws:
            session_id = _open(ws)
            session = session_manager._sessions[session_id]
            assert isinstance(session.scenario, EnvironmentalMonitoringScenario)
    finally:
        device_monitor.reset()


# =============================================================================
# C. No Panel 1 / MQTT / BME280 content leaks into Panel 2
# =============================================================================


def test_the_neutral_state_is_not_a_legacy_scenario_state() -> None:
    state = EnvironmentalSensingScenario().state

    assert not isinstance(state, ScenarioState)
    # Empty: no target address, topic, reading or flag exists to leak.
    assert vars(state) == {}
    assert not FORBIDDEN.search(repr(state))


def test_no_operation_leaks_legacy_or_panel_one_content() -> None:
    outcomes = _every_operation(EnvironmentalSensingScenario())

    for name, outcome in outcomes.items():
        for line in outcome.lines:
            assert not FORBIDDEN.search(line), f"{name}: {line!r}"


def test_the_snapshot_leaks_nothing_and_is_not_an_activity_shape() -> None:
    snapshot = EnvironmentalSensingScenario().snapshot()
    text = json.dumps(snapshot)

    assert not FORBIDDEN.search(text), text
    # None of the keys the other scenarios' snapshots share: they describe an
    # activity, and this scenario has none.
    assert not {"target", "discovery", "attack", "completion", "stage", "environment", "motor"} & set(
        snapshot
    )


def test_the_module_source_names_no_legacy_or_panel_one_content() -> None:
    """Every STRING LITERAL the module can emit is clean. (Docstrings and
    comments are deliberately exempt: they explain what this scenario is NOT.)"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    assert literals, "the module emits no text at all?"
    for literal in literals:
        assert not FORBIDDEN.search(literal), literal


def test_the_module_imports_nothing_that_carries_another_panels_facts() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert imported <= {
        "__future__",
        "dataclasses",
        "typing",
        "app.scenarios.base",
        "app.scenarios.events",
    }, imported


def test_the_module_executes_nothing() -> None:
    """The scenario layer's standing security boundary, re-asserted for the new
    file by structure (imports and calls), not by searching its prose."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))

    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    assert not {m.split(".")[0] for m in imported} & {"subprocess", "os", "socket", "serial", "asyncio"}

    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not called & {"eval", "exec", "system", "popen", "Popen", "run", "open", "__import__"}


# =============================================================================
# D. No objective, vulnerability, event or evaluation fact
# =============================================================================


def test_every_operation_is_an_honest_failure_with_no_event_and_no_verdict() -> None:
    scenario = EnvironmentalSensingScenario()

    for name, outcome in _every_operation(scenario).items():
        assert outcome.success is False, name
        assert outcome.exit_code == EXIT_FAILURE, name
        assert outcome.events == (), name
        # "Not assessed": a student cannot have got a required field of a
        # target that has none wrong, so the Reconnaissance-Efficiency seam is
        # left unanswered rather than claimed either way.
        assert outcome.fields_correct is None, name
        assert outcome.lines, name

    assert scenario.events == ()


def test_the_tools_say_the_panel_is_a_foundation() -> None:
    scenario = EnvironmentalSensingScenario()
    for outcome in (
        scenario.extract_firmware(),
        scenario.scan("host", 1),
        scenario.observe("host", 1, "t"),
        scenario.publish("host", 1, "t", "m"),
    ):
        assert FOUNDATION_NOTICE in " ".join(outcome.lines)


def test_analysis_without_a_capture_is_the_truthful_missing_file_answer() -> None:
    outcome = EnvironmentalSensingScenario().analyze_firmware(None)
    assert outcome.lines == ("firmware.bin: No such file or directory",)


def test_the_snapshot_declares_no_objectives_and_is_json_safe() -> None:
    snapshot = EnvironmentalSensingScenario().snapshot()

    assert snapshot["scenario_id"] == PANEL_TWO_SCENARIO
    assert snapshot["foundation"] is True
    assert snapshot["objectives"] == []
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_operations_never_change_the_snapshot() -> None:
    scenario = EnvironmentalSensingScenario()
    before = scenario.snapshot()
    _every_operation(scenario)
    assert scenario.snapshot() == before


def test_panel_two_package_still_defines_nothing_the_scenario_could_be_scored_against() -> None:
    package = default_panel_package_loader().load(PANEL_TWO_ID)

    assert package.learning.objectives == ()
    assert package.workflow == ()
    assert package.evaluation.objectives == ()
    assert package.evaluation.success_conditions == ()
    assert package.remediation is None


def test_a_panel_two_session_computes_no_score() -> None:
    """The evaluation seam: nothing is declared, so nothing is scored — a fake
    0 % or 100 % would be exactly the invented result this must not produce."""
    from app.metrics.acr import compute_acr

    package = default_panel_package_loader().load(PANEL_TWO_ID)
    result = compute_acr(package, "any-session", [])

    assert result.value is None
    assert not result.is_available


# --- through the real engine: no event, no state frame, no leak ---------------


def test_commands_in_a_panel_two_session_record_commands_but_no_scenario_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.websocket.select_session_scenario", panel_two_selector().select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        session = session_manager._sessions[session_id]

        for line in (
            "nmap 192.168.50.1",
            "mosquitto_sub -h 192.168.50.1 -t anything",
            "mosquitto_pub -h 192.168.50.1 -t anything -m hello",
            "strings firmware.bin",
            "grep anything firmware.bin",
        ):
            ws.send_json({"type": "input", "data": line + "\r"})
            frame = ws.receive_json()
            # One `output` frame and nothing after it: a command that causes no
            # scenario event sends neither an `event` nor a `state` frame.
            assert frame["type"] == "output", (line, frame)
            assert not FORBIDDEN.search(frame["data"]), (line, frame["data"])

        assert session.recorder.events == ()
        assert len(session.recorder.commands) == 5
        assert session.scenario.events == ()


def test_a_resumed_panel_two_session_replays_the_neutral_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place a `state` frame is sent unconditionally. The frontend's
    target panel reads it on every reload, so its shape has to be the neutral
    one — and it must be the session's own snapshot."""
    monkeypatch.setattr("app.websocket.select_session_scenario", panel_two_selector().select)
    # A session is bound to the panel that was attached when it opened, and a
    # resume is honoured only while that same panel is still attached
    # (`app/session_panel_guard.py`). Panel 2 stays "attached" here.
    monkeypatch.setattr(
        "app.websocket.resume_panel_matches",
        lambda expected: resume_panel_matches(
            expected, _FixedIdentification(panel_two_identification())
        ),
    )

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        expected = session_manager._sessions[session_id].scenario.snapshot()

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        assert ws.receive_json()["resumed"] is True
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "state":
                break

    assert [f["type"] for f in frames] == ["output", "state"]  # notice, then state
    assert frames[-1]["data"] == expected
    assert frames[-1]["data"]["objectives"] == []


# =============================================================================
# E. Mode preparation (fakes only)
# =============================================================================


def test_hack_preparation_for_panel_two_now_loads_its_own_scenario() -> None:
    """Hack entry used to fail at LOADING_SCENARIO for Panel 2 ("this panel's
    scenario is not available") because the id resolved to nothing."""
    bench = Bench(mac=PANEL_TWO_MAC)
    result = bench.prepare(SessionMode.HACK)

    assert result.success, result
    assert result.data["panel_id"] == PANEL_TWO_ID
    assert result.data["scenario_id"] == PANEL_TWO_SCENARIO
    assert result.data["scenario_id"] != DEFAULT_SCENARIO_ID


def test_hack_preparation_for_panel_two_compiles_and_flashes_only_its_own_sketch() -> None:
    bench = Bench(mac=PANEL_TWO_MAC)
    assert bench.prepare(SessionMode.HACK).success

    package = default_panel_package_loader().load(PANEL_TWO_ID)
    sketch = package.firmware_sketch_path
    baseline = (sketch / f"{sketch.name}.ino").read_text(encoding="utf-8")
    assert bench.compiler.sources == [baseline]
    assert [source for _port, source in bench.flasher.flashed] == [baseline]
    # Fake adapters: nothing real was uploaded anywhere.


def test_the_selector_inside_preparation_agrees_with_the_hack_connect_selector() -> None:
    """One decision, not two: preparation re-runs the same selector the
    connect path uses, so the scenario it verifies is the one a session gets."""
    resources = panel_two_selector()._resources.resolve()
    selection = SessionScenarioSelector(resources=panel_two_selector()._resources).select_for(
        resources
    )
    assert selection.scenario_id == PANEL_TWO_SCENARIO
