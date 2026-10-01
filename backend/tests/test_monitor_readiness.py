"""Monitor readiness: "we have not looked" is not "nothing is attached".

THE BUG THIS FILE PINS. Consumers read the shared monitor's cached state
instead of detecting, which is what keeps opening a Hack Mode terminal free
of `arduino-cli`. But `identify_panel` used to answer NOT_CONNECTED whenever
`state.connected` was False — and that is False both when detection found
nothing AND when no detection has ever run. So a session created before the
cache was ever filled concluded "no panel attached" about a board that was
plugged in the whole time, and scenario selection fell back to the default
scenario. Invisible today only because Panel 1's id and the default id
resolve to the same implementation; silently wrong the moment they do not.

THE FIX HAS TWO HALVES, AND BOTH ARE TESTED HERE:

1. HONESTY. `DeviceStatus.NOT_CHECKED` already distinguished "nobody looked"
   from "nothing there"; the panel layer now propagates that distinction
   instead of flattening it (`PanelIdentificationStatus.NOT_CHECKED`,
   `PanelResourceStatus.NOT_CHECKED`). The six pre-existing resource
   statuses are untouched.

2. READINESS. The application lifespan primes the monitor once at startup
   (`app/main.py` -> `DeviceMonitor.prime`), so the cache holds a real
   verdict before any consumer reads it. Detection stays entirely inside the
   monitor, owned by the app lifecycle — never a request path.

Coverage map: A cold initialization is represented correctly; B scenario
selection performs no detection; C session creation performs no detection or
probing; D a primed READY panel resolves its own package/scenario; E the
non-ready statuses keep their documented fallback; F Environmental
Monitoring behaviour is intact; G no panel-specific logic on the generic
path.

No physical hardware is involved: the shared monitor's detector and identity
probe are doubles for the duration of every test that needs a board.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import config
from app.build.flasher import DeviceDetectOutcome, SerialDevice
from app.hardware import DeviceMonitor, DeviceState, DeviceStatus, device_monitor
from app.hardware.identity import IdentityOutcome
from app.hardware.panel_identification import (
    PanelIdentificationService,
    PanelIdentificationStatus,
    identify_panel,
)
from app.hardware.panels import default_panel_registry
from app.main import app, lifespan
from app.panels import PanelResourceService, PanelResourceStatus
from app.scenario_selection import (
    ScenarioSource,
    SessionScenarioSelector,
    select_session_scenario,
)
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    EnvironmentalMonitoringScenario,
    ScenarioRegistry,
    create_default_scenario,
)
from app.sessions import session_manager
from tests.test_scenario_selection import AlternateScenario

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
FQBN = "esp32:esp32:esp32"
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"

_ESP32 = SerialDevice(
    port="COM7",
    protocol="serial",
    board_name="ESP32 Dev Module",
    board_fqbn=FQBN,
    has_usb_id=True,
)


# --- doubles ----------------------------------------------------------------


class _FakeDetector:
    """Canned detection. No subprocess, no `arduino-cli`, no board."""

    def __init__(self, outcome: DeviceDetectOutcome | None = None) -> None:
        self.outcome = outcome if outcome is not None else DeviceDetectOutcome()
        self.calls = 0

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        self.calls += 1
        return self.outcome


class _ExplodingDetector:
    """Fails the test if anything detects with it."""

    def __init__(self) -> None:
        self.calls = 0

    async def detect_devices(self, _request):  # pragma: no cover - must not run
        self.calls += 1
        raise AssertionError("a detection ran where none is allowed")


class _FakeProbe:
    """Canned MAC. No esptool, and no board is reset."""

    def __init__(self, mac: str | None = PANEL_ONE_MAC) -> None:
        self.mac = mac
        self.calls = 0

    async def read_mac(self, _request) -> IdentityOutcome:
        self.calls += 1
        return IdentityOutcome(mac=self.mac)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def shared_board(monkeypatch: pytest.MonkeyPatch):
    """Point the PROCESS-WIDE monitor at a fake Panel 1, uninitialized.

    Deliberately the shared `device_monitor` rather than a private one:
    these tests are about the real startup path and the real
    `select_session_scenario()`, both of which read that exact object. Its
    detector, probe and cached state are all restored afterwards.
    """
    detector = _FakeDetector(DeviceDetectOutcome(devices=(_ESP32,)))
    probe = _FakeProbe()
    monkeypatch.setattr(device_monitor, "_detector", detector)
    monkeypatch.setattr(device_monitor, "_identity_probe", probe)
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    yield detector, probe
    device_monitor.reset()


@pytest.fixture
def shared_empty(monkeypatch: pytest.MonkeyPatch):
    """The shared monitor over a detector that truthfully finds no board."""
    detector = _FakeDetector(DeviceDetectOutcome())
    monkeypatch.setattr(device_monitor, "_detector", detector)
    monkeypatch.setattr(device_monitor, "_identity_probe", _FakeProbe())
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    yield detector
    device_monitor.reset()


@pytest.fixture
def panel_one_registry(monkeypatch: pytest.MonkeyPatch) -> ScenarioRegistry:
    """A scenario table where Panel 1's declared id resolves to a DISTINCT
    class, so "the package chose it" is observable rather than coincidental.

    Patched into `app.scenario_selection`, which is where
    `select_session_scenario()` reads the process-wide registry from.
    """
    registry = ScenarioRegistry(
        {
            DEFAULT_SCENARIO_ID: EnvironmentalMonitoringScenario,
            PANEL_ONE_ID: AlternateScenario,
        }
    )
    monkeypatch.setattr(
        "app.scenario_selection.default_scenario_registry", registry
    )
    return registry


def _open_session(ws) -> str:
    frame = ws.receive_json()
    assert frame["type"] == "session"
    assert ws.receive_json()["type"] == "output"
    return frame["session_id"]


# --- A: cold initialization is represented correctly ------------------------


def test_a_fresh_device_state_has_not_been_checked() -> None:
    state = DeviceState()
    assert state.status is DeviceStatus.NOT_CHECKED
    assert state.checked is False
    assert state.connected is False


def test_the_first_detection_in_flight_has_not_been_checked() -> None:
    """DETECTING with nothing behind it: no earlier verdict to fall back on."""
    assert DeviceState(status=DeviceStatus.DETECTING).checked is False


def test_a_re_detection_keeps_the_previous_verdict_checked() -> None:
    """A later DETECTING carries the `checked_at` of what it is re-checking,
    so we do still know what was attached a moment ago."""
    from app.events.clock import utc_now

    assert DeviceState(status=DeviceStatus.DETECTING, checked_at=utc_now()).checked


@pytest.mark.parametrize(
    "status",
    [
        DeviceStatus.CONNECTED,
        DeviceStatus.DISCONNECTED,
        DeviceStatus.AMBIGUOUS,
        DeviceStatus.ERROR,
    ],
)
def test_a_completed_detection_counts_as_checked(status: DeviceStatus) -> None:
    assert DeviceState(status=status).checked is True


def test_an_unprimed_monitor_reports_itself_uninitialized(shared_board) -> None:
    assert device_monitor.initialized is False
    assert device_monitor.snapshot().checked is False


def test_priming_initializes_the_monitor(shared_board) -> None:
    detector, _ = shared_board

    state = run(device_monitor.prime())

    assert detector.calls == 1
    assert device_monitor.initialized is True
    assert state.connected is True
    assert state.mac == PANEL_ONE_MAC


def test_priming_twice_detects_once(shared_board) -> None:
    """Idempotent: an already-filled cache is not re-filled."""
    detector, _ = shared_board

    async def both():
        await device_monitor.prime()
        return await device_monitor.prime()

    run(both())
    assert detector.calls == 1


def test_an_unprimed_monitor_identifies_as_not_checked(shared_board) -> None:
    """The heart of the bug: a board IS attached, but nothing has looked, so
    the answer is NOT_CHECKED — never NOT_CONNECTED."""
    identification = PanelIdentificationService().identify()

    assert identification.status is PanelIdentificationStatus.NOT_CHECKED
    assert identification.status is not PanelIdentificationStatus.NOT_CONNECTED
    assert identification.panel is None


def test_an_unprimed_monitor_resolves_resources_as_not_checked(shared_board) -> None:
    resources = PanelResourceService().resolve()
    assert resources.status is PanelResourceStatus.NOT_CHECKED
    assert resources.package is None
    assert "first detection" in resources.detail


def test_not_checked_is_a_distinct_status_at_every_layer() -> None:
    assert (
        PanelIdentificationStatus.NOT_CHECKED
        is not PanelIdentificationStatus.NOT_CONNECTED
    )
    assert PanelResourceStatus.NOT_CHECKED is not PanelResourceStatus.NOT_CONNECTED
    assert PanelResourceStatus.NOT_CHECKED.value == "not_checked"


def test_the_six_documented_resource_statuses_are_preserved() -> None:
    """The fix ADDS a state; it renames and removes none."""
    for name in (
        "NOT_CONNECTED",
        "UNIDENTIFIED",
        "UNREGISTERED",
        "NO_PACKAGE",
        "PACKAGE_ERROR",
        "READY",
    ):
        assert hasattr(PanelResourceStatus, name), name
    assert PanelResourceStatus.NOT_CONNECTED.value == "not_connected"
    assert PanelResourceStatus.READY.value == "ready"


def test_identification_still_mutates_nothing(shared_board) -> None:
    state = device_monitor.snapshot()
    before = state.snapshot()
    identify_panel(state, default_panel_registry())
    assert state.snapshot() == before


def test_the_wire_snapshot_gained_no_field() -> None:
    """`checked` is a Python-side question, not a protocol change."""
    assert "checked" not in DeviceState().snapshot()


# --- B: scenario selection performs no hardware detection -------------------


def test_selection_over_an_unprimed_monitor_detects_nothing() -> None:
    detector = _ExplodingDetector()
    monitor = DeviceMonitor(detector=detector)
    selector = SessionScenarioSelector(
        resources=PanelResourceService(
            identification=PanelIdentificationService(monitor=monitor)
        )
    )

    selection = selector.select()

    assert detector.calls == 0
    assert selection.panel_status is PanelResourceStatus.NOT_CHECKED
    assert selection.source is ScenarioSource.DEFAULT


def test_selection_over_a_primed_monitor_detects_nothing_further(
    shared_board,
) -> None:
    """Selection reads the cache the monitor filled; it does not top it up."""
    detector, probe = shared_board
    run(device_monitor.prime())
    detections_after_prime = detector.calls
    probes_after_prime = probe.calls

    select_session_scenario()

    assert detector.calls == detections_after_prime
    assert probe.calls == probes_after_prime


# --- C: session creation performs no detection or probing -------------------


def test_connecting_runs_no_detection_and_no_probe(shared_board) -> None:
    """Opening a terminal must not run `arduino-cli` and must not reset the
    board — before or after the readiness fix."""
    detector, probe = shared_board
    run(device_monitor.prime())
    detections, probes = detector.calls, probe.calls

    with TestClient(app) as client:
        with client.websocket_connect("/ws/hack") as ws:
            session_id = _open_session(ws)
            session = session_manager._sessions[session_id]
            assert session.serial.is_open is False

    assert detector.calls == detections
    assert probe.calls == probes


def test_startup_detection_is_off_for_the_suite() -> None:
    """The autouse guard in conftest is what keeps every other TestClient in
    this repository from spawning a real CLI. Pin it, so removing it fails
    here rather than quietly slowing and destabilising the whole suite."""
    assert config.HARDWARE_STARTUP_DETECT is False


# --- the startup lifecycle itself -------------------------------------------


def test_the_lifespan_primes_the_monitor(
    shared_board, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real mechanism, driven deterministically: enter the real lifespan
    and await the exact task it published. No sleep, no polling, and no
    second initialization path invented for the test."""
    detector, _ = shared_board
    monkeypatch.setattr(config, "HARDWARE_STARTUP_DETECT", True)

    async def start_and_wait():
        async with lifespan(app):
            task = app.state.device_monitor_prime
            assert task is not None
            await task
            assert device_monitor.initialized is True
        return detector.calls

    assert run(start_and_wait()) == 1


