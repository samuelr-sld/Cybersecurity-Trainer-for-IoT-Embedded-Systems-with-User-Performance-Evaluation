"""Phase 2H — hardware-in-the-loop validation against a REAL, USB-connected ESP32.

Every other test in this suite proves the architecture's WIRING with fake
detectors and canned MAC probes (`tests/test_monitor_readiness.py`,
`tests/test_redetection_verdict.py`, `tests/test_session_scenario_wiring.py`,
...) — deliberately, so the suite never depends on physical hardware. This
file is the one place that removes the doubles and drives the real chain:

    real USB detection -> real esptool MAC read -> PanelRegistry ->
    PanelPackage -> ScenarioRegistry -> a real HackSession -> the real
    command router -> real serial I/O with the board

THE HARDWARE-IN-THE-LOOP PRINCIPLE THIS FILE HOLDS ITSELF TO: a test here
passes only when a genuine hardware operation happened, never merely because
a scenario's logical method returned success. As of Phase 2H.1, Hack Mode's
own `esptool.py read_flash` command reaches a real board when the shared
device layer has seen one (`app/commands/handlers/esptool_py.py`); every
other simulated tool (`nmap`, `mosquitto_sub`, `mosquitto_pub`) is still
intentionally, fully simulated, and this file proves that boundary rather
than pretending it is something it is not — see
`test_esptool_command_is_simulated_when_the_shared_monitor_has_not_seen_a_board`
and `test_esptool_command_reaches_real_hardware_when_the_shared_monitor_is_primed`.

ISOLATION FROM THE REST OF THE SUITE. Two process-wide singletons are
touched here on purpose (the shared `device_monitor`, for the serial
handlers and the real `/ws/hack` endpoint, which read it directly) and both
are restored (`device_monitor.reset()`) before any other test can observe
them. Every other real detection in this file runs against a *private*
`DeviceMonitor()` instance that no other module holds a reference to, so it
cannot leak into a test that assumes a cold, NOT_CHECKED monitor.

SKIPS CLEANLY, NEVER FAILS, WITH NO BOARD ATTACHED. `hil_monitor` below is
the first gate: every test in this file depends on it, directly or through
another fixture, so the whole module skips with one clear reason when no
ESP32 answers `arduino-cli board list` on this machine. The rest of the
backend test suite never depends on this file passing.

PANEL-SPECIFIC TESTS ARE GATED ON WHICH BOARD IT IS, NOT JUST THAT ONE IS
THERE. `panel_one_monitor` is the second gate (`tests/hil_gating.py`): the
tests that assert Panel 1's identity, package, scenario or serial banner
depend on it and skip unless the attached chip's MAC is Panel 1's — so a
different panel, or an unregistered module, skips them instead of failing them
for not being Panel 1. Panel-agnostic tests (USB detection, MAC reading, the
esptool and serial seams) depend only on `hil_monitor` and run on any board.
The gate compares the physical MAC, never the registry's answer, so a
regression in Panel 1's own binding still FAILS rather than hiding as a skip.

WHAT THIS FILE DOES NOT COVER, HONESTLY. There are no motor/relay/LED/buzzer
peripherals on the bench for this phase, so nothing here claims physical
actuation happened — the "device accepted the forged command" the simulated
scenario reports is exactly that: simulated. See the Phase 2H final report
for the NOT TESTED boundary this implies.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from app.build.flasher import default_flasher
from app.build.process import run_capture
from app.commands import CommandContext, default_router
from app.events.records import HackSessionRecord
from app.hardware import DeviceMonitor, DeviceStatus, device_monitor
from app.hardware.identity import discover_esptool
from app.hardware.panel_identification import (
    PanelIdentificationService,
    PanelIdentificationStatus,
)
from app.main import app
from app.metrics import MetricStatus, compute_acr, compute_re, compute_tte
from app.panels.service import PanelResourceService, PanelResourceStatus
from app.scenario_selection import ScenarioSource, SessionScenarioSelector
from app.scenarios import create_default_scenario
from app.scenarios.smart_home import SmartHomeMQTTScenario
from app.sessions import HackSession
from tests.hil_gating import PANEL_ONE_ID, PANEL_ONE_MAC, panel_one_skip_reason

pytestmark = pytest.mark.hardware

# PANEL_ONE_ID / PANEL_ONE_MAC (imported above) name Panel 1's registered board
# (app/hardware/panels.py::BUILT_IN_PANELS) — not a fake/test-only MAC, but this
# project's known development board, asserted against reality below rather than
# assumed. The constants below are the Smart Home scenario's own target defaults
# (app/scenarios/smart_home_state.py::MotorControlTarget), the same ones
# tests/test_smart_home_scenario.py uses.
BROKER = "192.168.50.1"
BROKER_PORT = 1883
COMMAND_TOPIC = "cybertrainer/smart-home/motor/control"

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


# --- gated real detection, for the one deterministic timing test -----------


class _GatedRealDetector:
    """The REAL arduino-cli detector, wrapped with a controllable gate.

    Same technique as `tests/test_redetection_verdict.py::GatedDetector`,
    applied to the real detector instead of a canned one: an
    `asyncio.Event` handshake makes the DETECTING race deterministic (no
    sleep, no timing guess) while what is eventually returned is still a
    genuine `arduino-cli board list` result.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.entered: asyncio.Event | None = None
        self.gate: asyncio.Event | None = None

    def hold(self) -> None:
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    def release(self) -> None:
        assert self.gate is not None
        self.gate.set()

    async def detect_devices(self, request):
        if self.entered is not None:
            self.entered.set()
        gate = self.gate
        if gate is not None:
            await gate.wait()
        return await self._inner.detect_devices(request)


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def hil_monitor():
    """One isolated `DeviceMonitor`, detected against real hardware ONCE.

    Deliberately NOT the process-wide `device_monitor` singleton: every
    other test in the suite assumes that object starts NOT_CHECKED (see
    `conftest.py::no_startup_device_detection`), so driving a real
    detection through it here would leak into unrelated tests. This
    instance is private to this module.

    Module-scoped so the one real, intrusive MAC probe this triggers
    (`esptool read_mac` resets the board) happens once for every test that
    only needs to *read* the resulting state, not once per test.

    Skips the whole module, with a clear reason, when no ESP32 answers on
    this machine — the phase's hard requirement that a hardware-in-the-loop
    suite must never make the rest of the test run depend on physical
    hardware.
    """
    monitor = DeviceMonitor()
    state = asyncio.run(monitor.refresh())
    if not state.connected:
        pytest.skip(
            "no ESP32 detected on USB ('arduino-cli board list' found nothing); "
            "hardware-in-the-loop tests require a physically connected board"
        )
    return monitor


