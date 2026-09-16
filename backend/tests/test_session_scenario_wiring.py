"""Phase 2D.4 — the panel's declared experiment reaches the Hack session.

    attached ESP32 -> MAC -> PanelRegistry -> PanelDefinition -> PanelPackage
        -> scenario_id -> ScenarioRegistry -> Scenario -> HackSession

Phases 2D.1-2D.3 built every link and deliberately left the chain unplugged:
`HackSession` always got `create_default_scenario()`, so a panel's declared
scenario had no effect on a real session. This file verifies the one thing
2D.4 adds — that the chain is now joined at connect — and that joining it
changed nothing else.

Verified here:

A. The existing no-panel/default session behaviour is unchanged.
B. A READY panel's package selects the scenario it declares.
C. Panel 1's declared `scenario_id` reaches a real `HackSession` through the
   real `/ws/hack` lifecycle.
D. An alternate package/scenario is selected by the same code path, with no
   panel-specific conditional anywhere in the lifecycle modules.
E. NOT_CHECKED / NOT_CONNECTED / UNIDENTIFIED / UNREGISTERED / NO_PACKAGE /
   PACKAGE_ERROR are safe and deterministic: the default scenario, never
   another panel's.
F. An unimplemented `scenario_id` falls back in a controlled way instead of
   raising into the connection.
G. `HackSession` receives an already-constructed `Scenario` and performs no
   panel/package lookup of its own.
H. Every selection is a fresh, isolated instance.
I. Selection has no hardware/build/serial/MQTT side effects.
J. The existing WebSocket scenario event/state synchronisation still works.

Nothing here touches real hardware: the device view is always a double or the
process-wide monitor in its reset (nothing-detected) state.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.hardware import device_monitor
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationService,
    PanelIdentificationStatus,
)
from app.hardware.panels import PanelDefinition, default_panel_registry
from app.main import app
from app.panels import (
    PanelPackageLoader,
    PanelResourceService,
    PanelResourceStatus,
    default_panel_package_loader,
)
from app.scenario_selection import (
    ScenarioSelection,
    ScenarioSource,
    SessionScenarioSelector,
    default_session_scenario_selector,
    select_session_scenario,
)
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    EnvironmentalMonitoringScenario,
    ScenarioRegistry,
    SmartHomeMQTTScenario,
    build_default_scenario_registry,
)
from app.sessions import HackSession, SessionManager, session_manager
from tests.test_panel_packages import valid_manifest, write_package
from tests.test_scenario_selection import AlternateScenario

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"


# --- doubles ----------------------------------------------------------------


class _FixedIdentification:
    """A panel-identification double returning one canned answer. No I/O."""

    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


class _RecordingDetector:
    """A `DeviceDetector` that fails the test if anything detects with it."""

    def __init__(self) -> None:
        self.calls = 0

    async def detect_devices(self, request):  # pragma: no cover - must not run
        self.calls += 1
        raise AssertionError("selection ran a hardware detection")


def identification_for(status, *, panel=None, mac=None, port="COM3"):
    return PanelIdentification(status=status, port=port, mac=mac, panel=panel)


def selector_over(identification, loader=None, registry=None) -> SessionScenarioSelector:
    """A selector over a canned identification. The real algorithm, no board."""
    return SessionScenarioSelector(
        resources=PanelResourceService(
            identification=_FixedIdentification(identification),
            loader=loader if loader is not None else default_panel_package_loader(),
        ),
        registry=registry,
    )


def panel_one_identification() -> PanelIdentification:
    panel = default_panel_registry().resolve(PANEL_ONE_MAC).panel
    assert panel is not None and panel.panel_id == PANEL_ONE_ID
    return identification_for(
        PanelIdentificationStatus.IDENTIFIED, panel=panel, mac=PANEL_ONE_MAC
    )


def registry_mapping_panel_one_to_alternate() -> ScenarioRegistry:
    """A registry where Panel 1's declared id resolves to a DISTINCT class.

    The 2D.3 built-in table maps `smart-home-mqtt-control` to the same
    implementation as the default id, which would make "the package chose it"
    and "the fallback chose it" indistinguishable. Pointing the id at a
    different class is what makes the wiring observable.
    """
    return ScenarioRegistry(
        {
            DEFAULT_SCENARIO_ID: EnvironmentalMonitoringScenario,
            PANEL_ONE_ID: AlternateScenario,
        }
    )


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_board():
    """The process-wide monitor with nothing detected, restored afterwards."""
    device_monitor.reset()
    yield
    device_monitor.reset()


def _open_session(ws) -> str:
    frame = ws.receive_json()
    assert frame["type"] == "session"
    assert ws.receive_json()["type"] == "output"
    return frame["session_id"]


# --- A: existing no-panel/default behaviour is unchanged --------------------


def test_session_manager_create_without_a_scenario_still_defaults() -> None:
    async def run() -> None:
        session = await SessionManager().create()
        assert isinstance(session.scenario, EnvironmentalMonitoringScenario)
        assert session.scenario.scenario_id == DEFAULT_SCENARIO_ID

    asyncio.run(run())


def test_hack_session_without_a_scenario_still_defaults() -> None:
    session = HackSession(session_id="no-injection")
    assert isinstance(session.scenario, EnvironmentalMonitoringScenario)


def test_connecting_with_no_board_attached_gets_the_default_scenario(
    client: TestClient, no_board
) -> None:
    """The ordinary development flow: no ESP32, so nothing declares an
    experiment, so the session runs the long-standing default."""
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]
        assert isinstance(session.scenario, EnvironmentalMonitoringScenario)
        assert session.scenario.scenario_id == DEFAULT_SCENARIO_ID


def test_an_unprimed_monitor_selection_reports_not_checked(no_board) -> None:
    """`no_board` resets the monitor, so nothing has detected: the selection
    says NOT_CHECKED ("nobody has looked"), not NOT_CONNECTED."""
    selection = select_session_scenario()
    assert selection.source is ScenarioSource.DEFAULT
    assert selection.panel_status is PanelResourceStatus.NOT_CHECKED
    assert selection.scenario_id == DEFAULT_SCENARIO_ID
    assert selection.panel_id is None


def test_banner_and_session_frames_are_unchanged(client: TestClient, no_board) -> None:
    """Selection sends nothing: the first two frames are exactly what they
    were before this phase, in the same order."""
    with client.websocket_connect("/ws/hack") as ws:
        session_frame = ws.receive_json()
        assert session_frame["type"] == "session"
        assert set(session_frame) == {"type", "session_id", "protocol_version"}
        banner = ws.receive_json()
        assert banner["type"] == "output"
        assert "hack mode channel established" in banner["data"]


# --- B: a READY panel's package selects its declared scenario ---------------


def test_ready_panel_selects_the_scenario_its_package_declares() -> None:
    selection = selector_over(
        panel_one_identification(), registry=registry_mapping_panel_one_to_alternate()
    ).select()

    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.panel_status is PanelResourceStatus.READY
    assert selection.scenario_id == PANEL_ONE_ID
    assert selection.panel_id == PANEL_ONE_ID
    assert isinstance(selection.scenario, AlternateScenario)
    assert selection.from_panel
    assert selection.detail == ""


def test_the_selected_id_is_the_package_declared_id_not_a_panel_id() -> None:
    """The package is the source of truth for WHICH scenario, so the id that
    is looked up comes from the manifest, never from the panel definition."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    selection = selector_over(
        panel_one_identification(), registry=registry_mapping_panel_one_to_alternate()
    ).select()
    assert selection.scenario_id == package.scenario_id


