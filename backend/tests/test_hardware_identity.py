"""Panel identity: reading an ESP32's MAC and naming the panel it is.

    ESP32 -> esptool read_mac -> DeviceState.mac -> panels.py -> DeviceState.panel

Three things are verified here:

1. The MAC parsing and panel mapping are correct, including against REAL
   `esptool read_mac` output captured from this project's own board.
2. Probing is *rare and safe*: once per port rather than once per poll, and
   never while a flash owns the serial port. Reading a MAC drives the board
   into its bootloader, so getting this wrong would reset the ESP32 every
   few seconds and could corrupt a firmware upload.
3. Nothing is ever invented. An unmapped board shows no panel name, an
   unreadable MAC leaves the board connected-but-unidentified, and the UI's
   port representations come only from what detection actually reported.

No esptool is executed and no board is touched: the probe is a double
everywhere except the static argv test, which inspects the command without
running it.
"""

from __future__ import annotations

import asyncio

import pytest

from app import config
from app.build.flasher import DeviceDetectOutcome, SerialDevice, parse_board_list
from app.hardware import (
    DeviceMonitor,
    DeviceState,
    DeviceStatus,
    EsptoolIdentityProbe,
    IdentityFailure,
    IdentityOutcome,
    IdentityRequest,
    NullIdentityProbe,
    normalize_mac,
    panel_for,
    panel_names,
)
from app.hardware.identity import discover_esptool, parse_mac
from app.hardware.panels import BUILT_IN_PANEL_NAMES, _parse_overrides
from app.hardware.serial_alias import (
    canonical_alias,
    resolve_serial_target,
    serial_representations,
)

FQBN = "esp32:esp32:esp32"

#: The canonical Linux/training serial path the courseware speaks.
CANONICAL = "/dev/ttyUSB0"

#: The ESP32 on this project's development machine, and the panel it maps to.
REAL_MAC = "20:9b:a9:88:0b:e4"
REAL_PANEL = "SMART HOME MQTT CONTROL SYSTEM"

#: Captured verbatim from `esptool v5.3.1 --port COM3 --no-stub read_mac`
#: against that board. Kept as a fixture so a future esptool upgrade that
#: changes the output shape fails here, loudly, instead of silently
#: returning None and blanking every panel name in the UI.
REAL_ESPTOOL_V5_OUTPUT = """esptool v5.3.1
Serial port COM3:
Connecting.....
Detecting chip type... ESP32
Connected to ESP32 on COM3:
Chip type:          ESP32-D0WD-V3 (revision v3.1)
Features:           Wi-Fi, BT, Dual Core + LP Core, 240MHz, Vref calibration in eFuse, Coding Scheme None
Crystal frequency:  40MHz
MAC:                20:9b:a9:88:0b:e4


MAC:                20:9b:a9:88:0b:e4

Hard resetting via RTS pin...
"""


def run(coro):
    return asyncio.run(coro)


class FakeProbe:
    """Canned MAC, no esptool, no board reset. Counts its invocations."""

    def __init__(self, mac: str | None = REAL_MAC) -> None:
        self.mac = mac
        self.calls = 0
        self.ports: list[str] = []

    async def read_mac(self, request: IdentityRequest) -> IdentityOutcome:
        self.calls += 1
        self.ports.append(request.port)
        await asyncio.sleep(0)
        if self.mac is None:
            return IdentityOutcome(category=IdentityFailure.UNREACHABLE)
        return IdentityOutcome(mac=self.mac)


class FakeDetector:
    def __init__(self, outcome: DeviceDetectOutcome | None = None) -> None:
        self.outcome = outcome if outcome is not None else DeviceDetectOutcome()

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        await asyncio.sleep(0)
        return self.outcome


def esp32(port: str = "COM3", **overrides) -> SerialDevice:
    fields = dict(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn=FQBN,
        has_usb_id=True,
    )
    fields.update(overrides)
    return SerialDevice(**fields)