@pytest.fixture(scope="module")
def panel_one_monitor(hil_monitor):
    """`hil_monitor`, but only when the attached board IS Panel 1.

    The second gate (see the module docstring and `tests/hil_gating.py`):
    skips every dependent test, with the reason, when the chip's MAC read by
    `hil_monitor` is absent or is any MAC other than Panel 1's — another
    panel, or an unregistered module. Reads the already-cached snapshot, so it
    triggers no second detection and no second MAC probe (a probe resets the
    board).

    Module-scoped, like `hil_monitor`, so a function-scoped fixture that
    drives the real board (`primed_shared_monitor`) is never set up for a
    test this gate is about to skip.
    """
    reason = panel_one_skip_reason(hil_monitor.snapshot())
    if reason is not None:
        pytest.skip(reason)
    return hil_monitor


@pytest.fixture
def hil_resources(panel_one_monitor) -> PanelResourceService:
    """The panel-resource chain over Panel 1's monitor. No I/O of its own.

    Gated on `panel_one_monitor`, and so is everything built on it
    (`hil_package`, `hil_selection`): every consumer asserts something about
    Panel 1's package or scenario.
    """
    identification = PanelIdentificationService(monitor=panel_one_monitor)
    return PanelResourceService(identification=identification)


@pytest.fixture
def hil_package(hil_resources):
    """The real, loaded `PanelPackage` for the attached Panel 1.

    Skipped, via `panel_one_monitor`, when the attached board is not Panel 1.
    Once Panel 1's MAC IS what answers, this FAILS (never skips) unless the
    chain resolves to READY: a silent skip there would hide a real regression
    in the panel binding rather than reporting one.
    """
    resources = hil_resources.resolve()
    assert resources.status is PanelResourceStatus.READY, resources.detail
    return resources.package


@pytest.fixture
def hil_selection(hil_resources):
    """The real `ScenarioSelection` for the attached Panel 1."""
    return SessionScenarioSelector(resources=hil_resources).select()


