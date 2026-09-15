"""Phase 1 verification: Hack Mode consuming the shared device state.

Two things are under test here, and the second matters more than the first:

1. Hack Mode reports the SAME physical ESP32 Build Mode reports, from the
   same shared `device_monitor` — not a second detection path, and not a
   device Hack Mode owns.

2. Hardware polling is COMPLETELY SILENT. Device presence is infrastructure
   state, not something a student did, so a poll must not print to the
   terminal, create a scenario event, change scenario state, or look like a
   command. That is asserted frame-by-frame below rather than trusted to a
   comment, because it is the requirement most easily broken by a later
   "helpful" change that echoes "ESP32 CONNECTED" into xterm.js.

No subprocess is spawned and no physical board is required: the shared
monitor's detector is replaced with a double for the duration of each test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.build.flasher import DeviceDetectOutcome, FlashFailureCategory, SerialDevice
from app.build.service import default_service
from app.hardware import (
    DeviceMonitor,
    DeviceStatus,
    IdentityFailure,
    IdentityOutcome,
    NullIdentityProbe,
    device_monitor,
)
from app.main import app
from app.models.messages import PROTOCOL_VERSION
from app.sessions import session_manager

FQBN = "esp32:esp32:esp32"

_ESP32 = SerialDevice(
    port="COM7",
    protocol="serial",
    board_name="ESP32 Dev Module",
    board_fqbn=FQBN,
    has_usb_id=True,
)


class _FakeDetector:
    """Canned detection, no subprocess, no board. Mutable between calls."""

    def __init__(self, outcome: DeviceDetectOutcome | None = None) -> None:
        self.outcome = outcome if outcome is not None else DeviceDetectOutcome()
        self.calls = 0

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        self.calls += 1
        return self.outcome


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


#: The MAC of the ESP32 on this project's development machine, and the panel
#: it is mapped to in `app/hardware/panels.py`. Used by the identity double
#: below so these tests exercise the *real* mapping table end to end rather
#: than a value invented here.
_REAL_MAC = "20:9b:a9:88:0b:e4"
_REAL_PANEL = "SMART HOME MQTT CONTROL SYSTEM"


class _FakeIdentityProbe:
    """Canned MAC, no esptool, no board reset. Counts its invocations.

    The count is what proves the per-port cache works: a board must be
    probed once when it appears, not once per poll — see
    `test_polling_does_not_re_probe_the_same_board`.
    """

    def __init__(self, mac: str | None = _REAL_MAC) -> None:
        self.mac = mac
        self.calls = 0

    async def read_mac(self, request) -> IdentityOutcome:
        self.calls += 1
        if self.mac is None:
            return IdentityOutcome(category=IdentityFailure.UNREACHABLE)
        return IdentityOutcome(mac=self.mac)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeIdentityProbe:
    """Stop the SHARED monitor from driving real esptool during tests.

    Without this the monitor would run a genuine `esptool read_mac` against
    whatever port the detector double named — probing a port that does not
    exist, or worse, resetting a real ESP32 that happens to be plugged into
    the machine running the suite. Tests must never touch physical hardware.
    """
    fake = _FakeIdentityProbe()
    monkeypatch.setattr(device_monitor, "_identity_probe", fake)
    return fake


@pytest.fixture
def detector(monkeypatch: pytest.MonkeyPatch, probe: _FakeIdentityProbe) -> _FakeDetector:
    """Point the SHARED monitor at a fake board for one test.

    Deliberately the process-wide `device_monitor` itself rather than a
    private one: these tests are about both modes reading the *shared*
    state, so swapping in a different monitor would test the wrong object.
    Its detector, identity probe and cached state are all restored
    afterwards, so one test's board cannot leak into the next through the
    freshness cache or the per-port MAC cache.
    """
    fake = _FakeDetector(DeviceDetectOutcome(devices=(_ESP32,)))
    monkeypatch.setattr(device_monitor, "_detector", fake)
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    yield fake
    device_monitor.reset()


def _open_session(ws) -> str:
    """Consume the `session` frame and the opening banner."""
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert session_frame["protocol_version"] == PROTOCOL_VERSION
    banner = ws.receive_json()
    assert banner["type"] == "output"
    return session_frame["session_id"]


def _hardware_status(ws) -> dict:
    """Send one hardware poll and return the single frame it produced."""
    ws.send_json({"type": "hardware_status"})
    return ws.receive_json()


# --- 1: Hack Mode receives the shared hardware state ------------------------


def test_hack_mode_reports_a_connected_esp32_from_the_shared_state(
    client: TestClient, detector: _FakeDetector
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        frame = _hardware_status(ws)

        assert frame["type"] == "hardware"
        assert frame["data"]["status"] == "connected"
        assert frame["data"]["connected"] is True
        assert frame["data"]["board_name"] == "ESP32 Dev Module"
        assert frame["data"]["port"] == "COM7"
        assert frame["data"]["fqbn"] == FQBN


def test_hack_mode_reports_no_device_when_nothing_is_attached(
    client: TestClient, detector: _FakeDetector
) -> None:
    detector.outcome = DeviceDetectOutcome(devices=())

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        data = _hardware_status(ws)["data"]

        assert data["status"] == "disconnected"
        assert data["connected"] is False
        assert data["board_name"] is None
        assert data["port"] is None


def test_hack_mode_distinguishes_a_broken_toolchain_from_an_unplugged_board(
    client: TestClient, detector: _FakeDetector
) -> None:
    """"No ESP32 detected" must never be how a missing CLI is presented."""
    detector.outcome = DeviceDetectOutcome(
        category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
    )

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        data = _hardware_status(ws)["data"]

        assert data["status"] == "error"
        assert data["status"] != "disconnected"


def test_hack_mode_names_an_unidentified_board_from_backend_config(
    client: TestClient, detector: _FakeDetector
) -> None:
    """A classic ESP32 behind a USB-UART bridge still gets a truthful label.

    The fallback comes from `config.HARDWARE_BOARD_LABEL` — backend
    configuration — never from a hardcoded frontend string, and it only ever
    names a board detection already found.
    """
    detector.outcome = DeviceDetectOutcome(
        devices=(SerialDevice(port="COM5", protocol="serial", has_usb_id=True),)
    )

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        data = _hardware_status(ws)["data"]

        assert data["status"] == "connected"
        assert data["port"] == "COM5"
        assert data["board_name"] == config.HARDWARE_BOARD_LABEL
        assert data["identified"] is False


def test_the_hardware_request_carries_no_fields_to_name_a_device(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Nothing on this wire can point the backend at a port or claim a board."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        ws.send_json({"type": "hardware_status", "port": "COM9"})
        reply = ws.receive_json()

        assert reply["type"] == "error"
        assert reply["message"] == "message does not match the protocol schema"


# --- 2: live detection — plug, unplug, replug, no reconnect ----------------


def test_unplugging_is_reflected_on_the_next_poll_without_reconnecting(
    client: TestClient, detector: _FakeDetector
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        assert _hardware_status(ws)["data"]["connected"] is True

        detector.outcome = DeviceDetectOutcome(devices=())

        assert _hardware_status(ws)["data"]["status"] == "disconnected"


def test_replugging_is_reflected_on_the_next_poll_within_one_session(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Phase 1 acceptance: no page refresh, no new socket, no restart."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        assert _hardware_status(ws)["data"]["port"] == "COM7"

        detector.outcome = DeviceDetectOutcome(devices=())
        assert _hardware_status(ws)["data"]["connected"] is False

        detector.outcome = DeviceDetectOutcome(
            devices=(SerialDevice(port="COM4", protocol="serial", has_usb_id=True),)
        )
        reconnected = _hardware_status(ws)["data"]

        assert reconnected["connected"] is True
        assert reconnected["port"] == "COM4"


# --- 3: SILENT polling — the hard requirement -------------------------------


def test_a_hardware_poll_produces_exactly_one_frame_and_it_is_not_output(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Nothing reaches xterm.js: no "ESP32 CONNECTED", no line at all.

    Proven by sending a poll, then a command whose reply is known, and
    checking the very next frame after the `hardware` one is that command's
    output — if the poll had also written a terminal line, an extra `output`
    frame would sit in between.
    """
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        frame = _hardware_status(ws)
        assert frame["type"] == "hardware"

        ws.send_json({"type": "input", "data": "help"})
        following = ws.receive_json()

        assert following["type"] == "output"
        assert "help" in following["data"].lower()