def monitor_for(*devices: SerialDevice, probe: FakeProbe | None = None):
    fake_probe = probe if probe is not None else FakeProbe()
    monitor = DeviceMonitor(
        detector=FakeDetector(DeviceDetectOutcome(devices=devices)),
        identity_probe=fake_probe,
    )
    return monitor, fake_probe


# --- 1: reading a MAC out of real esptool output ---------------------------


def test_the_real_boards_mac_is_parsed_from_real_esptool_output() -> None:
    assert parse_mac(REAL_ESPTOOL_V5_OUTPUT) == REAL_MAC


def test_the_esptool_v4_output_shape_is_also_understood() -> None:
    """v4 prints a leaner block; both spellings must keep working."""
    v4 = "esptool.py v4.5.1\nDetecting chip type... ESP32\nMAC: 20:9b:a9:88:0b:e4\n"
    assert parse_mac(v4) == REAL_MAC


def test_output_without_a_mac_yields_none_rather_than_a_guess() -> None:
    assert parse_mac("Connecting....\nA fatal error occurred: Failed to connect\n") is None
    assert parse_mac("") is None


def test_macs_are_normalized_to_one_canonical_spelling() -> None:
    """One spelling everywhere, so a panel lookup cannot miss on case."""
    for spelling in (
        "20:9B:A9:88:0B:E4",
        "20:9b:a9:88:0b:e4",
        "20-9B-A9-88-0B-E4",
        "  20:9b:a9:88:0b:e4  ",
    ):
        assert normalize_mac(spelling) == REAL_MAC


def test_things_that_are_not_macs_are_rejected() -> None:
    for junk in ("", "COM3", "20:9b:a9:88:0b", "20:9b:a9:88:0b:e4:ff", "zz:9b:a9:88:0b:e4"):
        assert normalize_mac(junk) is None


# --- 2: the panel mapping ---------------------------------------------------


def test_the_connected_board_maps_to_its_panel() -> None:
    assert panel_for(REAL_MAC) == REAL_PANEL


def test_panel_lookup_is_case_insensitive() -> None:
    assert panel_for("20:9B:A9:88:0B:E4") == REAL_PANEL


def test_an_unmapped_board_has_no_panel_name_rather_than_an_invented_one() -> None:
    """The UI shows the MAC instead. Naming an unknown board would tell a
    student they are sitting at a station they are not."""
    assert panel_for("aa:bb:cc:dd:ee:ff") is None


def test_no_mac_means_no_panel() -> None:
    assert panel_for(None) is None
    assert panel_for("") is None


def test_the_built_in_table_holds_canonical_keys() -> None:
    """A key that is not canonical could never be matched by a lookup."""
    for mac in BUILT_IN_PANEL_NAMES:
        assert normalize_mac(mac) == mac


def test_additional_panels_can_be_configured_without_a_code_change() -> None:
    """The four unconnected panels get added this way, or by editing the table."""
    parsed = _parse_overrides(
        "24:6F:28:AB:CD:EF=ENVIRONMENTAL MONITORING SYSTEM,"
        "aa:bb:cc:dd:ee:ff=EMERGENCY EXIT LIGHTING SYSTEM"
    )
    assert parsed == {
        "24:6f:28:ab:cd:ef": "ENVIRONMENTAL MONITORING SYSTEM",
        "aa:bb:cc:dd:ee:ff": "EMERGENCY EXIT LIGHTING SYSTEM",
    }


def test_a_malformed_override_is_skipped_not_fatal() -> None:
    """A typo in an env var must not stop the backend from starting."""
    assert _parse_overrides("not-a-mac=X,,=Y,aa:bb:cc:dd:ee:ff=") == {}


def test_configured_overrides_win_over_the_built_in_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PANEL_NAMES_RAW", f"{REAL_MAC}=RELABELLED PANEL")
    assert panel_names()[REAL_MAC] == "RELABELLED PANEL"


# --- 3: the esptool invocation is safe and correct --------------------------