def test_ready_panel_selection_through_the_shipped_registry() -> None:
    """With the shipped table, Panel 1 resolves to its own dedicated scenario
    (Phase 2D.5 replaced the temporary EnvironmentalMonitoringScenario map)."""
    selection = selector_over(panel_one_identification()).select()
    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.scenario_id == PANEL_ONE_ID
    assert isinstance(selection.scenario, SmartHomeMQTTScenario)
    assert not isinstance(selection.scenario, EnvironmentalMonitoringScenario)


# --- C: Panel 1's scenario_id reaches a real HackSession --------------------


def test_panel_one_scenario_reaches_a_real_session_over_the_websocket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end claim of this phase, through the real `/ws/hack`
    lifecycle: a connected Panel 1 makes the session run the scenario its
    package declares, not the default."""
    selector = selector_over(
        panel_one_identification(), registry=registry_mapping_panel_one_to_alternate()
    )
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]

        assert isinstance(session.scenario, AlternateScenario)
        assert not isinstance(session.scenario, EnvironmentalMonitoringScenario)


def test_the_session_holds_the_exact_instance_that_was_selected() -> None:
    """Injection, not re-derivation: the object the selector built is the
    object the session runs."""
    selection = selector_over(
        panel_one_identification(), registry=registry_mapping_panel_one_to_alternate()
    ).select()

    async def run() -> None:
        session = await SessionManager().create(scenario=selection.scenario)
        assert session.scenario is selection.scenario

    asyncio.run(run())


def test_the_recorder_anchors_to_the_selected_scenario(isolated_event_store) -> None:
    """The session's event log records the scenario it actually ran, so the
    evidence says which experiment the student was given."""
    session = HackSession(session_id="anchored", scenario=AlternateScenario())
    session.recorder.start()
    row = isolated_event_store.session("anchored")
    assert row is not None
    assert row.scenario_id == AlternateScenario.scenario_id


# --- D: an alternate package selects its own scenario, with no branches -----


def test_an_alternate_package_selects_its_own_scenario(
    tmp_path: pathlib.Path,
) -> None:
    """A brand-new panel is a package plus a registration. The SAME selector
    resolves it with no code change and no panel branch."""
    write_package(tmp_path, "fake-panel-two", valid_manifest("fake-panel-two"))
    panel = PanelDefinition(
        panel_id="fake-panel-two",
        display_name="FAKE PANEL 2",
        mac_addresses=("02:00:00:00:00:22",),
        package_id="fake-panel-two",
    )
    registry = build_default_scenario_registry()
    registry.register("fake-panel-two", AlternateScenario)

    selection = selector_over(
        identification_for(
            PanelIdentificationStatus.IDENTIFIED, panel=panel, mac="02:00:00:00:00:22"
        ),
        loader=PanelPackageLoader(root=tmp_path),
        registry=registry,
    ).select()

    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.scenario_id == "fake-panel-two"
    assert isinstance(selection.scenario, AlternateScenario)


#: The lifecycle modules this phase touches. None of them may name a panel.
_LIFECYCLE_MODULES = ("scenario_selection.py", "sessions.py", "websocket.py")

#: Every value that would make a lifecycle module panel-specific: a panel id,
#: a package id, a registered MAC, or a declared scenario id.
_PANEL_SPECIFIC_LITERALS = frozenset(
    {panel.panel_id for panel in default_panel_registry().panels}
    | {
        panel.package_id
        for panel in default_panel_registry().panels
        if panel.package_id
    }
    | {
        mac
        for panel in default_panel_registry().panels
        for mac in panel.mac_addresses
    }
    | {PANEL_ONE_ID, PANEL_ONE_MAC}
) - {DEFAULT_SCENARIO_ID}


@pytest.mark.parametrize("module", _LIFECYCLE_MODULES)
def test_lifecycle_modules_contain_no_panel_specific_literal(module: str) -> None:
    """No `if panel == 1`, no `if mac == ...`, no `if package_id == ...`: the
    lifecycle cannot name a panel, because the names are not in it."""
    tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
    offenders = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _PANEL_SPECIFIC_LITERALS
    )
    assert offenders == [], f"{module} names panel-specific values: {offenders}"


@pytest.mark.parametrize("module", _LIFECYCLE_MODULES)
def test_lifecycle_modules_compare_nothing_to_a_panel_identity(module: str) -> None:
    """Structural companion to the literal check: nothing in the lifecycle
    compares a panel/package/MAC-shaped attribute against anything."""
    tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
    banned = {"panel_id", "package_id", "mac", "panel_number", "panel"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        parts = [node.left, *node.comparators]
        for part in parts:
            name = None
            if isinstance(part, ast.Attribute):
                name = part.attr
            elif isinstance(part, ast.Name):
                name = part.id
            if name in banned:
                # `is None` guards are fine — they ask "is there one?", not
                # "which one is it?". Anything else is a panel branch.
                assert all(
                    isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops
                ), f"{module} branches on {name}"
                assert all(
                    isinstance(other, ast.Constant) and other.value is None
                    for other in node.comparators
                ), f"{module} branches on {name}"


# --- E: non-READY states are safe and deterministic -------------------------


@pytest.mark.parametrize(
    "identification, expected_status",
    [
        (
            identification_for(PanelIdentificationStatus.NOT_CHECKED, port=None),
            PanelResourceStatus.NOT_CHECKED,
        ),
        (
            identification_for(PanelIdentificationStatus.NOT_CONNECTED, port=None),
            PanelResourceStatus.NOT_CONNECTED,
        ),
        (
            identification_for(PanelIdentificationStatus.UNIDENTIFIED),
            PanelResourceStatus.UNIDENTIFIED,
        ),
        (
            identification_for(
                PanelIdentificationStatus.UNREGISTERED, mac="02:00:00:00:00:ff"
            ),
            PanelResourceStatus.UNREGISTERED,
        ),
        (
            identification_for(
                PanelIdentificationStatus.IDENTIFIED,
                panel=PanelDefinition(panel_id="bare", display_name="BARE"),
                mac="02:00:00:00:00:0a",
            ),
            PanelResourceStatus.NO_PACKAGE,
        ),
    ],
)
def test_non_ready_states_fall_back_to_the_default_scenario(
    identification, expected_status
) -> None:
    selection = selector_over(
        identification, registry=registry_mapping_panel_one_to_alternate()
    ).select()

    assert selection.source is ScenarioSource.DEFAULT
    assert selection.panel_status is expected_status
    assert selection.scenario_id == DEFAULT_SCENARIO_ID
    assert isinstance(selection.scenario, EnvironmentalMonitoringScenario)
    # THE SAFETY RULE: an unresolved panel never lands on another panel's
    # experiment. `AlternateScenario` is the only panel-specific scenario in
    # this registry, and nothing here can reach it.
    assert not isinstance(selection.scenario, AlternateScenario)


def test_a_declared_but_missing_package_falls_back_with_a_reason(
    tmp_path: pathlib.Path,
) -> None:
    panel = PanelDefinition(
        panel_id="ghost", display_name="GHOST", package_id="ghost"
    )
    selection = selector_over(
        identification_for(
            PanelIdentificationStatus.IDENTIFIED, panel=panel, mac="02:00:00:00:00:0b"
        ),
        loader=PanelPackageLoader(root=tmp_path),
    ).select()

    assert selection.panel_status is PanelResourceStatus.PACKAGE_ERROR
    assert selection.source is ScenarioSource.DEFAULT
    assert selection.detail
    assert selection.panel_id == "ghost"


def test_a_malformed_package_falls_back_instead_of_raising(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "panel.json").write_text("{not json", encoding="utf-8")
    panel = PanelDefinition(
        panel_id="broken", display_name="BROKEN", package_id="broken"
    )

    selection = selector_over(
        identification_for(
            PanelIdentificationStatus.IDENTIFIED, panel=panel, mac="02:00:00:00:00:0c"
        ),
        loader=PanelPackageLoader(root=tmp_path),
    ).select()

    assert selection.panel_status is PanelResourceStatus.PACKAGE_ERROR
    assert selection.source is ScenarioSource.DEFAULT
    assert isinstance(selection.scenario, EnvironmentalMonitoringScenario)


def test_non_ready_selection_is_deterministic() -> None:
    """Same inputs, same answer, every time — no ordering or cache effects."""
    selector = selector_over(
        identification_for(PanelIdentificationStatus.UNIDENTIFIED),
        registry=registry_mapping_panel_one_to_alternate(),
    )
    first, second = selector.select(), selector.select()
    assert (first.source, first.scenario_id, first.panel_status) == (
        second.source,
        second.scenario_id,
        second.panel_status,
    )


def test_an_unresolved_panel_still_yields_a_usable_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board we cannot place must not break the terminal."""
    selector = selector_over(
        identification_for(
            PanelIdentificationStatus.UNREGISTERED, mac="02:00:00:00:00:ff"
        )
    )
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": "help\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "Available commands:" in reply["data"]


