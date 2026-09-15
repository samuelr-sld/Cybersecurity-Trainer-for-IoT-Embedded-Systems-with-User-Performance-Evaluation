"""Phase 1 verification: the shared ESP32 device-detection/state layer.

What this file is responsible for proving:

    ESP32 -> Arduino CLI detection -> Shared Device State -> Build/Hack Mode

i.e. that `app/hardware/` correctly represents the *currently attached
physical device*, that it gets there by REUSING `app/build/flasher.py`'s
existing Arduino CLI detection and parsing rather than reimplementing it,
and that it is safe for two modes to read at once.

Everything here runs against detector doubles and, where real parsing is
under test, against real `arduino-cli board list --format json` payloads fed
through the real parser — no subprocess is ever spawned and no physical
board is ever required, exactly as `test_build_service.py` does for the
flash pipeline. The real `ArduinoCliFlasher` itself is covered by
`test_build_flasher.py`; what is new here is the shared layer on top of it.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from app import config
from app.build.flasher import (
    DeviceDetectOutcome,
    DeviceDetectRequest,
    FlashFailureCategory,
    SerialDevice,
    _select_candidates,
    parse_board_list,
)
from app.build.models import HardwareStatus
from app.hardware import DeviceMonitor, DeviceState, DeviceStatus

FQBN = "esp32:esp32:esp32"


def run(coro):
    return asyncio.run(coro)


class FakeDetector:
    """A `DeviceDetector` double: canned outcome, no subprocess, no board.

    Records every `DeviceDetectRequest` it was handed (`self.requests`), which
    is how the coalescing/caching tests below count real CLI invocations —
    one appended request means one `arduino-cli board list` would have run.
    """

    def __init__(self, outcome: DeviceDetectOutcome | None = None) -> None:
        self.outcome = outcome if outcome is not None else DeviceDetectOutcome()
        self.requests: list[DeviceDetectRequest] = []

    async def detect_devices(self, request: DeviceDetectRequest) -> DeviceDetectOutcome:
        self.requests.append(request)
        # A real yield point, so `asyncio.gather` can genuinely interleave two
        # `refresh` calls — without it the first would run to completion before
        # the second started, defeating the concurrency tests.
        await asyncio.sleep(0)
        return self.outcome


def esp32(port: str = "COM7", *, identified: bool = True) -> SerialDevice:
    """One detected board, as `_select_candidates` would hand it over."""
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module" if identified else None,
        board_fqbn=FQBN if identified else None,
        has_usb_id=True,
    )


def detector_for(*devices: SerialDevice) -> FakeDetector:
    return FakeDetector(DeviceDetectOutcome(devices=devices))


def monitor_for(*devices: SerialDevice) -> tuple[DeviceMonitor, FakeDetector]:
    detector = detector_for(*devices)
    return DeviceMonitor(detector=detector), detector


# --- 1: the device state representation ------------------------------------
#
# The four fields Phase 1 requires — `connected`, `port`, `board`, `fqbn` —
# plus the discipline that `connected` cannot contradict `status`.


def test_a_fresh_device_state_claims_nothing() -> None:
    """NOT_CHECKED is not DISCONNECTED: nothing has looked yet."""
    state = DeviceState()

    assert state.status is DeviceStatus.NOT_CHECKED
    assert state.connected is False
    assert state.port is None
    assert state.board is None
    assert state.fqbn is None
    assert state.checked_at is None


def test_connected_is_derived_from_status_not_stored_separately() -> None:
    """A state can never claim to be connected while reporting otherwise."""
    for status in DeviceStatus:
        state = DeviceState(status=status)
        assert state.connected is (status is DeviceStatus.CONNECTED)


def test_device_state_is_immutable() -> None:
    """Replaced wholesale, never mutated — the basis of lock-free reads."""
    state = DeviceState()
    with pytest.raises(Exception):
        state.port = "COM7"  # type: ignore[misc]


def test_snapshot_carries_the_shared_contract() -> None:
    state = DeviceState(
        status=DeviceStatus.CONNECTED,
        port="COM7",
        board="ESP32 Dev Module",
        fqbn=FQBN,
        identified=True,
    )

    snapshot = state.snapshot()

    # `status`/`board_name`/`port` are spelled exactly as Build Mode's own
    # `hardware` block spells them, which is what lets one frontend
    # formatter render a Build Mode `state` frame and a Hack Mode
    # `hardware` frame.
    assert snapshot["status"] == "connected"
    assert snapshot["board_name"] == "ESP32 Dev Module"
    assert snapshot["port"] == "COM7"
    assert snapshot["connected"] is True
    assert snapshot["fqbn"] == FQBN
    assert snapshot["identified"] is True


def test_build_mode_and_the_shared_layer_share_one_status_vocabulary() -> None:
    """Not merely equal values — literally the same enum object.

    This is what makes it impossible for a Build Mode `state` frame and a
    Hack Mode `hardware` frame to describe one board with different words.
    """
    assert HardwareStatus is DeviceStatus
    assert HardwareStatus.CONNECTED is DeviceStatus.CONNECTED
    assert [member.value for member in HardwareStatus] == [
        member.value for member in DeviceStatus
    ]


# --- 2: ESP32 connected -----------------------------------------------------


def test_monitor_starts_with_nothing_checked() -> None:
    monitor, detector = monitor_for()

    # `snapshot()` must never trigger detection — it is the lock-free read
    # path both modes use.
    assert monitor.snapshot().status is DeviceStatus.NOT_CHECKED
    assert detector.requests == []


def test_a_connected_esp32_is_reported_with_its_real_port_and_board() -> None:
    monitor, _ = monitor_for(esp32(port="COM7"))

    state = run(monitor.refresh())

    assert state.connected is True
    assert state.status is DeviceStatus.CONNECTED
    assert state.port == "COM7"
    assert state.board == "ESP32 Dev Module"
    assert state.fqbn == FQBN
    assert state.identified is True
    assert state.checked_at is not None


def test_refresh_publishes_into_the_shared_state_readable_without_awaiting() -> None:
    """The point of the shared layer: a second reader sees the same board."""
    monitor, _ = monitor_for(esp32(port="COM5"))

    run(monitor.refresh())

    assert monitor.snapshot().port == "COM5"
    assert monitor.snapshot() is monitor.snapshot()


def test_an_unidentified_board_is_connected_but_honestly_unnamed() -> None:
    """A classic ESP32 behind a CP2102/CH340 bridge has no CLI board name.

    The shared layer reports `board=None` rather than inventing one — each
    mode supplies its own fallback label (Build Mode its project board, Hack
    Mode `config.HARDWARE_BOARD_LABEL`).
    """
    monitor, _ = monitor_for(esp32(port="COM5", identified=False))

    state = run(monitor.refresh())

    assert state.connected is True
    assert state.port == "COM5"
    assert state.board is None
    assert state.identified is False
    # Falls back to the target it detected against, never to an invented one.
    assert state.fqbn == config.HARDWARE_TARGET_FQBN


# --- 3: ESP32 disconnected --------------------------------------------------


def test_no_device_is_a_truthful_disconnected_not_an_error() -> None:
    monitor, _ = monitor_for()

    state = run(monitor.refresh())

    assert state.status is DeviceStatus.DISCONNECTED
    assert state.connected is False
    assert state.port is None
    assert state.board is None


def test_discovery_failure_is_error_and_never_dressed_up_as_disconnected() -> None:
    """The distinction the whole failure-domain split exists for.

    A missing or wedged Arduino CLI is an environment problem. Reporting it
    as "no ESP32 is plugged in" would send a student to check their USB
    cable for a toolchain fault.
    """
    detector = FakeDetector(
        DeviceDetectOutcome(
            category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE,
            stderr="arduino-cli not found",
        )
    )
    monitor = DeviceMonitor(detector=detector)

    state = run(monitor.refresh())

    assert state.status is DeviceStatus.ERROR
    assert state.status is not DeviceStatus.DISCONNECTED
    assert state.connected is False
    assert "arduino-cli not found" in state.detail


def test_several_candidates_are_ambiguous_and_never_guessed_between() -> None:
    monitor, _ = monitor_for(esp32(port="COM7"), esp32(port="COM9"))

    state = run(monitor.refresh())

    assert state.status is DeviceStatus.AMBIGUOUS
    assert state.connected is False
    assert state.port is None
    assert "COM7" in state.detail and "COM9" in state.detail


def test_refresh_never_raises_on_a_broken_toolchain() -> None:
    """A poller calls this forever; it must always return an answer."""
    for category in (
        FlashFailureCategory.TIMEOUT,
        FlashFailureCategory.INTERNAL_ERROR,
        FlashFailureCategory.TOOLCHAIN_UNAVAILABLE,
    ):
        monitor = DeviceMonitor(detector=FakeDetector(DeviceDetectOutcome(category=category)))
        assert run(monitor.refresh()).status is DeviceStatus.ERROR


# --- 4: unplug / reconnect, with no restart in between ----------------------


def test_unplugging_stops_the_board_being_reported_as_connected() -> None:
    detector = detector_for(esp32(port="COM7"))
    monitor = DeviceMonitor(detector=detector)
    assert run(monitor.refresh()).connected is True

    detector.outcome = DeviceDetectOutcome(devices=())
    state = run(monitor.refresh())

    assert state.status is DeviceStatus.DISCONNECTED
    assert state.port is None
    assert state.board is None
    assert monitor.snapshot().connected is False


def test_the_full_plug_unplug_replug_cycle_on_one_live_monitor() -> None:
    """Phase 1's acceptance criterion: no process restart, no page refresh.

    One long-lived monitor — the same object both modes hold — tracks the
    board through being present, gone, and back on a different port.
    """
    detector = detector_for(esp32(port="COM7"))
    monitor = DeviceMonitor(detector=detector)

    assert run(monitor.refresh()).port == "COM7"

    detector.outcome = DeviceDetectOutcome(devices=())
    assert run(monitor.refresh()).status is DeviceStatus.DISCONNECTED

    detector.outcome = DeviceDetectOutcome(devices=(esp32(port="COM4"),))
    reconnected = run(monitor.refresh())

    assert reconnected.connected is True
    assert reconnected.port == "COM4"


def test_a_detection_in_flight_is_visible_without_losing_the_last_verdict() -> None:
    """DETECTING must not blank a board that is still plugged in."""
    seen: list[DeviceState] = []

    class Watching(FakeDetector):
        async def detect_devices(self, request):
            seen.append(monitor.snapshot())
            return await super().detect_devices(request)

    detector = Watching(DeviceDetectOutcome(devices=(esp32(port="COM7"),)))
    monitor = DeviceMonitor(detector=detector)
    run(monitor.refresh())

    detector.outcome = DeviceDetectOutcome(devices=(esp32(port="COM7"),))
    run(monitor.refresh())

    mid_flight = seen[-1]
    assert mid_flight.status is DeviceStatus.DETECTING
    assert mid_flight.port == "COM7"


# --- 5: reuse of the existing Arduino CLI detection and parsing -------------
#
# Phase 1 requires the shared layer to REUSE `app/build/flasher.py`, not to
# grow a second copy of Arduino CLI knowledge.


def test_the_monitor_detects_through_the_existing_flasher_request_type() -> None:
    """It asks via `DeviceDetectRequest` — the existing detection contract."""
    monitor, detector = monitor_for(esp32())

    run(monitor.refresh())

    assert len(detector.requests) == 1
    request = detector.requests[0]
    assert isinstance(request, DeviceDetectRequest)
    assert request.fqbn == config.HARDWARE_TARGET_FQBN
    assert request.timeout_seconds == config.BUILD_DEVICE_DETECT_TIMEOUT_SECONDS


def test_a_caller_may_name_the_board_target_to_prefer() -> None:
    """Build Mode passes its own project board; the monitor honours it."""
    monitor, detector = monitor_for(esp32())

    run(monitor.refresh(fqbn="esp32:esp32:nodemcu-32s"))

    assert detector.requests[0].fqbn == "esp32:esp32:nodemcu-32s"


def test_shared_state_is_built_from_the_real_board_list_parser() -> None:
    """End-to-end over `parse_board_list` + `_select_candidates`.

    A genuine `arduino-cli board list --format json` payload is parsed by
    the REAL parser in `app/build/flasher.py` and narrowed by the REAL
    candidate ladder, then folded into shared state — proving the shared
    layer consumes that pipeline rather than parsing CLI output itself.
    """
    payload = """
    {"detected_ports": [
        {"port": {"address": "COM7", "label": "COM7", "protocol": "serial",
                  "properties": {"vid": "0x10c4", "pid": "0xea60"}},
         "matching_boards": [{"name": "ESP32 Dev Module", "fqbn": "esp32:esp32:esp32"}]},
        {"port": {"address": "COM1", "label": "COM1", "protocol": "serial"}}
    ]}
    """

    devices = parse_board_list(payload)
    outcome = DeviceDetectOutcome(devices=_select_candidates(devices, FQBN))
    state = DeviceMonitor().publish(outcome, fqbn=FQBN)

    # COM1 (no USB id, unidentified) was dropped by the real ladder, so this
    # is CONNECTED rather than AMBIGUOUS.
    assert state.status is DeviceStatus.CONNECTED
    assert state.port == "COM7"
    assert state.board == "ESP32 Dev Module"
    assert state.fqbn == FQBN


def test_the_shared_layer_builds_no_arduino_cli_command_of_its_own() -> None:
    """Static guard: detection knowledge stays in `app/build/flasher.py`.

    If a future change starts assembling `board`/`list`/`--format json`
    here, that is a second implementation of something this phase exists to
    share, and this fails rather than letting the two drift apart.
    """
    package = pathlib.Path(__file__).resolve().parents[1] / "app" / "hardware"
    modules = sorted(package.rglob("*.py"))
    assert modules, "no shared hardware modules found"

    # Docstrings legitimately *name* the CLI when explaining the delegation,
    # so they are excluded: what would be a second implementation is a
    # command fragment in real code, not prose about one.
    offenders = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            text = node.value.lower()
            if any(fragment in text for fragment in ("--format", "arduino-cli", "board list")):
                offenders.append(f"{path.name}: {node.value[:60]!r}")
    assert offenders == [], f"Arduino CLI command built in the shared layer: {offenders}"


# --- 6: safe concurrent consumption by both modes ---------------------------
#
# The shared layer exists so Build Mode and Hack Mode read one board. That
# only works if simultaneous reads are safe and simultaneous polls do not
# multiply real `arduino-cli board list` invocations.


def test_concurrent_refreshes_collapse_into_one_cli_invocation() -> None:
    """Build Mode and Hack Mode polling at once must not double the work."""
    detector = detector_for(esp32(port="COM7"))
    monitor = DeviceMonitor(detector=detector, cache_seconds=30.0)

    async def both_modes():
        return await asyncio.gather(*(monitor.refresh() for _ in range(5)))

    states = run(both_modes())

    assert len(detector.requests) == 1
    assert {state.port for state in states} == {"COM7"}


def test_both_modes_read_one_identical_state_object() -> None:
    monitor, _ = monitor_for(esp32(port="COM7"))
    run(monitor.refresh())

    build_mode_view = monitor.snapshot()
    hack_mode_view = monitor.snapshot()

    assert build_mode_view is hack_mode_view
    assert build_mode_view.port == hack_mode_view.port == "COM7"


def test_a_fresh_detection_is_reused_within_the_coalescing_window() -> None:
    detector = detector_for(esp32())
    monitor = DeviceMonitor(detector=detector, cache_seconds=30.0)

    run(monitor.refresh())
    run(monitor.refresh())

    assert len(detector.requests) == 1


def test_reuse_is_off_by_default_so_an_injected_detector_is_always_asked() -> None:
    monitor, detector = monitor_for(esp32())

    run(monitor.refresh())
    run(monitor.refresh())

    assert len(detector.requests) == 2


def test_a_cached_detection_is_not_reused_across_a_different_platform() -> None:
    """A check for one board platform must not be served by another's."""
    detector = detector_for(esp32())
    monitor = DeviceMonitor(detector=detector, cache_seconds=30.0)

    run(monitor.refresh(fqbn="esp32:esp32:esp32"))
    run(monitor.refresh(fqbn="arduino:avr:uno"))

    assert len(detector.requests) == 2


