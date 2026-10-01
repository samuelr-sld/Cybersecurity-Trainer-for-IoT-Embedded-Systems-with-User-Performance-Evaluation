"""A board swapped for another on the SAME port must not keep the old board's name.

THE BUG. `DeviceMonitor` reads a board's MAC once per plug-in and caches it by
PORT NAME (a read resets the board, so it cannot run on every poll). Two
boards behind identical USB bridges — every generic CP210x devkit reports
`10C4:EA60`, serial `0001` — get the same port and the same descriptors, so
`arduino-cli board list` cannot tell them apart; and the cache was only dropped
when a poll happened to see NO board. Swapping Panel 2 for Panel 1 between two
polls therefore left the header, the session guard, scenario selection and —
worst — mode preparation naming PANEL 2: preparation picks the firmware to
flash from the identified panel, so it would restore Panel 2's firmware onto
Panel 1's hardware.

THE FIX, in three layers, all tested here without hardware:

A. `DeviceMonitor.refresh(reverify_identity=True)` re-reads the MAC instead of
   trusting the cache; mode preparation's detection stage uses it.
B. `DeviceMonitor.forget_identity(port)` withdraws trust in one port's cached
   identity without probing.
C. `PortPresenceWatcher` (`app/hardware/presence.py`) calls it when the OS stops
   listing a port, which catches a swap a poll would miss.

Every board here is a fake detector plus a fake MAC probe: nothing spawns a
process, opens a port or resets a board.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from app import config
from app.build.flasher import DeviceDetectOutcome, SerialDevice
from app.hardware import DeviceMonitor
from app.hardware.identity import IdentityOutcome
from app.hardware.presence import PortPresenceWatcher, enumerate_serial_ports
from app.mode_preparation import PreparationStage, SessionMode
from tests.test_mode_preparation import BASELINE_INO, PANEL_ONE, PANEL_ONE_MAC, Bench

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
PANEL_TWO = "environmental-monitoring"
PANEL_TWO_MAC = "20:50:0d:4d:4e:a8"
PANEL_ONE_NAME = "SMART HOME MQTT CONTROL SYSTEM"
PANEL_TWO_NAME = "ENVIRONMENTAL MONITORING SYSTEM"


def cp210x(port: str = "COM3") -> SerialDevice:
    """The descriptor every generic CP210x devkit reports, whichever board it is."""
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn="esp32:esp32:esp32",
        has_usb_id=True,
        vid="10C4",
        pid="EA60",
    )


class Detector:
    """`arduino-cli board list`: the same single device every time."""

    def __init__(self, *devices: SerialDevice) -> None:
        self.devices = devices or (cp210x(),)

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        return DeviceDetectOutcome(devices=tuple(self.devices))


class Probe:
    """What a real `esptool read_mac` would read from whichever board is attached."""

    def __init__(self, mac: str | None = PANEL_TWO_MAC) -> None:
        self.on_the_wire = mac
        self.reads = 0

    async def read_mac(self, _request) -> IdentityOutcome:
        self.reads += 1
        return IdentityOutcome(mac=self.on_the_wire)


def run(coro):
    return asyncio.run(coro)


def make(cache_seconds: float = 0.0, mac: str | None = PANEL_TWO_MAC):
    probe = Probe(mac)
    monitor = DeviceMonitor(detector=Detector(), identity_probe=probe, cache_seconds=cache_seconds)
    return monitor, probe


# =============================================================================
# The cause, pinned: polling cannot see a same-descriptor swap
# =============================================================================


def test_a_plain_refresh_still_probes_once_per_plug_in() -> None:
    """The cache's purpose is unchanged: polling must not reset the board."""
    monitor, probe = make()

    async def go():
        for _ in range(3):
            await monitor.refresh()

    run(go())
    assert probe.reads == 1


def test_polling_alone_cannot_see_a_swap_behind_an_identical_bridge() -> None:
    """Why this bug existed — and why layers A and C are needed. The detection
    is byte-identical before and after the swap, so a plain (even forced-fresh)
    refresh has nothing to notice and keeps the previous board's name."""
    monitor, probe = make()

    async def go():
        first = await monitor.refresh()
        probe.on_the_wire = PANEL_ONE_MAC  # unplug Panel 2, plug Panel 1: same port
        second = await monitor.refresh(max_age_seconds=0)
        return first, second

    first, second = run(go())
    assert first.panel == PANEL_TWO_NAME
    assert second.panel == PANEL_TWO_NAME  # stale, by design of a poll
    assert probe.reads == 1