def test_the_lifespan_leaves_no_task_behind(
    shared_board, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "HARDWARE_STARTUP_DETECT", True)

    async def start_and_stop():
        async with lifespan(app):
            task = app.state.device_monitor_prime
        assert task.done()

    run(start_and_stop())


def test_the_lifespan_detects_nothing_when_disabled(shared_board) -> None:
    detector, _ = shared_board

    async def start_and_stop():
        async with lifespan(app):
            assert app.state.device_monitor_prime is None

    run(start_and_stop())
    assert detector.calls == 0
    assert device_monitor.initialized is False


def test_a_failing_initial_detection_cannot_break_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`refresh()` is documented not to raise; if a bug made it raise anyway,
    the application must still start."""
    monkeypatch.setattr(config, "HARDWARE_STARTUP_DETECT", True)

    async def boom():
        raise RuntimeError("detection blew up")

    monkeypatch.setattr(device_monitor, "prime", boom)

    async def start_and_stop():
        async with lifespan(app):
            await app.state.device_monitor_prime

    run(start_and_stop())


# --- D: a primed READY panel resolves its own package and scenario ----------


def test_a_primed_panel_resolves_ready(shared_board) -> None:
    run(device_monitor.prime())

    resources = PanelResourceService().resolve()

    assert resources.status is PanelResourceStatus.READY
    assert resources.panel.panel_id == PANEL_ONE_ID
    assert resources.package.scenario_id == PANEL_ONE_ID


def test_a_primed_panel_selects_its_declared_scenario(
    shared_board, panel_one_registry
) -> None:
    run(device_monitor.prime())

    selection = select_session_scenario()

    assert selection.panel_status is PanelResourceStatus.READY
    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.scenario_id == PANEL_ONE_ID
    assert isinstance(selection.scenario, AlternateScenario)


def test_a_primed_panel_reaches_a_real_session(
    shared_board, panel_one_registry
) -> None:
    """End to end over the real `/ws/hack`: once the monitor holds a verdict,
    a connecting session runs the attached panel's own experiment."""
    run(device_monitor.prime())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/hack") as ws:
            session_id = _open_session(ws)
            session = session_manager._sessions[session_id]
            assert isinstance(session.scenario, AlternateScenario)