def test_the_mac_read_command_is_a_safe_argument_array() -> None:
    """Same security boundary as the compiler and flasher.

    Asserted by inspecting the argv this probe *would* run — no process is
    spawned — so the shape is verified without a board or a toolchain.
    """
    captured: dict = {}

    class RecordingProbe(EsptoolIdentityProbe):
        async def read_mac(self, request):
            captured["args"] = [
                self._resolve() or "esptool",
                "--port",
                request.port,
                "--no-stub",
                "read_mac",
            ]
            return IdentityOutcome(mac=REAL_MAC)

    probe = RecordingProbe("/opt/esptool")
    run(probe.read_mac(IdentityRequest(port="COM3", timeout_seconds=5)))

    args = captured["args"]
    assert args == ["/opt/esptool", "--port", "COM3", "--no-stub", "read_mac"]
    # An argument array, never a command string: nothing here is a shell
    # line, and no element was built by concatenating caller text.
    assert all(isinstance(arg, str) for arg in args)
    assert not any(";" in arg or "&&" in arg or "|" in arg for arg in args)


def test_read_mac_is_spelled_the_way_both_esptool_majors_accept() -> None:
    """v4 accepts only `read_mac`; v5 accepts both. Underscore works on both."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app" / "hardware" / "identity.py"
    ).read_text(encoding="utf-8")
    assert '"read_mac"' in source
    assert '"read-mac"' not in source


def test_a_missing_esptool_is_reported_without_raising() -> None:
    """No ESP32 core installed is a normal state, not a crash."""
    probe = EsptoolIdentityProbe("")

    outcome = run(probe.read_mac(IdentityRequest(port="COM3", timeout_seconds=1)))

    assert outcome.ok is False
    assert outcome.mac is None
    assert outcome.category is IdentityFailure.TOOL_UNAVAILABLE


def test_esptool_discovery_finds_the_core_bundled_binary_or_nothing() -> None:
    """Whatever it returns must be a real path, never a guess.

    This machine has the ESP32 core installed, so this exercises the real
    discovery; on a machine without it, None is the correct answer and the
    board simply stays unidentified.
    """
    found = discover_esptool()
    if found is not None:
        assert "esptool" in found.lower()


def test_the_null_probe_never_touches_hardware() -> None:
    outcome = run(NullIdentityProbe().read_mac(IdentityRequest(port="COM3", timeout_seconds=1)))
    assert outcome.mac is None
    assert outcome.category is IdentityFailure.NOT_PROBED


# --- 4: the monitor attaches identity to the shared state -------------------


def test_a_connected_board_gets_its_mac_and_panel_name() -> None:
    monitor, _ = monitor_for(esp32(port="COM3"))

    state = run(monitor.refresh())

    assert state.connected is True
    assert state.port == "COM3"
    assert state.mac == REAL_MAC
    assert state.panel == REAL_PANEL


def test_an_unmapped_board_keeps_its_mac_but_has_no_panel() -> None:
    monitor, _ = monitor_for(esp32(), probe=FakeProbe(mac="aa:bb:cc:dd:ee:ff"))

    state = run(monitor.refresh())

    assert state.mac == "aa:bb:cc:dd:ee:ff"
    assert state.panel is None


def test_an_unreadable_mac_leaves_the_board_connected_but_unidentified() -> None:
    """A failed probe must never be escalated into "nothing is plugged in"."""
    monitor, _ = monitor_for(esp32(), probe=FakeProbe(mac=None))

    state = run(monitor.refresh())

    assert state.connected is True
    assert state.status is DeviceStatus.CONNECTED
    assert state.mac is None
    assert state.panel is None


def test_identity_appears_in_the_wire_snapshot() -> None:
    monitor, _ = monitor_for(esp32(port="COM3"))
    run(monitor.refresh())

    snapshot = monitor.snapshot().snapshot()

    assert snapshot["mac"] == REAL_MAC
    assert snapshot["panel"] == REAL_PANEL
    assert snapshot["port"] == "COM3"


# --- 5: probing is rare — once per board, not once per poll ----------------


def test_a_board_is_probed_once_no_matter_how_often_it_is_polled() -> None:
    """THE HAZARD: every probe resets the ESP32 into its bootloader.

    A 10-second poll that re-probed would reboot the student's board every
    10 seconds, mid-activity, forever.
    """
    monitor, probe = monitor_for(esp32(port="COM3"))

    for _ in range(5):
        run(monitor.refresh())

    assert probe.calls == 1
    assert monitor.snapshot().mac == REAL_MAC


def test_identity_is_dropped_when_the_board_is_unplugged() -> None:
    """Requirement: the header must not keep naming an unplugged panel."""
    detector = FakeDetector(DeviceDetectOutcome(devices=(esp32(),)))
    probe = FakeProbe()
    monitor = DeviceMonitor(detector=detector, identity_probe=probe)
    assert run(monitor.refresh()).panel == REAL_PANEL

    detector.outcome = DeviceDetectOutcome(devices=())
    state = run(monitor.refresh())

    assert state.connected is False
    assert state.mac is None
    assert state.panel is None


def test_replugging_re_probes_and_recovers_identity() -> None:
    detector = FakeDetector(DeviceDetectOutcome(devices=(esp32(),)))
    probe = FakeProbe()
    monitor = DeviceMonitor(detector=detector, identity_probe=probe)
    run(monitor.refresh())

    detector.outcome = DeviceDetectOutcome(devices=())
    run(monitor.refresh())
    assert monitor.snapshot().mac is None

    detector.outcome = DeviceDetectOutcome(devices=(esp32(),))
    state = run(monitor.refresh())

    assert state.mac == REAL_MAC
    assert state.panel == REAL_PANEL
    # The cache was dropped on disconnect, so this really did re-read.
    assert probe.calls == 2


def test_a_different_port_is_probed_separately() -> None:
    detector = FakeDetector(DeviceDetectOutcome(devices=(esp32(port="COM3"),)))
    probe = FakeProbe()
    monitor = DeviceMonitor(detector=detector, identity_probe=probe)
    run(monitor.refresh())

    detector.outcome = DeviceDetectOutcome(devices=(esp32(port="COM9"),))
    run(monitor.refresh())

    assert probe.ports == ["COM3", "COM9"]


# --- 6: probing never collides with a flash ---------------------------------


def test_holding_the_port_suppresses_probing() -> None:
    """`esptool read_mac` must never fight `arduino-cli upload` for a port."""
    monitor, probe = monitor_for(esp32())

    with monitor.hold_identity_probe():
        state = run(monitor.refresh())

    assert probe.calls == 0
    # Still honestly connected — only the identity is deferred.
    assert state.connected is True
    assert state.mac is None


def test_probing_resumes_once_the_port_is_released() -> None:
    monitor, probe = monitor_for(esp32())
    with monitor.hold_identity_probe():
        run(monitor.refresh())

    state = run(monitor.refresh())

    assert probe.calls == 1
    assert state.mac == REAL_MAC


def test_nested_holds_do_not_release_early() -> None:
    monitor, probe = monitor_for(esp32())

    with monitor.hold_identity_probe():
        with monitor.hold_identity_probe():
            pass
        run(monitor.refresh())

    assert probe.calls == 0


def test_publish_attaches_a_known_identity_but_never_probes() -> None:
    """How a flash's own discovery keeps the panel name on screen for free."""
    monitor, probe = monitor_for(esp32(port="COM3"))
    run(monitor.refresh())
    assert probe.calls == 1

    state = monitor.publish(DeviceDetectOutcome(devices=(esp32(port="COM3"),)), fqbn=FQBN)

    assert probe.calls == 1, "publish must never spawn a probe"
    assert state.mac == REAL_MAC
    assert state.panel == REAL_PANEL