# =============================================================================
# A. refresh(reverify_identity=True)
# =============================================================================


def test_reverifying_reads_the_board_that_is_actually_attached() -> None:
    monitor, probe = make()

    async def go():
        await monitor.refresh()
        probe.on_the_wire = PANEL_ONE_MAC
        return await monitor.refresh(reverify_identity=True)

    state = run(go())
    assert state.mac == PANEL_ONE_MAC
    assert state.panel == PANEL_ONE_NAME
    assert state.connected and state.port == "COM3"
    assert probe.reads == 2


def test_reverifying_ignores_the_freshness_cache() -> None:
    """A cached verdict cannot be re-verified, so reverify implies a re-detect
    even when the caller passed no `max_age_seconds` and the cache is warm."""
    monitor, probe = make(cache_seconds=60.0)

    async def go():
        await monitor.refresh()
        probe.on_the_wire = PANEL_ONE_MAC
        cached = await monitor.refresh()  # served from the 60 s cache
        verified = await monitor.refresh(reverify_identity=True)
        return cached, verified

    cached, verified = run(go())
    assert cached.panel == PANEL_TWO_NAME and probe.reads == 2
    assert verified.panel == PANEL_ONE_NAME


def test_the_new_identity_is_what_the_shared_snapshot_holds() -> None:
    monitor, probe = make()

    async def go():
        await monitor.refresh()
        probe.on_the_wire = PANEL_ONE_MAC
        await monitor.refresh(reverify_identity=True)

    run(go())
    assert monitor.snapshot().mac == PANEL_ONE_MAC
    assert monitor.snapshot().panel == PANEL_ONE_NAME


def test_a_failed_reverification_never_falls_back_to_the_stale_name() -> None:
    """Safe failure: unidentified, not "probably still the old board"."""
    monitor, probe = make()

    async def go():
        await monitor.refresh()
        probe.on_the_wire = None  # esptool could not read the MAC
        return await monitor.refresh(reverify_identity=True)

    state = run(go())
    assert state.connected  # presence comes from detection, not esptool
    assert state.mac is None and state.panel is None
    assert monitor.snapshot().mac is None


def test_reverifying_never_probes_while_a_flash_owns_the_port() -> None:
    """The existing hazard rule outranks the request: keep what we know."""
    monitor, probe = make()

    async def go():
        await monitor.refresh()
        probe.on_the_wire = PANEL_ONE_MAC
        with monitor.hold_identity_probe():
            return await monitor.refresh(reverify_identity=True)

    state = run(go())
    assert probe.reads == 1
    assert state.panel == PANEL_TWO_NAME


def test_reverifying_when_nothing_is_attached_reports_nothing_attached() -> None:
    probe = Probe()
    monitor = DeviceMonitor(detector=Detector(), identity_probe=probe)
    monitor._detector.devices = ()  # unplugged

    state = run(monitor.refresh(reverify_identity=True))
    assert not state.connected and state.mac is None
    assert probe.reads == 0


# =============================================================================
# B. forget_identity
# =============================================================================


def test_forgetting_a_port_clears_its_identity_from_the_snapshot_at_once() -> None:
    monitor, probe = make()
    run(monitor.refresh())
    assert monitor.snapshot().panel == PANEL_TWO_NAME

    monitor.forget_identity("COM3")

    snapshot = monitor.snapshot()
    assert snapshot.mac is None and snapshot.panel is None
    # The board did not go anywhere: only the claim about WHICH board it is.
    assert snapshot.connected and snapshot.port == "COM3"
    assert snapshot.board_present
    assert probe.reads == 1  # forgetting never probes


def test_the_next_refresh_reads_the_board_again_exactly_once() -> None:
    monitor, probe = make()

    async def go():
        await monitor.refresh()
        monitor.forget_identity("COM3")
        probe.on_the_wire = PANEL_ONE_MAC
        first = await monitor.refresh()
        await monitor.refresh()
        return first

    first = run(go())
    assert first.panel == PANEL_ONE_NAME
    assert probe.reads == 2  # one at plug-in, one after forgetting; then cached again


def test_forgetting_another_port_leaves_this_identity_alone() -> None:
    monitor, probe = make()
    run(monitor.refresh())

    monitor.forget_identity("COM9")

    assert monitor.snapshot().panel == PANEL_TWO_NAME
    run(monitor.refresh())
    assert probe.reads == 1


