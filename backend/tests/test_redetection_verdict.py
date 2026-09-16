"""Phase 2D.4.2 — a board does not vanish because we are re-asking.

THE BUG THIS FILE PINS. `DeviceMonitor.refresh` publishes DETECTING over the
*previous* state rather than over a blank one:

    self._state = replace(self._state, status=DeviceStatus.DETECTING)

so a re-check deliberately keeps the port, board, MAC, panel and
`checked_at` the last completed detection established. `DeviceState.connected`
is nevertheless `status is CONNECTED`, so it reads False for that entire
window — and `identify_panel` asked exactly that question:

    if not state.connected:
        return NOT_CONNECTED

A connected, identified Panel 1 therefore reported NOT_CONNECTED for the
duration of every re-poll, even though its MAC and panel were sitting
untouched in the very state being consulted. Nothing had established that
the panel disappeared; we were merely asking again.

THE FIX. `DeviceState.board_present` reads the verdict that survives a
re-check — during DETECTING, the retained `port`, which only a CONNECTED
outcome ever sets — and `identify_panel` asks that instead of `connected`.
`connected` is unchanged and still means "usable this instant"; no field is
added, nothing is cached, and no status is renamed.

Phase 2D.4.1's cold-start behaviour is untouched and re-asserted here: with
no previous verdict at all, a DETECTING state is still NOT_CHECKED, because
"the first detection is in flight" is not "a board is present".

Coverage map: 1-2 unchecked/first-detection stay NOT_CHECKED; 3-4 a prior
verdict survives the re-detection window, identity included; 5-7 completion
replaces it — same panel, no panel, different panel; 8-9 nothing here made
selection or session creation detect; 10-11 the 2D.4.1 and Environmental
Monitoring behaviours still hold.

No sleeps and no real hardware: the in-flight window is held open by an
`asyncio.Event` inside a detector double, and released by the test.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.build.flasher import DeviceDetectOutcome, SerialDevice
from app.hardware import (
    DeviceMonitor,
    DeviceState,
    DeviceStatus,
    IdentityOutcome,
    PanelDefinition,
    PanelIdentificationService,
    PanelIdentificationStatus,
    PanelRegistry,
    default_panel_registry,
    device_monitor,
    identify_panel,
)
from app.main import app
from app.panels import PanelResourceService, PanelResourceStatus
from app.scenario_selection import ScenarioSource, select_session_scenario
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    EnvironmentalMonitoringScenario,
    ScenarioRegistry,
)
from app.sessions import session_manager
from tests.test_panel_registry import (
    ALPHA,
    BRAVO,
    MAC_ALPHA,
    MAC_BRAVO,
    MAC_UNKNOWN,
    esp32,
    make_registry,
    run,
)
from tests.test_scenario_selection import AlternateScenario

FQBN = "esp32:esp32:esp32"
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"


# --- doubles ----------------------------------------------------------------


class GatedDetector:
    """Detection that can be parked mid-flight until the test releases it.

    The deterministic stand-in for "a re-detection is in progress": while the
    detector is parked, `DeviceMonitor.refresh` has already published
    DETECTING over the previous verdict and is awaiting this result, which is
    precisely the window under test. An `asyncio.Event` hands control back and
    forth — no sleep, no timeout, no polling for a state change.
    """

    def __init__(self, *devices: SerialDevice) -> None:
        self.outcome = DeviceDetectOutcome(devices=devices)
        self.detections = 0
        self.entered: asyncio.Event | None = None
        self.gate: asyncio.Event | None = None

    def hold(self) -> None:
        """Make the NEXT detection park until `release()`."""
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    def release(self) -> None:
        assert self.gate is not None
        self.gate.set()
        self.gate = None
        self.entered = None

    def finds(self, *devices: SerialDevice) -> None:
        """What the detection currently in flight will report when released."""
        self.outcome = DeviceDetectOutcome(devices=devices)

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        self.detections += 1
        if self.entered is not None:
            self.entered.set()
        gate = self.gate
        if gate is not None:
            await gate.wait()
        return self.outcome

    async def run_flash(self, _request):  # pragma: no cover - must never run
        raise AssertionError("identification must never flash")


class PortProbe:
    """A MAC per port, so a re-detection can legitimately find another board."""

    def __init__(self, macs: dict[str, str]) -> None:
        self.macs = macs
        self.calls = 0

    async def read_mac(self, request) -> IdentityOutcome:
        self.calls += 1
        return IdentityOutcome(mac=self.macs.get(request.port))


def monitor_for(*devices: SerialDevice, macs=None, registry=None):
    """A private monitor over a gated detector — the established harness of
    `tests/test_panel_registry.py`, with the detection made holdable."""
    detector = GatedDetector(*devices)
    probe = PortProbe(macs if macs is not None else {"COM3": MAC_ALPHA})
    monitor = DeviceMonitor(
        detector=detector,
        identity_probe=probe,
        panel_registry=registry if registry is not None else make_registry(),
    )
    return PanelIdentificationService(monitor=monitor), monitor, detector, probe


async def redetecting(service, monitor, detector):
    """Drive the monitor into a real in-flight re-detection and return the
    task still running it. The caller asserts, then releases and awaits."""
    detector.hold()
    task = asyncio.create_task(monitor.refresh())
    assert detector.entered is not None
    await detector.entered.wait()
    # The monitor has published DETECTING and is awaiting the detector.
    assert monitor.snapshot().status is DeviceStatus.DETECTING
    return task


# --- 1 & 2: nothing known yet stays NOT_CHECKED (Phase 2D.4.1, unchanged) ----


def test_the_initial_state_is_still_not_checked() -> None:
    assert identify_panel(DeviceState(), make_registry()).status is (
        PanelIdentificationStatus.NOT_CHECKED
    )


def test_a_first_detection_in_flight_is_still_not_checked() -> None:
    """No previous verdict behind it, so there is nothing to preserve — and
    "we are looking for the first time" is not "a board is present"."""
    state = DeviceState(status=DeviceStatus.DETECTING)
    assert identify_panel(state, make_registry()).status is (
        PanelIdentificationStatus.NOT_CHECKED
    )
    assert state.board_present is False


def test_a_first_detection_in_flight_on_the_real_monitor_is_not_checked() -> None:
    """The same thing through the monitor, with no prior `refresh` at all."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32())
        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid

    assert run(scenario()).status is PanelIdentificationStatus.NOT_CHECKED