def test_the_coalescing_window_is_well_under_the_frontend_poll_interval() -> None:
    """Otherwise the cache would start hiding real unplugs between polls.

    The frontend polls every 10s (src/hardware/deviceState.js); reuse only
    ever exists to merge *concurrent* polls, never to slow detection down.
    """
    assert 0 < config.HARDWARE_CACHE_SECONDS < 10


# --- 7: publishing a detection someone else already ran ---------------------
#
# How Build Mode's flash pipeline contributes to the shared state without
# spending a second `arduino-cli board list`.


def test_publish_records_a_detection_without_running_one() -> None:
    monitor, detector = monitor_for()

    state = monitor.publish(DeviceDetectOutcome(devices=(esp32(port="COM7"),)), fqbn=FQBN)

    assert detector.requests == [], "publish must not trigger its own detection"
    assert state.connected is True
    assert monitor.snapshot().port == "COM7"


def test_publish_and_refresh_agree_on_what_connected_means() -> None:
    """One mapping, so a flash's discovery and a poll cannot disagree."""
    outcome = DeviceDetectOutcome(devices=(esp32(port="COM7"),))
    published = DeviceMonitor().publish(outcome, fqbn=FQBN)
    refreshed = run(DeviceMonitor(detector=FakeDetector(outcome)).refresh(fqbn=FQBN))

    assert published.status is refreshed.status
    assert published.port == refreshed.port
    assert published.board == refreshed.board
    assert published.fqbn == refreshed.fqbn


# --- 8: the shared layer is state only --------------------------------------


def test_the_shared_layer_produces_no_events_of_any_kind() -> None:
    """Hardware presence is infrastructure, not student activity.

    `refresh` returns a `DeviceState` and nothing else — there is no event
    list, no log, and no sink for one to be delivered through, which is the
    structural half of Phase 1's silence requirement. The transport half is
    asserted in `test_websocket_hardware.py`.
    """
    monitor, _ = monitor_for(esp32())

    result = run(monitor.refresh())

    assert isinstance(result, DeviceState)
    assert not hasattr(result, "events")
    assert not hasattr(monitor, "events")


def test_the_shared_layer_cannot_flash_anything() -> None:
    """It knows what is connected; writing firmware is Build Mode's job."""
    monitor, _ = monitor_for(esp32())

    assert not hasattr(monitor, "run_flash")
    assert not hasattr(monitor, "flash")