def test_forgetting_every_port_clears_the_cache_and_the_snapshot() -> None:
    monitor, probe = make()
    run(monitor.refresh())

    monitor.forget_identity()

    assert monitor.snapshot().panel is None
    run(monitor.refresh())
    assert probe.reads == 2


def test_forgetting_before_any_detection_is_harmless() -> None:
    monitor, _ = make()
    monitor.forget_identity("COM3")
    monitor.forget_identity()
    assert monitor.snapshot().panel is None


# =============================================================================
# The dangerous path: mode preparation must flash the board that is THERE
# =============================================================================


def _panel_two_baseline() -> str:
    from app.panels import default_panel_package_loader

    sketch = default_panel_package_loader().load(PANEL_TWO).firmware_sketch_path
    return (sketch / f"{sketch.name}.ino").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", [SessionMode.HACK, SessionMode.BUILD])
def test_preparation_after_a_swap_flashes_the_new_boards_own_firmware(mode) -> None:
    bench = Bench(mac=PANEL_TWO_MAC)

    first = bench.prepare(mode)
    assert first.success, first
    assert first.data["panel_id"] == PANEL_TWO
    assert bench.flasher.flashed[-1][1] == _panel_two_baseline()

    # Same port, same descriptors (the fake listing is identical): only the
    # MAC on the wire changes — Panel 2 unplugged, Panel 1 plugged in.
    bench.probe.mac = PANEL_ONE_MAC
    second = bench.prepare(mode)

    assert second.success, second
    assert second.data["panel_id"] == PANEL_ONE
    assert second.data["mac"] == PANEL_ONE_MAC
    baseline_one = BASELINE_INO.read_text(encoding="utf-8")
    assert bench.compiler.sources[-1] == baseline_one
    assert bench.flasher.flashed[-1][1] == baseline_one
    assert bench.flasher.flashed[-1][1] != _panel_two_baseline()


def test_preparation_reads_the_mac_again_on_every_entry() -> None:
    bench = Bench()
    bench.prepare(SessionMode.HACK)
    reads = bench.probe.reads
    bench.prepare(SessionMode.BUILD)
    assert bench.probe.reads > reads


def test_a_board_whose_mac_cannot_be_read_after_a_swap_is_never_flashed() -> None:
    """Fail safe: no identity means no flash, rather than the previous identity."""
    bench = Bench(mac=PANEL_TWO_MAC)
    assert bench.prepare(SessionMode.HACK).success
    flashes = len(bench.flasher.flashed)

    bench.probe.mac = None
    result = bench.prepare(SessionMode.HACK)

    assert not result.success
    assert result.failed_stage is PreparationStage.DETECTING_DEVICE
    assert len(bench.flasher.flashed) == flashes


# =============================================================================
# C. The presence watcher
# =============================================================================


class Ports:
    """A scriptable stand-in for the OS's serial-port list."""

    def __init__(self, *ports: str) -> None:
        self.ports = frozenset(ports)
        self.fail = False
        self.calls = 0

    def __call__(self) -> frozenset[str]:
        self.calls += 1
        if self.fail:
            raise OSError("enumeration failed")
        return self.ports


def watcher_over(monitor, ports: Ports) -> PortPresenceWatcher:
    return PortPresenceWatcher(monitor, enumerate_ports=ports, interval_seconds=0.05)


def test_the_first_look_is_a_baseline_and_forgets_nothing() -> None:
    monitor, _ = make()
    run(monitor.refresh())
    watcher = watcher_over(monitor, Ports("COM3"))

    assert run(watcher.check_once()) == frozenset()
    assert monitor.snapshot().panel == PANEL_TWO_NAME


def test_a_port_that_stays_keeps_its_identity() -> None:
    monitor, _ = make()
    run(monitor.refresh())
    ports = Ports("COM3")
    watcher = watcher_over(monitor, ports)

    async def go():
        for _ in range(3):
            await watcher.check_once()

    run(go())
    assert monitor.snapshot().panel == PANEL_TWO_NAME


def test_a_port_that_vanishes_loses_its_identity_immediately() -> None:
    monitor, probe = make()
    run(monitor.refresh())
    ports = Ports("COM3")
    watcher = watcher_over(monitor, ports)

    async def go():
        await watcher.check_once()
        ports.ports = frozenset()  # unplugged
        return await watcher.check_once()

    assert run(go()) == frozenset({"COM3"})
    assert monitor.snapshot().panel is None
    assert probe.reads == 1  # the watcher never probes