def test_polling_repeatedly_never_emits_an_event_or_scenario_state(
    client: TestClient, detector: _FakeDetector
) -> None:
    """No Activity Log rows, no exploit events, no scenario snapshots.

    A client polls this for the whole life of a session; a single `event`
    frame per poll would bury real student activity under hardware noise.
    """
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        frames = []
        for _ in range(5):
            frames.append(_hardware_status(ws))
            # Alternate a connected/disconnected board, so even a *change*
            # in hardware state cannot be what triggers an event.
            detector.outcome = (
                DeviceDetectOutcome(devices=())
                if detector.outcome.devices
                else DeviceDetectOutcome(devices=(_ESP32,))
            )

        assert [frame["type"] for frame in frames] == ["hardware"] * 5
        assert not any(frame["type"] in {"event", "state", "output"} for frame in frames)


def test_polling_does_not_touch_hack_scenario_state(
    client: TestClient, detector: _FakeDetector
) -> None:
    """The simulated target is unaffected by the physical board.

    Compares the scenario snapshot around a burst of polls — discovery
    flags, attack flags and completion must all be exactly as they were.
    """
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = _session(session_id)
        before = session.scenario.snapshot()

        for _ in range(3):
            assert _hardware_status(ws)["type"] == "hardware"

        assert session.scenario.snapshot() == before