# --- 3 & 4: a prior verdict survives the re-detection window ----------------


def test_a_redetected_panel_is_not_reported_as_disconnected() -> None:
    """THE REGRESSION. Before the fix this asserted NOT_CONNECTED for a board
    that never went anywhere."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32())
        await service.refresh()
        assert service.identify().panel is ALPHA

        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid

    mid = run(scenario())
    assert mid.status is not PanelIdentificationStatus.NOT_CONNECTED
    assert mid.status is PanelIdentificationStatus.IDENTIFIED


def test_the_previous_identity_is_available_during_a_redetection() -> None:
    """Preserving the verdict means preserving all of it — panel, MAC, port —
    because those are the retained fields the answer is resolved from."""

    async def scenario():
        service, monitor, detector, probe = monitor_for(esp32())
        await service.refresh()
        probes_before = probe.calls

        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid, probe.calls, probes_before

    mid, probes_after, probes_before = run(scenario())
    assert mid.panel is ALPHA
    assert mid.mac == MAC_ALPHA
    assert mid.port == "COM3"
    # Preserved, not re-acquired: reading the retained state probed nothing.
    assert probes_after == probes_before


def test_the_resource_layer_preserves_the_previous_verdict_too() -> None:
    """The panel layer's answer is what the resource layer resolves, so a
    re-poll no longer flips a READY panel to NOT_CONNECTED and back.

    It stays READY rather than gaining a "provisional" member: the existing
    status model has no such state, preserving the previous verdict means
    answering exactly what we answered a moment ago, and inventing a seventh
    status here would simply move the false negative into every consumer
    that has to handle it."""

    async def scenario():
        # The real registry, so Panel 1's real package is what resolves.
        service, monitor, detector, _ = monitor_for(
            esp32(), macs={"COM3": PANEL_ONE_MAC}, registry=default_panel_registry()
        )
        resources = PanelResourceService(identification=service)
        await service.refresh()
        assert resources.resolve().status is PanelResourceStatus.READY

        task = await redetecting(service, monitor, detector)
        mid = resources.resolve()
        detector.release()
        await task
        return mid

    mid = run(scenario())
    assert mid.status is PanelResourceStatus.READY
    assert mid.package is not None
    assert mid.package.scenario_id == PANEL_ONE_ID


def test_redetecting_over_a_disconnected_verdict_stays_not_connected() -> None:
    """Preservation cuts both ways: a re-check over "nothing was there" must
    not start claiming something is."""

    async def scenario():
        service, monitor, detector, _ = monitor_for()  # detector finds nothing
        await service.refresh()
        assert monitor.snapshot().status is DeviceStatus.DISCONNECTED

        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid

    assert run(scenario()).status is PanelIdentificationStatus.NOT_CONNECTED


@pytest.mark.parametrize(
    "previous",
    [DeviceStatus.DISCONNECTED, DeviceStatus.AMBIGUOUS, DeviceStatus.ERROR],
)
def test_a_redetection_over_a_portless_verdict_is_not_present(
    previous: DeviceStatus,
) -> None:
    """Only a CONNECTED outcome ever carries a port, so re-checking over any
    other verdict retains none — which is exactly what keeps preservation
    from inventing a board. Built the way `refresh` builds it: `replace` the
    previous completed state with DETECTING."""
    from dataclasses import replace

    from app.events.clock import utc_now

    completed = DeviceState(status=previous, fqbn=FQBN, checked_at=utc_now())
    assert completed.port is None
    in_flight = replace(completed, status=DeviceStatus.DETECTING)

    assert in_flight.checked is True  # we do know what the last answer was
    assert in_flight.board_present is False  # and it was "no board"
    assert identify_panel(in_flight, make_registry()).status is (
        PanelIdentificationStatus.NOT_CONNECTED
    )


def test_an_unidentified_board_stays_unidentified_while_rechecking() -> None:
    """A board whose MAC could not be read keeps that answer too — the point
    is preserving the verdict, not upgrading it."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32(), macs={})
        await service.refresh()
        assert service.identify().status is PanelIdentificationStatus.UNIDENTIFIED

        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid

    assert run(scenario()).status is PanelIdentificationStatus.UNIDENTIFIED