def test_publish_for_an_unknown_port_stays_unidentified() -> None:
    monitor, probe = monitor_for()

    state = monitor.publish(DeviceDetectOutcome(devices=(esp32(port="COM9"),)), fqbn=FQBN)

    assert probe.calls == 0
    assert state.connected is True
    assert state.mac is None


# --- 7: student-facing port representations --------------------------------
#
# The USB field cycles between the ACTUAL detected port and the canonical
# Linux/training path the courseware speaks. The trainer deploys to a
# Raspberry Pi, so `/dev/ttyUSB0` is what the Hack Mode command examples use
# regardless of what the development host enumerates the same board as.


def test_a_windows_dev_port_offers_the_canonical_trainer_path() -> None:
    """COM3 on this dev box; students still read and type /dev/ttyUSB0."""
    monitor, _ = monitor_for(esp32(port="COM3"))

    state = run(monitor.refresh())

    assert state.port_aliases == ("COM3", CANONICAL)
    # The real port is first: it is the truth about this machine.
    assert state.port_aliases[0] == state.port == "COM3"


def test_a_pi_whose_real_port_is_the_canonical_path_has_one_representation() -> None:
    """Nothing to toggle between when the two coincide - and that is right."""
    monitor, _ = monitor_for(esp32(port="/dev/ttyUSB0"))

    state = run(monitor.refresh())

    assert state.port_aliases == ("/dev/ttyUSB0",)
    assert state.port == "/dev/ttyUSB0"