def test_the_same_connection_before_priming_falls_back_visibly(
    shared_board, panel_one_registry
) -> None:
    """The regression itself. Before priming, the very same board yields the
    default scenario — but the selection now SAYS so as NOT_CHECKED rather
    than claiming the board is not connected."""
    selection = select_session_scenario()

    assert selection.panel_status is PanelResourceStatus.NOT_CHECKED
    assert selection.source is ScenarioSource.DEFAULT
    assert not isinstance(selection.scenario, AlternateScenario)


# --- E: the non-ready statuses keep their documented behaviour --------------


def test_a_primed_empty_monitor_is_not_connected_not_not_checked(
    shared_empty,
) -> None:
    """Detection RAN and found nothing: that is NOT_CONNECTED, and the fix
    must not have blurred it back into "we have not looked"."""
    run(device_monitor.prime())

    selection = select_session_scenario()

    assert selection.panel_status is PanelResourceStatus.NOT_CONNECTED
    assert selection.source is ScenarioSource.DEFAULT
    assert selection.scenario_id == DEFAULT_SCENARIO_ID


def test_a_primed_unregistered_board_stays_unregistered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        device_monitor, "_detector", _FakeDetector(DeviceDetectOutcome(devices=(_ESP32,)))
    )
    monkeypatch.setattr(device_monitor, "_identity_probe", _FakeProbe("02:00:00:00:00:ff"))
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    try:
        run(device_monitor.prime())
        selection = select_session_scenario()
        assert selection.panel_status is PanelResourceStatus.UNREGISTERED
        assert selection.source is ScenarioSource.DEFAULT
    finally:
        device_monitor.reset()