def test_an_unregistered_board_stays_unregistered_while_rechecking() -> None:
    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32(), macs={"COM3": MAC_UNKNOWN})
        await service.refresh()
        assert service.identify().status is PanelIdentificationStatus.UNREGISTERED

        task = await redetecting(service, monitor, detector)
        mid = service.identify()
        detector.release()
        await task
        return mid

    mid = run(scenario())
    assert mid.status is PanelIdentificationStatus.UNREGISTERED
    assert mid.mac == MAC_UNKNOWN


# --- 5, 6 & 7: completion replaces the preserved verdict --------------------


def test_completing_with_the_same_panel_keeps_it() -> None:
    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32())
        await service.refresh()

        task = await redetecting(service, monitor, detector)
        detector.release()
        await task
        return service.identify()

    after = run(scenario())
    assert after.status is PanelIdentificationStatus.IDENTIFIED
    assert after.panel is ALPHA


def test_completing_with_no_device_becomes_not_connected() -> None:
    """CASE C — the preserved verdict is not sticky. Once the detection
    establishes that the board is gone, it is gone."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(esp32())
        await service.refresh()
        assert service.identify().panel is ALPHA

        task = await redetecting(service, monitor, detector)
        detector.finds()  # the board was unplugged
        detector.release()
        await task
        return service.identify(), monitor.snapshot()

    after, state = run(scenario())
    assert after.status is PanelIdentificationStatus.NOT_CONNECTED
    assert after.panel is None
    assert state.mac is None
    assert state.board_present is False


def test_completing_with_a_different_panel_replaces_the_identity() -> None:
    """CASE D — no stale identity survives a completed detection."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(
            esp32(), macs={"COM3": MAC_ALPHA, "COM8": MAC_BRAVO}
        )
        await service.refresh()
        assert service.identify().panel is ALPHA

        task = await redetecting(service, monitor, detector)
        detector.finds(esp32("COM8"))
        detector.release()
        await task
        return service.identify()

    after = run(scenario())
    assert after.panel is BRAVO
    assert after.mac == MAC_BRAVO
    assert after.port == "COM8"