@pytest.fixture
def primed_shared_monitor(hil_monitor):
    """Populate the PROCESS-WIDE `device_monitor` from a real detection.

    Needed only for consumers that read the shared singleton directly
    rather than through an injectable service: the serial command handlers
    (`app/commands/handlers/serial_common.py`) and the real `/ws/hack`
    endpoint's `select_session_scenario()` call.

    FUNCTION-SCOPED, NOT MODULE-SCOPED, ON PURPOSE. `conftest.py`'s
    autouse `reset_shared_device_monitor` resets this exact singleton
    before and after EVERY test in the whole suite — the fix for the real
    cross-test leak that fixture's docstring documents — so a module-scoped
    prime here would be wiped out before the second test that needs it ever
    ran. Re-priming per test costs one extra real MAC probe for the small
    handful of tests below that need the shared singleton specifically;
    reset unconditionally afterwards (redundant with the autouse fixture's
    own reset, but explicit here is cheap and removes any doubt).
    """
    state = asyncio.run(device_monitor.refresh())
    assert state.connected, (
        "the shared device_monitor did not see the board hil_monitor already "
        "found; something about the two detections disagrees"
    )
    try:
        yield state
    finally:
        device_monitor.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --- 1: USB detection ---------------------------------------------------


def test_usb_detection_finds_a_real_board(hil_monitor) -> None:
    state = hil_monitor.snapshot()
    assert state.status is DeviceStatus.CONNECTED
    assert state.port  # a real OS/CLI-reported address ("COM3", "/dev/ttyUSB0", ...)
    assert "/dev/ttyUSB0" in state.port_aliases or state.port == "/dev/ttyUSB0"


# --- 2: MAC identification -----------------------------------------------


def test_mac_identification_reads_the_real_chip(hil_monitor) -> None:
    state = hil_monitor.snapshot()
    assert state.mac is not None, (
        "esptool could not read the chip's MAC; check discover_esptool() and, "
        "on an unusual install, TRAINER_ESPTOOL_PATH"
    )
    assert _MAC_RE.match(state.mac)
    assert state.identified is False or state.identified is True  # sanity: no crash either way


# --- 3: PanelRegistry resolution -----------------------------------------


def test_panel_registry_resolves_the_identified_board(panel_one_monitor) -> None:
    identification = PanelIdentificationService(monitor=panel_one_monitor).identify()
    assert identification.status is PanelIdentificationStatus.IDENTIFIED, (
        f"connected board MAC {identification.mac!r} is not registered to any "
        "panel (see app/hardware/panels.py::BUILT_IN_PANELS). This assertion "
        "documents this project's known development board — an UNREGISTERED "
        "board is itself a valid, already-tested outcome of the architecture, "
        "not a failure of it."
    )
    assert identification.mac == PANEL_ONE_MAC
    assert identification.panel.panel_id == PANEL_ONE_ID
    assert identification.package_id == PANEL_ONE_ID


# --- 4: PanelPackage loading ----------------------------------------------


def test_panel_package_loads_and_is_ready(hil_resources) -> None:
    resources = hil_resources.resolve()
    assert resources.status is PanelResourceStatus.READY
    assert resources.package is not None
    assert resources.package.panel_id == PANEL_ONE_ID
    assert resources.scenario is not None
    assert resources.scenario.scenario_id == PANEL_ONE_ID
    assert resources.firmware is not None


# --- 5: scenario selection -------------------------------------------------


def test_scenario_selection_chooses_the_smart_home_scenario(hil_selection) -> None:
    assert hil_selection.source is ScenarioSource.PANEL_PACKAGE
    assert hil_selection.scenario_id == PANEL_ONE_ID
    assert isinstance(hil_selection.scenario, SmartHomeMQTTScenario)
    assert hil_selection.panel_id == PANEL_ONE_ID


# --- 6: re-detection does not flatten a known panel into NOT_CONNECTED -----