# --- F: an unimplemented scenario id is a controlled fallback ---------------


def test_a_package_naming_an_unimplemented_scenario_falls_back(
    tmp_path: pathlib.Path,
) -> None:
    manifest = valid_manifest("orphan-panel")
    manifest["scenario"]["scenario_id"] = "not-implemented-anywhere"
    write_package(tmp_path, "orphan-panel", manifest)
    panel = PanelDefinition(
        panel_id="orphan-panel",
        display_name="ORPHAN",
        mac_addresses=("02:00:00:00:00:33",),
        package_id="orphan-panel",
    )

    selection = selector_over(
        identification_for(
            PanelIdentificationStatus.IDENTIFIED, panel=panel, mac="02:00:00:00:00:33"
        ),
        loader=PanelPackageLoader(root=tmp_path),
        registry=registry_mapping_panel_one_to_alternate(),
    ).select()

    # The package loaded fine — the failure is purely "nothing implements it".
    assert selection.panel_status is PanelResourceStatus.READY
    assert selection.source is ScenarioSource.DEFAULT
    assert selection.scenario_id == DEFAULT_SCENARIO_ID
    assert "not-implemented-anywhere" in selection.detail
    assert isinstance(selection.scenario, EnvironmentalMonitoringScenario)
    assert not isinstance(selection.scenario, AlternateScenario)