def test_polling_records_no_scenario_events(
    client: TestClient, detector: _FakeDetector
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = _session(session_id)
        before = len(session.scenario.events)

        for _ in range(3):
            _hardware_status(ws)

        assert len(session.scenario.events) == before


def test_polling_is_not_treated_as_a_student_command(
    client: TestClient, detector: _FakeDetector
) -> None:
    """A poll must never reach the command router.

    If it did, it would be indistinguishable from something typed: it would
    echo, it could count toward attempts, and an unknown-command reply would
    appear in the terminal.
    """
    import app.commands.router as router_module

    dispatched: list[str] = []
    original = router_module.CommandRouter.dispatch

    async def recording(self, line, context):
        dispatched.append(line)
        return await original(self, line, context)

    router_module.CommandRouter.dispatch = recording
    try:
        with client.websocket_connect("/ws/hack") as ws:
            _open_session(ws)
            _hardware_status(ws)
            assert dispatched == []

            # Sanity: a real command still goes through the router, so the
            # assertion above is meaningful rather than vacuous.
            ws.send_json({"type": "input", "data": "help"})
            ws.receive_json()
            assert dispatched == ["help"]
    finally:
        router_module.CommandRouter.dispatch = original


def _session(session_id: str):
    """The live `HackSession` behind an open socket."""
    import asyncio

    session = asyncio.run(session_manager.get(session_id))
    assert session is not None
    return session


# --- 4: the Hack Engine is unchanged ----------------------------------------


def test_the_simulated_hack_engine_still_works_alongside_hardware_polling(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Phase 1 adds observation of the board, not interaction with it.

    A real scenario command still produces its terminal output, its scenario
    events and its state frame, interleaved with hardware polls.
    """
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _hardware_status(ws)

        ws.send_json({"type": "input", "data": "firmware-extract"})
        output = ws.receive_json()
        assert output["type"] == "output"

        event = ws.receive_json()
        assert event["type"] == "event"
        assert event["event"] == "firmware_extracted"

        state = ws.receive_json()
        assert state["type"] == "state"

        # And polling still works afterwards, still silently.
        assert _hardware_status(ws)["type"] == "hardware"


def test_resize_still_behaves_exactly_as_before(
    client: TestClient, detector: _FakeDetector
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "resize", "cols": 100, "rows": 30})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "resize accepted (100x30)" in reply["data"]


# --- 5: both modes agree about one physical board ---------------------------


def test_build_mode_and_hack_mode_report_the_same_board_and_port(
    client: TestClient, detector: _FakeDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 1 acceptance criterion, end to end over both sockets.

    Build Mode's service is pointed at the same shared monitor production
    uses (it already is by default; this only re-states it explicitly after
    other tests may have swapped it), so both channels resolve to one
    detection of one board.
    """
    monkeypatch.setattr(default_service, "_monitor", device_monitor)

    with client.websocket_connect("/ws/hack") as hack_ws:
        _open_session(hack_ws)
        hack = _hardware_status(hack_ws)["data"]

    with client.websocket_connect("/ws/build") as build_ws:
        for _ in range(4):  # session + 2 bootstrap events + state
            build_ws.receive_json()
        build_ws.send_json({"type": "hardware_status"})
        build = build_ws.receive_json()["data"]["hardware"]

    assert hack["status"] == build["status"] == "connected"
    assert hack["port"] == build["port"] == "COM7"
    assert hack["board_name"] == build["board_name"] == "ESP32 Dev Module"


def test_both_modes_report_no_device_together(
    client: TestClient, detector: _FakeDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(default_service, "_monitor", device_monitor)
    detector.outcome = DeviceDetectOutcome(devices=())

    with client.websocket_connect("/ws/hack") as hack_ws:
        _open_session(hack_ws)
        hack = _hardware_status(hack_ws)["data"]

    with client.websocket_connect("/ws/build") as build_ws:
        for _ in range(4):
            build_ws.receive_json()
        build_ws.send_json({"type": "hardware_status"})
        build = build_ws.receive_json()["data"]["hardware"]

    assert hack["status"] == build["status"] == "disconnected"
    assert hack["port"] is build["port"] is None


def test_one_shared_detection_serves_both_modes_within_the_window(
    client: TestClient, detector: _FakeDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two modes asking must not mean two `arduino-cli board list` runs."""
    monkeypatch.setattr(default_service, "_monitor", device_monitor)
    monkeypatch.setattr(device_monitor, "_cache_seconds", 30.0)
    device_monitor.reset()

    with client.websocket_connect("/ws/hack") as hack_ws:
        _open_session(hack_ws)
        _hardware_status(hack_ws)

    with client.websocket_connect("/ws/build") as build_ws:
        for _ in range(4):
            build_ws.receive_json()
        build_ws.send_json({"type": "hardware_status"})
        build_ws.receive_json()

    assert detector.calls == 1


# --- 6: Hack Mode does not own, and cannot write to, the board -------------


def test_hack_mode_exposes_the_port_for_a_future_serial_transport(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Phase 2's hook — and the entire extent of it in Phase 1.

    The detected serial port reaches Hack Mode so a later serial transport
    has something to open. Nothing in this phase opens it.
    """
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        data = _hardware_status(ws)["data"]

    assert data["port"] == "COM7"


def test_no_serial_transport_exists_in_the_hack_protocol_yet(
    client: TestClient, detector: _FakeDetector
) -> None:
    """Phase 1 adds no serial read/write, serial monitor, or serial send."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        for frame in (
            {"type": "serial_send", "data": "AT\r"},
            {"type": "serial_read"},
            {"type": "serial_open", "port": "COM7"},
        ):
            ws.send_json(frame)
            reply = ws.receive_json()
            assert reply["type"] == "error"


def test_the_shared_state_is_not_a_hack_session_field(
    client: TestClient, detector: _FakeDetector
) -> None:
    """The board is platform state, not something a Hack session owns.

    A per-session copy is exactly what would let two students' views of one
    physical ESP32 drift apart.
    """
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        _hardware_status(ws)
        session = _session(session_id)

    assert not hasattr(session, "hardware_status")
    assert not hasattr(session, "device_state")
    assert device_monitor.snapshot().status is DeviceStatus.CONNECTED


def test_a_private_monitor_never_leaks_into_the_shared_one() -> None:
    """Injecting a detector must not publish into the process-wide state."""
    private = DeviceMonitor(
        detector=_FakeDetector(DeviceDetectOutcome(devices=(_ESP32,))),
        identity_probe=NullIdentityProbe(),
    )
    import asyncio

    before = device_monitor.snapshot()
    asyncio.run(private.refresh())

    assert private.snapshot().connected is True
    assert device_monitor.snapshot() is before