def test_a_completed_verdict_is_never_shadowed_by_the_preserved_one() -> None:
    """Belt and braces for "do not expose stale information after the new
    detection has actually completed": drive three cycles and check that each
    settled answer is the detector's latest, not the one before it."""

    async def scenario():
        service, monitor, detector, _ = monitor_for(
            esp32(), macs={"COM3": MAC_ALPHA, "COM8": MAC_BRAVO}
        )
        seen = []
        await service.refresh()
        seen.append(service.identify().panel)

        detector.finds(esp32("COM8"))
        await monitor.refresh()
        seen.append(service.identify().panel)

        detector.finds()
        await monitor.refresh()
        seen.append(service.identify().panel)
        return seen

    assert run(scenario()) == [ALPHA, BRAVO, None]


# --- `board_present` itself -------------------------------------------------


def test_board_present_matches_connected_outside_a_detection() -> None:
    for status in (
        DeviceStatus.CONNECTED,
        DeviceStatus.DISCONNECTED,
        DeviceStatus.AMBIGUOUS,
        DeviceStatus.ERROR,
        DeviceStatus.NOT_CHECKED,
    ):
        state = DeviceState(status=status, port="COM3")
        assert state.board_present is state.connected, status


def test_board_present_is_the_only_thing_detecting_changes() -> None:
    from app.events.clock import utc_now

    detecting = DeviceState(
        status=DeviceStatus.DETECTING, port="COM3", mac=MAC_ALPHA, checked_at=utc_now()
    )
    assert detecting.connected is False  # unchanged meaning
    assert detecting.board_present is True  # the verdict that survives


def test_the_wire_snapshot_still_gained_no_field() -> None:
    """A reading of existing state, not a protocol change."""
    assert "board_present" not in DeviceState().snapshot()
    assert "checked" not in DeviceState().snapshot()


# --- 8 & 9: nothing here made selection or session creation detect ----------


@pytest.fixture
def shared_panel_one(monkeypatch: pytest.MonkeyPatch):
    """The PROCESS-WIDE monitor holding Panel 1, over a gated detector."""
    detector = GatedDetector(esp32("COM7"))
    probe = PortProbe({"COM7": PANEL_ONE_MAC})
    monkeypatch.setattr(device_monitor, "_detector", detector)
    monkeypatch.setattr(device_monitor, "_identity_probe", probe)
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    yield detector, probe
    device_monitor.reset()


@pytest.fixture
def panel_one_registry(monkeypatch: pytest.MonkeyPatch) -> ScenarioRegistry:
    registry = ScenarioRegistry(
        {
            DEFAULT_SCENARIO_ID: EnvironmentalMonitoringScenario,
            PANEL_ONE_ID: AlternateScenario,
        }
    )
    monkeypatch.setattr("app.scenario_selection.default_scenario_registry", registry)
    return registry