def test_a_primed_unreadable_mac_stays_unidentified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        device_monitor, "_detector", _FakeDetector(DeviceDetectOutcome(devices=(_ESP32,)))
    )
    monkeypatch.setattr(device_monitor, "_identity_probe", _FakeProbe(None))
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    try:
        run(device_monitor.prime())
        selection = select_session_scenario()
        assert selection.panel_status is PanelResourceStatus.UNIDENTIFIED
        assert selection.source is ScenarioSource.DEFAULT
    finally:
        device_monitor.reset()


# --- F: Environmental Monitoring behaviour is intact ------------------------


def test_the_default_scenario_is_unchanged() -> None:
    scenario = create_default_scenario()
    assert isinstance(scenario, EnvironmentalMonitoringScenario)
    assert scenario.scenario_id == DEFAULT_SCENARIO_ID == "legacy-environmental-monitoring"


def test_the_no_hardware_flow_still_gets_environmental_monitoring(
    shared_empty,
) -> None:
    run(device_monitor.prime())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/hack") as ws:
            session_id = _open_session(ws)
            session = session_manager._sessions[session_id]
            assert isinstance(session.scenario, EnvironmentalMonitoringScenario)
            ws.send_json({"type": "input", "data": "help\r"})
            assert "Available commands:" in ws.receive_json()["data"]