def test_the_whole_swap_ends_with_the_new_boards_identity() -> None:
    """The reported bug, end to end: Panel 2, swap to Panel 1 on the same port,
    with NO poll ever seeing the port empty — only the faster watcher does."""
    monitor, probe = make()
    ports = Ports("COM3")
    watcher = watcher_over(monitor, ports)

    async def go():
        before = await monitor.refresh()
        await watcher.check_once()  # baseline
        ports.ports = frozenset()  # Panel 2 unplugged ...
        await watcher.check_once()
        ports.ports = frozenset({"COM3"})  # ... Panel 1 plugged in, same port name
        probe.on_the_wire = PANEL_ONE_MAC
        await watcher.check_once()
        after = await monitor.refresh()  # the next ordinary poll
        return before, after

    before, after = run(go())
    assert before.panel == PANEL_TWO_NAME
    assert after.panel == PANEL_ONE_NAME
    assert after.mac == PANEL_ONE_MAC
    assert probe.reads == 2  # once per plug-in, as designed


def test_an_unrelated_port_disappearing_leaves_the_boards_identity_alone() -> None:
    monitor, _ = make()
    run(monitor.refresh())
    ports = Ports("COM3", "COM1")
    watcher = watcher_over(monitor, ports)

    async def go():
        await watcher.check_once()
        ports.ports = frozenset({"COM3"})
        return await watcher.check_once()

    assert run(go()) == frozenset({"COM1"})
    assert monitor.snapshot().panel == PANEL_TWO_NAME


def test_a_failing_enumeration_is_not_evidence_the_board_left() -> None:
    monitor, _ = make()
    run(monitor.refresh())
    ports = Ports("COM3")
    watcher = watcher_over(monitor, ports)

    async def go():
        await watcher.check_once()
        ports.fail = True
        failed = await watcher.check_once()
        ports.fail = False
        recovered = await watcher.check_once()
        return failed, recovered

    failed, recovered = run(go())
    assert failed == frozenset() and recovered == frozenset()
    assert monitor.snapshot().panel == PANEL_TWO_NAME


def test_the_loop_keeps_checking_and_stops_cleanly_when_cancelled() -> None:
    monitor, _ = make()
    ports = Ports("COM3")
    watcher = watcher_over(monitor, ports)

    async def go():
        task = asyncio.create_task(watcher.run())
        deadline = asyncio.get_running_loop().time() + 3
        while ports.calls < 3 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return ports.calls

    assert run(go()) >= 3


def test_without_pyserial_the_default_enumerator_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "serial.tools", None)
    assert enumerate_serial_ports() == frozenset()


def test_the_default_enumerator_lists_ports_without_opening_any() -> None:
    """Real pyserial, real OS list — read-only, whatever is or is not plugged in."""
    assert isinstance(enumerate_serial_ports(), frozenset)


def test_the_watcher_can_only_withdraw_trust_it_never_probes_or_runs_tools() -> None:
    tree = ast.parse((APP_DIR / "hardware" / "presence.py").read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    roots = {name.split(".")[0] for name in imported}
    assert not roots & {"subprocess", "os", "socket"}
    assert not {name for name in imported if "identity" in name or "flasher" in name or "process" in name}

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "read_mac" not in called and "refresh" not in called and "open" not in called
    assert "forget_identity" in called


# =============================================================================
# Lifespan wiring
# =============================================================================


class _FakeWatcher:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False

    async def run(self) -> None:
        self.started = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_the_lifespan_starts_and_stops_the_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    fake = _FakeWatcher()
    monkeypatch.setattr(config, "HARDWARE_PRESENCE_WATCH", True)
    monkeypatch.setattr("app.main.default_presence_watcher", lambda: fake)

    with TestClient(app) as client:
        assert client.app.state.serial_presence_watcher is not None
    assert fake.started and fake.cancelled


def test_the_watcher_is_off_when_disabled_by_config() -> None:
    from app.main import app

    with TestClient(app) as client:  # conftest disables it for the suite
        assert client.app.state.serial_presence_watcher is None


def test_the_presence_flag_defaults_on_in_the_shipped_config() -> None:
    """The fixture patches the module attribute; the shipped default is what ships."""
    source = (APP_DIR / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("TRAINER_HARDWARE_PRESENCE_WATCH", "1")' in source