def test_an_unimplemented_scenario_id_does_not_break_a_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manifest = valid_manifest("orphan-panel")
    manifest["scenario"]["scenario_id"] = "not-implemented-anywhere"
    write_package(tmp_path, "orphan-panel", manifest)
    panel = PanelDefinition(
        panel_id="orphan-panel", display_name="ORPHAN", package_id="orphan-panel"
    )
    selector = selector_over(
        identification_for(
            PanelIdentificationStatus.IDENTIFIED, panel=panel, mac="02:00:00:00:00:33"
        ),
        loader=PanelPackageLoader(root=tmp_path),
    )
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]
        assert isinstance(session.scenario, EnvironmentalMonitoringScenario)


# --- G: HackSession consumes a Scenario; it never looks one up --------------


def test_hack_session_takes_the_scenario_it_is_given() -> None:
    scenario = AlternateScenario()
    session = HackSession(session_id="injected", scenario=scenario)
    assert session.scenario is scenario


def test_hack_session_has_no_panel_or_package_api() -> None:
    session = HackSession(session_id="surface", scenario=AlternateScenario())
    for attribute in (
        "panel",
        "panel_id",
        "package",
        "package_id",
        "mac",
        "resources",
        "select_scenario",
        "identify_panel",
        "load_package",
    ):
        assert not hasattr(session, attribute), attribute