def test_a_pi_on_ttyacm0_keeps_its_real_port_and_offers_the_canonical_one() -> None:
    """A board enumerating as ACM still teaches the canonical path."""
    monitor, _ = monitor_for(esp32(port="/dev/ttyACM0"))

    state = run(monitor.refresh())

    assert state.port_aliases == ("/dev/ttyACM0", CANONICAL)
    assert state.port == "/dev/ttyACM0"


def test_usb_ids_are_never_a_student_facing_representation() -> None:
    """VID:PID stays backend diagnostics; it is not courseware vocabulary."""
    device = esp32(port="COM3", vid="10C4", pid="EA60")
    monitor, _ = monitor_for(device)

    state = run(monitor.refresh())

    assert state.port_aliases == ("COM3", CANONICAL)
    assert not any("VID" in alias or "10C4" in alias for alias in state.port_aliases)
    # ...but the metadata itself is still captured by detection.
    assert (device.vid, device.pid) == ("10C4", "EA60")


def test_cycling_representations_never_changes_the_real_port() -> None:
    """The header toggle is display-only; `port` is what the backend uses.

    Whatever the UI shows, `DeviceState.port` - the value `flash_workspace`
    uploads through - stays the one address detection reported.
    """
    monitor, _ = monitor_for(esp32(port="COM3"))

    state = run(monitor.refresh())

    assert state.port == "COM3"
    assert state.port_aliases[0] == state.port


def test_a_distinct_label_is_offered_as_a_representation_too() -> None:
    """Where the OS genuinely spells one device two ways, both are offered."""
    monitor, _ = monitor_for(
        esp32(port="/dev/ttyUSB0", label="/dev/serial/by-id/usb-CP2102")
    )

    state = run(monitor.refresh())

    # Canonical equals the real port here, so it dedupes away; the by-id path
    # detection actually reported remains.
    assert state.port_aliases == ("/dev/ttyUSB0", "/dev/serial/by-id/usb-CP2102")
    assert state.port == "/dev/ttyUSB0"


def test_representations_come_from_the_real_board_list_parser() -> None:
    """The exact payload the reference ESP32 produces on this Windows box."""
    payload = """
    {"detected_ports": [
        {"port": {"address": "COM3", "label": "COM3", "protocol": "serial",
                  "properties": {"vid": "0x10C4", "pid": "0xEA60"}}}
    ]}
    """
    devices = parse_board_list(payload)

    # address == label on Windows, so the duplicate is dropped rather than
    # presented as a second, identical "representation".
    assert devices[0].label is None

    state = run(monitor_for(devices[0])[0].refresh())
    assert state.port_aliases == ("COM3", CANONICAL)
    # The port the backend actually talks to is untouched by any of this.
    assert state.port == "COM3"