def test_selection_during_a_redetection_still_picks_the_panels_scenario(
    shared_panel_one, panel_one_registry
) -> None:
    """The payoff: a student connecting while a 10s header poll happens to be
    in flight gets their panel's experiment, not the default fallback."""
    detector, _ = shared_panel_one

    async def scenario():
        await device_monitor.prime()
        detector.hold()
        task = asyncio.create_task(device_monitor.refresh())
        await detector.entered.wait()
        mid = select_session_scenario()
        detections_during = detector.detections
        detector.release()
        await task
        return mid, detections_during

    selection, detections_during = run(scenario())
    assert selection.panel_status is PanelResourceStatus.READY
    assert selection.source is ScenarioSource.PANEL_PACKAGE
    assert selection.scenario_id == PANEL_ONE_ID
    assert isinstance(selection.scenario, AlternateScenario)
    # Selecting started no detection of its own — the only one running is the
    # re-detection the test itself launched.
    assert detections_during == 2


def test_selection_during_a_redetection_runs_no_detection(shared_panel_one) -> None:
    detector, probe = shared_panel_one

    async def scenario():
        await device_monitor.prime()
        detector.hold()
        task = asyncio.create_task(device_monitor.refresh())
        await detector.entered.wait()
        before = (detector.detections, probe.calls)
        for _ in range(5):
            select_session_scenario()
        after = (detector.detections, probe.calls)
        detector.release()
        await task
        return before, after

    before, after = run(scenario())
    assert before == after


def test_session_creation_during_a_redetection_runs_no_detection(
    shared_panel_one, panel_one_registry
) -> None:
    """A real `/ws/hack` connect mid-poll: the panel's scenario is selected,
    and opening the terminal detected nothing and probed nothing."""
    detector, probe = shared_panel_one
    run(device_monitor.prime())
    detections, probes = detector.detections, probe.calls

    with TestClient(app) as client:
        with client.websocket_connect("/ws/hack") as ws:
            assert ws.receive_json()["type"] == "session"
            assert ws.receive_json()["type"] == "output"
            session = next(iter(session_manager._sessions.values()))
            assert isinstance(session.scenario, AlternateScenario)
            assert session.serial.is_open is False

    assert detector.detections == detections
    assert probe.calls == probes


# --- 10 & 11: the 2D.4.1 and Environmental Monitoring behaviours hold -------


def test_an_unprimed_monitor_still_selects_the_default(shared_panel_one) -> None:
    """Phase 2D.4.1's cold case, unchanged by this phase."""
    selection = select_session_scenario()
    assert selection.panel_status is PanelResourceStatus.NOT_CHECKED
    assert selection.source is ScenarioSource.DEFAULT
    assert selection.scenario_id == DEFAULT_SCENARIO_ID


def test_no_board_still_selects_environmental_monitoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = GatedDetector()  # finds nothing
    monkeypatch.setattr(device_monitor, "_detector", detector)
    monkeypatch.setattr(device_monitor, "_identity_probe", PortProbe({}))
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    try:
        run(device_monitor.prime())
        selection = select_session_scenario()
        assert selection.panel_status is PanelResourceStatus.NOT_CONNECTED
        assert isinstance(selection.scenario, EnvironmentalMonitoringScenario)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/hack") as ws:
                assert ws.receive_json()["type"] == "session"
                assert ws.receive_json()["type"] == "output"
                ws.send_json({"type": "input", "data": "help\r"})
                assert "Available commands:" in ws.receive_json()["data"]
    finally:
        device_monitor.reset()


def test_the_registry_lookup_path_is_unchanged() -> None:
    """This phase changed which question is asked, not what happens after it:
    a fresh CONNECTED state resolves exactly as it always did."""
    registry = PanelRegistry(
        (
            PanelDefinition(
                panel_id="alpha", display_name="ALPHA", mac_addresses=(MAC_ALPHA,)
            ),
        )
    )
    state = DeviceState(status=DeviceStatus.CONNECTED, port="COM3", mac=MAC_ALPHA)
    identification = identify_panel(state, registry)
    assert identification.status is PanelIdentificationStatus.IDENTIFIED
    assert identification.panel.panel_id == "alpha"