def test_the_terminal_still_works_before_the_monitor_is_primed(
    shared_board,
) -> None:
    """A cold cache must not degrade the terminal in any way."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/hack") as ws:
            _open_session(ws)
            ws.send_json(
                {
                    "type": "input",
                    "data": "esptool.py read_flash 0x0 0x400000 firmware.bin\r",
                }
            )
            assert ws.receive_json()["type"] == "output"
            assert ws.receive_json()["type"] == "event"
            assert ws.receive_json()["type"] == "state"


# --- G: no panel-specific logic on the generic path -------------------------


#: The modules this change touched, minus `app/hardware/panels.py`, which is
#: the registry table and is SUPPOSED to name every panel.
_CHANGED_GENERIC_MODULES = (
    "main.py",
    "scenario_selection.py",
    "sessions.py",
    "websocket.py",
    "hardware/state.py",
    "hardware/monitor.py",
    "hardware/panel_identification.py",
    "panels/service.py",
)

_PANEL_SPECIFIC_LITERALS = frozenset(
    {panel.panel_id for panel in default_panel_registry().panels}
    | {p.package_id for p in default_panel_registry().panels if p.package_id}
    | {mac for p in default_panel_registry().panels for mac in p.mac_addresses}
) - {DEFAULT_SCENARIO_ID}


@pytest.mark.parametrize("module", _CHANGED_GENERIC_MODULES)
def test_no_panel_specific_literal_on_the_generic_path(module: str) -> None:
    tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
    offenders = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _PANEL_SPECIFIC_LITERALS
    )
    assert offenders == [], f"{module} names panel-specific values: {offenders}"


@pytest.mark.parametrize("module", _CHANGED_GENERIC_MODULES)
def test_no_execution_primitive_was_introduced(module: str) -> None:
    """Companion to the repo-wide scan: nothing this phase touched gained a
    way to spawn, evaluate, or dynamically import anything."""
    tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__"}, module
        if isinstance(func, ast.Attribute):
            assert func.attr not in {
                "system",
                "popen",
                "spawn",
                "spawnv",
                "import_module",
                "Popen",
            }, module
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ), module


def test_only_the_monitor_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural: the modules on the request path import no detection API.
    Detection lives in `app/hardware/monitor.py` and is reached from the app
    lifespan, which is not a request path."""
    for module in ("scenario_selection.py", "sessions.py", "websocket.py"):
        tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(
            name
            for name in imported
            for bad in ("subprocess", "app.build.flasher", "app.build.process", "serial")
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{module} imports {offenders}"