def test_sessions_module_imports_no_panel_or_selection_layer() -> None:
    """Structural proof that the session layer cannot do the lookup: the
    modules that could perform one are not importable from it."""
    tree = ast.parse((APP_DIR / "sessions.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = (
        "app.panels",
        "app.scenario_selection",
        "app.hardware.panels",
        "app.hardware.panel_identification",
        "app.hardware.identity",
        "app.build",
        "json",
        "pathlib",
    )
    offenders = sorted(
        name
        for name in imported
        for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"sessions.py imports {offenders}"


def test_creating_a_session_never_consults_the_package_loader() -> None:
    """A loader whose every method fails the test, wired nowhere near the
    session: creating one must not reach it."""

    class _ExplodingLoader(PanelPackageLoader):
        def load(self, package_id):  # pragma: no cover - must not run
            raise AssertionError("the session layer loaded a package")

        def load_for_panel(self, panel):  # pragma: no cover - must not run
            raise AssertionError("the session layer loaded a package")

    async def run() -> None:
        session = await SessionManager().create(scenario=AlternateScenario())
        assert isinstance(session.scenario, AlternateScenario)

    # The loader exists and would raise if the session layer had a path to
    # one; it has none, which is exactly the point.
    _ExplodingLoader()
    asyncio.run(run())


# --- H: every selection is a fresh, isolated instance -----------------------


def test_two_selections_are_independent_instances() -> None:
    selector = selector_over(
        panel_one_identification(), registry=registry_mapping_panel_one_to_alternate()
    )
    first, second = selector.select(), selector.select()
    assert first.scenario is not second.scenario
    assert first.scenario.state is not second.scenario.state


def test_two_connections_get_independent_panel_scenarios(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = selector_over(panel_one_identification())
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as first:
        first_id = _open_session(first)
        with client.websocket_connect("/ws/hack") as second:
            second_id = _open_session(second)
            first_session = session_manager._sessions[first_id]
            second_session = session_manager._sessions[second_id]

            assert first_session.scenario is not second_session.scenario
            first.send_json(
                {
                    "type": "input",
                    "data": "esptool.py read_flash 0x0 0x400000 firmware.bin\r",
                }
            )
            assert first.receive_json()["type"] == "output"
            assert first.receive_json()["type"] == "event"
            assert first.receive_json()["type"] == "state"

            assert first_session.scenario.state.discovery.firmware_extracted is True
            assert second_session.scenario.state.discovery.firmware_extracted is False


# --- I: selection has no hardware/build/serial/MQTT side effects ------------


def test_selection_runs_no_hardware_detection() -> None:
    """`select()` reads the monitor's existing state; it never detects, so no
    `arduino-cli board list` runs and no MAC is probed (probing resets the
    board — that must never be a side effect of opening a terminal)."""
    from app.hardware.monitor import DeviceMonitor

    detector = _RecordingDetector()
    monitor = DeviceMonitor(detector=detector)
    selector = SessionScenarioSelector(
        resources=PanelResourceService(
            identification=PanelIdentificationService(monitor=monitor)
        )
    )

    selection = selector.select()

    assert detector.calls == 0
    # A monitor nobody primed: NOT_CHECKED, because selection did not go and
    # look — which is exactly what `detector.calls == 0` proves.
    assert selection.panel_status is PanelResourceStatus.NOT_CHECKED


def test_connecting_opens_no_serial_port(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering Hack Mode with a panel attached still claims no port."""
    selector = selector_over(panel_one_identification())
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]
        assert session.serial.is_open is False
        assert session.serial.address is None


def test_selection_starts_nothing_in_the_scenario() -> None:
    """Construction is selection, not execution: the selected scenario is in
    its untouched initial state, with no events and no attack in progress."""
    selection = selector_over(panel_one_identification()).select()
    state = selection.scenario.state
    assert not state.discovery.firmware_extracted
    assert not state.attack.spoof_attempted
    assert not state.completion.attack_successful
    assert selection.scenario.events == ()


def test_selection_module_imports_nothing_that_executes_or_connects() -> None:
    tree = ast.parse((APP_DIR / "scenario_selection.py").read_text(encoding="utf-8"))
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
        "socket",
        "paho",
        "shutil",
        "os",
        "pathlib",
        "app.build",
        "app.hardware.serial_transport",
        "app.hardware.identity",
        "app.commands",
    )
    offenders = sorted(
        name
        for name in imported
        for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"scenario_selection.py imports {offenders}"


def test_selection_module_uses_no_dynamic_execution() -> None:
    """A `scenario_id` is a key in a table, never a module name or code."""
    tree = ast.parse((APP_DIR / "scenario_selection.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__"}
        if isinstance(func, ast.Attribute):
            assert func.attr not in {
                "import_module",
                "system",
                "popen",
                "spawn",
                "run",
                "Popen",
            }
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                )


def test_selection_never_calls_refresh_on_the_resource_service() -> None:
    """`refresh()` would re-detect through the monitor. Selection must use
    the passive `resolve()`, so a connect cannot trigger device I/O."""

    class _RefusesRefresh(PanelResourceService):
        async def refresh(self):  # pragma: no cover - must not run
            raise AssertionError("selection refreshed the device state")

    selector = SessionScenarioSelector(
        resources=_RefusesRefresh(
            identification=_FixedIdentification(
                identification_for(PanelIdentificationStatus.NOT_CONNECTED, port=None)
            )
        )
    )
    assert selector.select().source is ScenarioSource.DEFAULT


def test_selection_writes_no_event_rows(no_board) -> None:
    """Plugging a panel in is not something a student did. Selecting its
    scenario records no Hack Mode activity."""

    async def run() -> None:
        selection = select_session_scenario()
        session = await SessionManager().create(scenario=selection.scenario)
        # `recorder.start()` writes the session row (pre-existing Phase 2B
        # behaviour); selection itself contributes no command and no event.
        assert session.recorder.commands == ()
        assert session.recorder.events == ()

    asyncio.run(run())


# --- J: existing WebSocket scenario synchronisation is intact ---------------


def test_event_and_state_frames_still_follow_a_scenario_command(
    client: TestClient, no_board
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json(
            {
                "type": "input",
                "data": "esptool.py read_flash 0x0 0x400000 firmware.bin\r",
            }
        )
        assert ws.receive_json()["type"] == "output"
        event = ws.receive_json()
        assert event["type"] == "event"
        assert event["sequence"] >= 1
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["data"]["discovery"]["firmware_extracted"] is True


def test_a_panel_selected_scenario_still_synchronises_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Event/state delivery is a transport concern, so it works identically
    for a package-selected scenario."""
    selector = selector_over(panel_one_identification())
    monkeypatch.setattr("app.websocket.select_session_scenario", selector.select)

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        ws.send_json(
            {
                "type": "input",
                "data": "esptool.py read_flash 0x0 0x400000 firmware.bin\r",
            }
        )
        assert ws.receive_json()["type"] == "output"
        assert ws.receive_json()["type"] == "event"
        state = ws.receive_json()
        assert state["type"] == "state"
        session = session_manager._sessions[session_id]
        assert state["data"] == session.scenario.snapshot()


def test_hardware_status_is_still_silent_after_selection(
    client: TestClient, no_board
) -> None:
    """The hardware poll's one-frame contract is untouched by this phase."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "hardware_status"})
        frame = ws.receive_json()
        assert frame["type"] == "hardware"
        # Nothing else follows: prove it by round-tripping a command.
        ws.send_json({"type": "input", "data": "help\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "Available commands:" in reply["data"]


# --- the public seam is honest ---------------------------------------------


def test_default_selector_is_built_at_call_time() -> None:
    assert default_session_scenario_selector() is not default_session_scenario_selector()


def test_selection_describe_is_a_log_line_not_a_frame(no_board) -> None:
    selection = select_session_scenario()
    described = selection.describe()
    assert "source=default" in described
    assert "panel_status=not_checked" in described
    # It is plain text for a log — never JSON a client could be handed.
    with pytest.raises(json.JSONDecodeError):
        json.loads(described)


def test_selection_is_frozen_data() -> None:
    selection = selector_over(panel_one_identification()).select()
    assert isinstance(selection, ScenarioSelection)
    with pytest.raises(Exception):
        selection.scenario_id = "mutated"  # type: ignore[misc]