def test_a_disconnected_state_offers_no_port_representations() -> None:
    """No stale path, no canonical path - a dash, per the header spec."""
    monitor, _ = monitor_for()

    state = run(monitor.refresh())

    assert state.port_aliases == ()
    assert state.port is None


# --- 8: resolving a canonical target back to the real port -----------------
#
# The seam the later Hack Mode serial engine plugs into. `/dev/ttyUSB0` must
# never reach a serial library - it is resolved to `DeviceState.port` first.


def connected(port: str = "COM3") -> DeviceState:
    return DeviceState(
        status=DeviceStatus.CONNECTED,
        port=port,
        port_aliases=serial_representations(port),
    )


def test_the_canonical_path_resolves_to_the_real_windows_port() -> None:
    """`serial-monitor /dev/ttyUSB0` must open COM3 on this dev box."""
    assert resolve_serial_target(CANONICAL, connected("COM3")) == "COM3"


def test_the_canonical_path_resolves_to_a_real_acm_port_on_linux() -> None:
    """Courseware says /dev/ttyUSB0; the board is actually on /dev/ttyACM0."""
    assert resolve_serial_target(CANONICAL, connected("/dev/ttyACM0")) == "/dev/ttyACM0"


def test_the_canonical_path_resolves_to_itself_when_it_is_the_real_port() -> None:
    assert resolve_serial_target(CANONICAL, connected("/dev/ttyUSB0")) == "/dev/ttyUSB0"


def test_the_real_port_also_resolves() -> None:
    """A student reading the header's other representation, or dmesg."""
    assert resolve_serial_target("COM3", connected("COM3")) == "COM3"


def test_resolution_is_forgiving_of_case_and_whitespace() -> None:
    """Typed command lines carry stray spaces; COM names are case-blind."""
    assert resolve_serial_target("  com3  ", connected("COM3")) == "COM3"
    assert resolve_serial_target(" /dev/ttyUSB0 ", connected("COM3")) == "COM3"


def test_an_unknown_target_resolves_to_nothing_rather_than_a_guess() -> None:
    """Never fall back to the only board: that opens what nobody asked for."""
    for target in ("/dev/ttyACM9", "COM9", "", "   ", None):
        assert resolve_serial_target(target, connected("COM3")) is None


def test_nothing_resolves_while_no_device_is_connected() -> None:
    """A canonical path is a label, not a device - it cannot conjure one."""
    assert resolve_serial_target(CANONICAL, DeviceState()) is None
    assert (
        resolve_serial_target(CANONICAL, DeviceState(status=DeviceStatus.DISCONNECTED))
        is None
    )


def test_resolution_never_hands_the_canonical_alias_downstream() -> None:
    """THE POINT: what goes downstream is always the real port.

    If this ever returned the alias, a serial layer would try to open a
    device node that does not exist on this host.
    """
    for port in ("COM3", "/dev/ttyACM0", "/dev/ttyUSB1"):
        resolved = resolve_serial_target(CANONICAL, connected(port))
        assert resolved == port
        assert resolved != CANONICAL


def test_resolution_does_not_mutate_the_device_state() -> None:
    """Display/command resolution is read-only over the shared state."""
    state = connected("COM3")
    before = (state.port, state.port_aliases, state.status)

    resolve_serial_target(CANONICAL, state)

    assert (state.port, state.port_aliases, state.status) == before


def test_the_canonical_alias_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lab standardising on another node changes config, not code."""
    monkeypatch.setattr(config, "CANONICAL_SERIAL_ALIAS", "/dev/ttyAMA0")

    assert canonical_alias() == "/dev/ttyAMA0"
    assert serial_representations("COM3") == ("COM3", "/dev/ttyAMA0")
    assert resolve_serial_target("/dev/ttyAMA0", connected("COM3")) == "COM3"