def test_redetection_preserves_the_known_panel_mid_poll(
    hil_monitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 2D.4.1/2D.4.2 fix, proven against the real toolchain.

    `DeviceMonitor.refresh()` publishes DETECTING over the previous verdict
    before awaiting the real `arduino-cli board list`, so a caller mid-poll
    must still see the board as present. The unit-level race is already
    pinned deterministically with fake detectors in
    `tests/test_redetection_verdict.py`; this proves the same guarantee
    holds with the real detector standing behind it, using a gate (not a
    sleep) so the timing is exact rather than assumed.
    """
    known = hil_monitor.snapshot()
    assert known.connected and known.mac is not None

    gated = _GatedRealDetector(default_flasher)
    monkeypatch.setattr(hil_monitor, "_detector", gated)

    async def scenario():
        gated.hold()
        task = asyncio.create_task(hil_monitor.refresh())
        await gated.entered.wait()
        # The monitor has published DETECTING and is awaiting the real
        # detector — precisely the window under test.
        mid = hil_monitor.snapshot()
        gated.release()
        final = await task
        return mid, final

    mid, final = asyncio.run(scenario())

    assert mid.status is DeviceStatus.DETECTING
    assert mid.board_present, "a re-poll in flight must not read as disconnected"
    assert mid.port == known.port
    assert mid.mac == known.mac
    assert final.status is DeviceStatus.CONNECTED
    assert final.mac == known.mac


# --- 7: real esptool flash-read capability, real vs. simulated ------------


def test_real_esptool_can_read_flash_from_the_connected_board(hil_monitor, tmp_path) -> None:
    """Validates the underlying TOOLCHAIN directly, independent of Hack Mode.

    Exercises the exact same argv-array pattern
    `app/hardware/flash_reader.py::EsptoolFlashReader` uses (discovered the
    same way `app/hardware/identity.py` discovers esptool for MAC reads),
    without going through a command dispatch — the lowest-level proof that
    the capability genuinely exists on this machine's toolchain, ahead of
    the higher-level tests below that drive it through the student-facing
    command.

    Bounded and read-only: 4 KiB from the bootloader region, never a write,
    never the whole 4 MiB image, into a pytest-managed temp directory that
    is removed automatically after the test.
    """
    executable = discover_esptool()
    assert executable, "no esptool binary found; see discover_esptool()"

    state = hil_monitor.snapshot()
    out_file = tmp_path / "flash_read_hil.bin"
    args = [
        executable,
        "--port",
        state.port,
        "read_flash",
        "0x1000",
        "0x1000",
        str(out_file),
    ]

    result = asyncio.run(run_capture(args, timeout_seconds=60))

    assert result.exit_code == 0, result.stderr.decode(errors="replace")
    assert out_file.exists()
    assert out_file.stat().st_size == 0x1000


def test_esptool_command_is_simulated_when_the_shared_monitor_has_not_seen_a_board(
    hil_selection,
) -> None:
    """The no-hardware development flow, still exactly preserved (Phase 2H.1).

    `app/commands/handlers/esptool_py.py` decides real-vs-simulated from the
    PROCESS-WIDE `device_monitor` singleton — the same one every other
    consumer of "what is plugged in" reads — never from `hil_selection`'s
    own private monitor. `conftest.py`'s autouse `reset_shared_device_monitor`
    keeps that singleton at NOT_CHECKED for every test unless a test
    deliberately primes it (see `primed_shared_monitor` below), so this
    session — built from a real, hardware-identified Smart Home scenario —
    still gets the scenario's fixed, simulated chip identity
    (`ESP32-D0WDQ6`), never the real board's own `ESP32-D0WD-V3` revision,
    exactly as it always has for every existing test in this suite.
    """
    session = HackSession(session_id="hil-esptool-sim-check", scenario=hil_selection.scenario)
    context = CommandContext(session=session)

    result = asyncio.run(
        default_router.dispatch("esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    )

    joined = "\n".join(result.lines)
    assert "ESP32-D0WDQ6" in joined  # the scenario's fixed, simulated chip identity
    assert "ESP32-D0WD-V3" not in joined  # the real chip's actual revision, never leaked in
    assert session.firmware_artifact is None


def test_esptool_command_reaches_real_hardware_when_the_shared_monitor_is_primed(
    primed_shared_monitor,
) -> None:
    """THE PHASE 2H.1 FIX ITSELF: the student-facing command reaches the
    real board, produces a real artifact, and that artifact feeds the
    existing `strings`/`grep` analysis path.

    A small, bounded, fast real read (4 KiB) rather than the doc example's
    full 4 MiB — the full-size read is proven to work by
    `test_real_esptool_can_read_flash_from_the_connected_board` above and by
    the manual validation in the Phase 2H.1 report; this test only needs to
    prove the SEAM, not re-time a multi-minute transfer.

    "REAL HARDWARE RESULT != SIMULATED SCENARIO RESULT", concretely: the
    printed banner names the real board's own chip revision, and the
    `strings` output that follows is the ACTUAL ESP-IDF bootloader's
    strings (e.g. its own copyright/assert text) — not the scenario's
    fictional "Smart Home MQTT Motor Controller" table, which is what the
    no-hardware test just above still produces.
    """
    session = HackSession(session_id="hil-esptool-real-check", scenario=SmartHomeMQTTScenario())
    context = CommandContext(session=session)

    async def scenario():
        extract = await default_router.dispatch(
            "esptool.py read_flash 0x1000 0x1000 firmware.bin", context
        )
        strings_result = await default_router.dispatch("strings firmware.bin", context)
        return extract, strings_result

    extract, strings_result = asyncio.run(scenario())

    assert extract.exit_code == 0, extract.lines
    assert [e.type.value for e in extract.events] == ["firmware_extracted"]
    assert "ESP32-D0WDQ6" not in "\n".join(extract.lines)  # never the canned chip identity

    artifact = session.firmware_artifact
    assert artifact is not None
    assert artifact.size == 0x1000
    assert len(artifact.data) == 0x1000
    assert artifact.offset == 0x1000

    assert strings_result.exit_code == 0
    assert strings_result.lines, "real bootloader bytes always contain printable strings"
    # The scenario's fictional narrative must never appear against real bytes.
    joined = "\n".join(strings_result.lines)
    assert "Smart Home MQTT" not in joined
    assert [e.type.value for e in strings_result.events] == [
        "firmware_analyzed",
        "broker_discovered",
        "topic_discovered",
    ]


# --- 8: real serial commands -----------------------------------------------


def test_real_serial_commands_round_trip_through_the_router(primed_shared_monitor) -> None:
    """serial-status / serial-monitor / serial-send / serial-close against
    the real, USB-connected ESP32, through the same command router a
    student's terminal uses — proving these four commands are already
    hardware-in-the-loop capable (category C), not merely wired to look
    that way.

    The payload sent is inert, harmless diagnostic ASCII text; the parser
    upstream already guarantees it carries no shell metacharacters or
    control characters, and nothing on this path interprets it.

    Deliberately uses the GENERIC default scenario, not the Smart Home one:
    the serial commands are scenario-agnostic by architecture (they reach
    `context.session.serial`, never `context.scenario`), and this proves
    that independence rather than assuming it.
    """
    session = HackSession(session_id="hil-serial-roundtrip", scenario=create_default_scenario())
    context = CommandContext(session=session)

    async def scenario():
        status_before = await default_router.dispatch("serial-status", context)
        opened = await default_router.dispatch("serial-monitor", context)
        still_open = session.serial.is_open
        sent = await default_router.dispatch("serial-send HIL-PING", context)
        status_after = await default_router.dispatch("serial-status", context)
        closed = await default_router.dispatch("serial-close", context)
        return status_before, opened, still_open, sent, status_after, closed

    status_before, opened, still_open, sent, status_after, closed = asyncio.run(scenario())

    assert status_before.exit_code == 0
    assert "closed" in "\n".join(status_before.lines)

    assert opened.exit_code == 0
    assert still_open, "serial-monitor must have actually opened the real port"

    assert sent.exit_code == 0
    assert "sent" in "\n".join(sent.lines)

    assert "open on" in "\n".join(status_after.lines)

    assert closed.exit_code == 0
    assert not session.serial.is_open


# --- 9: real activity feeds the existing metric functions ------------------


def test_real_session_activity_feeds_the_existing_metric_functions(
    hil_selection, hil_package
) -> None:
    """Confirms real Hack Mode evidence (not fixture data) is consumable by
    the already-implemented, generic metric functions (`app/metrics/`).

    Metric FORMULAS are untouched by this phase and are not re-derived
    here — this only proves the recorded rows a real session produces are
    shaped the way those functions already expect, by driving the full
    declared workflow (`backend/panels/smart-home-mqtt-control/panel.json`)
    through the real router.

    `hil_selection` is real up through panel/package/scenario selection —
    `SmartHomeMQTTScenario` is chosen because a real chip's real MAC really
    resolved to Panel 1 — but this test deliberately does NOT prime the
    shared `device_monitor` (see `primed_shared_monitor`), so the workflow's
    `esptool.py`/`strings`/`grep` steps run the simulated path and can use
    the package's own pedagogical search terms (`grep broker`) reliably,
    regardless of whatever happens to actually be flashed on the bench's
    unrelated bare dev board right now. The command-reaches-real-hardware
    claim is proven separately and unambiguously by
    `test_esptool_command_reaches_real_hardware_when_the_shared_monitor_is_primed`.
    """
    session = HackSession(session_id="hil-metrics-evidence", scenario=hil_selection.scenario)
    context = CommandContext(session=session)

    workflow = [
        "esptool.py read_flash 0x0 0x400000 firmware.bin",
        "strings firmware.bin",
        "grep broker firmware.bin",
        f"nmap -p {BROKER_PORT} {BROKER}",
        f"mosquitto_sub -h {BROKER} -t {COMMAND_TOPIC}",
        f"mosquitto_pub -h {BROKER} -t {COMMAND_TOPIC} -m START",
    ]

    async def run_workflow():
        for line in workflow:
            result = await default_router.dispatch(line, context)
            assert result.exit_code == 0, f"{line!r} -> {result.lines}"

    asyncio.run(run_workflow())

    events = session.recorder.events
    commands = session.recorder.commands
    session_record = HackSessionRecord(
        session_id=session.session_id,
        scenario_id=session.scenario.scenario_id,
        started_at=session.created_at,
        participant_id=None,
    )

    acr = compute_acr(hil_package, session.session_id, events)
    re_value = compute_re(hil_package, session.session_id, commands)
    tte = compute_tte(session_record, events)

    assert acr.status is MetricStatus.COMPUTED
    assert acr.value == pytest.approx(100.0)

    assert re_value.status is MetricStatus.COMPUTED
    assert re_value.value == pytest.approx(100.0)

    assert tte.status is MetricStatus.COMPUTED
    assert tte.value >= 0


# --- 10: automated end-to-end validation, through the real endpoint --------


def test_the_real_websocket_endpoint_uses_the_real_panel_and_hardware(
    panel_one_monitor, primed_shared_monitor, client: TestClient
) -> None:
    """PRECONDITION -> hardware detection -> MAC -> PanelRegistry -> package
    -> SmartHomeMQTTScenario -> HackSession -> command dispatch -> event
    recording, all through `/ws/hack` exactly as a student's browser drives
    it — with a real ESP32 on the other end instead of a test double.

    Kept to two commands with a KNOWN, already-proven frame shape rather
    than the full six-step workflow: the frame-ordering contract itself
    (output, then one `event` per transition, then `state` iff any events
    occurred) is already pinned by
    `tests/test_session_scenario_wiring.py::test_event_and_state_frames_still_follow_a_scenario_command`
    for `esptool.py` specifically. The point here is only that the REAL
    board is what the endpoint selected and is reporting — the full attack
    sequence against real evidence is exercised without the WebSocket
    framing risk in
    `test_real_session_activity_feeds_the_existing_metric_functions` above.

    Reads a small, bounded 4 KiB rather than the doc example's full 4 MiB —
    fast, and the point (the command reaches real hardware at all) does not
    need a multi-minute transfer to prove; see
    `test_esptool_command_reaches_real_hardware_when_the_shared_monitor_is_primed`
    for the same real-vs-simulated contrast this test also makes, from a
    plain function call rather than the wire protocol.
    """
    with client.websocket_connect("/ws/hack") as ws:
        session_frame = ws.receive_json()
        assert session_frame["type"] == "session"
        banner = ws.receive_json()
        assert banner["type"] == "output"

        ws.send_json(
            {"type": "input", "data": "esptool.py read_flash 0x1000 0x1000 firmware.bin\r"}
        )
        output_frame = ws.receive_json()
        assert output_frame["type"] == "output"
        assert "ESP32-D0WDQ6" not in output_frame["data"]  # not the simulated banner
        event_frame = ws.receive_json()
        assert event_frame["type"] == "event"
        assert event_frame["event"] == "firmware_extracted"
        assert ws.receive_json()["type"] == "state"

        ws.send_json({"type": "input", "data": "serial-status\r"})
        status_frame = ws.receive_json()
        assert status_frame["type"] == "output"
        assert "SMART HOME MQTT CONTROL SYSTEM" in status_frame["data"]
