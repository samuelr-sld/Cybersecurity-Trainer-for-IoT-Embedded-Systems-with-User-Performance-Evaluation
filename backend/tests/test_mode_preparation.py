"""Mode Session Preparation — every mode entry restores the vulnerable baseline.

All of these run against fakes: a fake `arduino-cli board list`, a fake MAC
probe, a fake compiler and a fake flasher. Nothing here spawns a process or
touches a board; the physical acceptance test is run separately, by hand,
against Panel 1 (see the phase notes).

What is proved:
  1/2. Hack and Build entry both run the full baseline lifecycle, in order.
  3/4. The baseline is really compiled and really flashed — the package's
       own source reaches both adapters, to the port detection found.
  5.   A failure at any stage stops the pipeline and reports that stage.
  6.   A flash failure releases the build dir, the probe hold and the lock.
  7.   Retry is a fresh run, from detection, with a fresh build dir.
  8.   A candidate flashed from Build Mode never becomes the next baseline.
  9.   Panel firmware/scenario come from package metadata — an alternate
       panel works through the same code, and the module names no panel.
  and  the `/ws/prepare` transport: stage frames, one result, and mode
       selection being the only thing a client can say.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient

from app.build import SerialDevice
from app.build.blockly_bridge import program_to_blockly
from app.build.compiler import CompileFailureCategory, CompileOutcome
from app.build.flasher import DeviceDetectOutcome, FlashFailureCategory, FlashOutcome
from app.build.program_source import program_for_source
from app.build.semantic import (
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    SemanticProgram,
    SemanticSection,
    SemanticType,
    SymbolValue,
)
from app.build.service import BuildService
from app.build_project_selection import BuildProjectSelector
from app.build_sessions import BuildSession
from app.hardware import DeviceMonitor
from app.hardware.identity import IdentityOutcome
from app.hardware.panel_identification import PanelIdentificationService
from app.hardware.panels import PanelDefinition, PanelRegistry
from app.mode_preparation import (
    PREPARATION_STEPS,
    ModePreparationService,
    PreparationStage,
    SessionMode,
    StageStatus,
)
from app.panels.loader import PanelPackageLoader
from app.panels.service import PanelResourceService
from app.scenarios import ScenarioRegistry
from app.scenarios.environmental import EnvironmentalMonitoringScenario

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"
PACKAGE_DIR = BACKEND / "panels" / PANEL_ONE
SKETCH_NAME = "smart_home_mqtt_control.ino"
BASELINE_INO = PACKAGE_DIR / "firmware" / "smart_home_mqtt_control" / SKETCH_NAME
SECURITY = "helper_applyCommand"


# --- fakes -------------------------------------------------------------------


def _esp32(port: str = "COM7") -> SerialDevice:
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn="esp32:esp32:esp32",
        has_usb_id=True,
    )


class FakeBoard:
    """`arduino-cli board list` for one fake bench.

    `listings` is consumed one entry per detection; the last entry repeats,
    so a test scripts only the detections it cares about.
    """

    def __init__(self, *listings: tuple[SerialDevice, ...]) -> None:
        self.listings = list(listings) or [(_esp32(),)]
        self.detections = 0

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        index = min(self.detections, len(self.listings) - 1)
        self.detections += 1
        return DeviceDetectOutcome(devices=tuple(self.listings[index]))


class FakeProbe:
    def __init__(self, mac: str | None = PANEL_ONE_MAC) -> None:
        self.mac = mac
        self.reads = 0

    async def read_mac(self, _request) -> IdentityOutcome:
        self.reads += 1
        return IdentityOutcome(mac=self.mac)


def _sources(sketch_dir: pathlib.Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(sketch_dir.glob("*.ino")))


class FakeCompiler:
    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.sources: list[str] = []
        self.sketch_dirs: list[pathlib.Path] = []

    async def run_compile(self, request) -> CompileOutcome:
        self.sources.append(_sources(request.sketch_dir))
        self.sketch_dirs.append(request.sketch_dir)
        if self.success:
            return CompileOutcome.ok(exit_code=0, stdout="ok", stderr="", duration_seconds=0.1)
        return CompileOutcome.failed(
            CompileFailureCategory.COMPILER_ERROR, exit_code=1, stderr="error: expected ';'"
        )


class FakeFlasher:
    """Detection delegates to the bench; uploads are recorded."""

    def __init__(self, board: FakeBoard, monitor_ref: list, success: bool = True) -> None:
        self.board = board
        self.success = success
        self.flashed: list[tuple[str, str]] = []
        self.holds_seen: list[int] = []
        self._monitor_ref = monitor_ref

    async def detect_devices(self, request) -> DeviceDetectOutcome:
        return await self.board.detect_devices(request)

    async def run_flash(self, request) -> FlashOutcome:
        self.flashed.append((request.port, _sources(request.sketch_dir)))
        self.holds_seen.append(self._monitor_ref[0]._probe_holds)
        if self.success:
            return FlashOutcome.ok(
                exit_code=0,
                stdout="Hash of data verified.",
                stderr="",
                duration_seconds=1.0,
                port=request.port,
            )
        return FlashOutcome.failed(
            FlashFailureCategory.UPLOAD_ERROR, exit_code=2, stderr="A fatal error occurred"
        )


class Bench:
    """One preparation service wired entirely to fakes."""

    def __init__(
        self,
        *listings,
        mac: str | None = PANEL_ONE_MAC,
        compiles: bool = True,
        flashes: bool = True,
        panel_registry: PanelRegistry | None = None,
        loader: PanelPackageLoader | None = None,
        scenario_registry: ScenarioRegistry | None = None,
    ) -> None:
        self.board = FakeBoard(*listings)
        self.probe = FakeProbe(mac)
        self.monitor = DeviceMonitor(
            detector=self.board,
            cache_seconds=0.0,
            identity_probe=self.probe,
            panel_registry=panel_registry,
        )
        self.compiler = FakeCompiler(compiles)
        self.flasher = FakeFlasher(self.board, [self.monitor], flashes)
        self.resources = PanelResourceService(
            identification=PanelIdentificationService(monitor=self.monitor),
            **({} if loader is None else {"loader": loader}),
        )
        self.service = ModePreparationService(
            monitor=self.monitor,
            resources=self.resources,
            compiler=self.compiler,
            flasher=self.flasher,
            scenario_registry=scenario_registry,
        )
        self.progress = []

    async def _emit(self, progress) -> None:
        self.progress.append(progress)

    def prepare(self, mode: SessionMode):
        self.progress = []
        return asyncio.run(self.service.prepare(mode, emit=self._emit))

    def transitions(self) -> list[tuple[str, str]]:
        return [(p.stage.value, p.status.value) for p in self.progress]


def _full_run() -> list[tuple[str, str]]:
    expected = []
    for stage in PREPARATION_STEPS:
        expected += [(stage.value, "running"), (stage.value, "succeeded")]
    return expected + [("ready", "succeeded")]


# --- 1 / 2 / 3 / 4. both modes prepare the real baseline ---------------------


@pytest.mark.parametrize("mode", [SessionMode.HACK, SessionMode.BUILD])
def test_mode_entry_runs_every_stage_in_order(mode) -> None:
    bench = Bench()
    result = bench.prepare(mode)

    assert result.success, result
    assert result.mode is mode
    assert bench.transitions() == _full_run()
    assert result.data["panel_id"] == PANEL_ONE
    assert result.data["port"] == "COM7"
    assert result.data["scenario_id"] == PANEL_ONE


@pytest.mark.parametrize("mode", [SessionMode.HACK, SessionMode.BUILD])
def test_preparation_compiles_and_flashes_the_package_baseline(mode) -> None:
    bench = Bench()
    assert bench.prepare(mode).success

    baseline = BASELINE_INO.read_text(encoding="utf-8")
    assert bench.compiler.sources == [baseline]
    assert bench.flasher.flashed == [("COM7", baseline)]


def test_build_entry_loads_the_build_project_state() -> None:
    result = Bench().prepare(SessionMode.BUILD)
    assert result.data["project_id"] == "smart-home-mqtt-control-firmware"


def test_every_entry_prepares_again_nothing_is_skipped() -> None:
    bench = Bench()
    assert bench.prepare(SessionMode.HACK).success
    assert bench.prepare(SessionMode.BUILD).success
    assert bench.prepare(SessionMode.HACK).success
    assert len(bench.compiler.sources) == 3
    assert len(bench.flasher.flashed) == 3


def test_flash_holds_off_identity_probing_for_its_duration() -> None:
    bench = Bench()
    assert bench.prepare(SessionMode.HACK).success
    assert bench.flasher.holds_seen == [1]
    assert bench.monitor._probe_holds == 0


def test_verification_runs_a_fresh_detection_after_flashing() -> None:
    bench = Bench()
    assert bench.prepare(SessionMode.HACK).success
    # detection (stage 1), the flash's own discovery, post-flash verification.
    assert bench.board.detections == 3


# --- 5. failure blocks mode entry, at the stage that failed -----------------


def _stages_reached(bench) -> set[str]:
    return {p.stage.value for p in bench.progress}


def test_no_board_fails_detection_and_nothing_is_built() -> None:
    bench = Bench(())
    result = bench.prepare(SessionMode.HACK)

    assert not result.success
    assert result.failed_stage is PreparationStage.DETECTING_DEVICE
    assert "no ESP32" in result.message
    assert bench.compiler.sources == [] and bench.flasher.flashed == []
    assert bench.progress[-1].status is StageStatus.FAILED
    assert "ready" not in _stages_reached(bench)


def test_two_boards_fail_detection() -> None:
    result = Bench((_esp32("COM7"), _esp32("COM8"))).prepare(SessionMode.BUILD)
    assert result.failed_stage is PreparationStage.DETECTING_DEVICE


def test_an_unregistered_board_fails_detection() -> None:
    bench = Bench(mac="aa:bb:cc:dd:ee:ff")
    result = bench.prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.DETECTING_DEVICE
    assert "not a registered training panel" in result.message
    assert bench.compiler.sources == []


def test_an_unreadable_mac_fails_detection() -> None:
    result = Bench(mac=None).prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.DETECTING_DEVICE


def test_a_panel_without_a_package_fails_firmware_resolution(tmp_path) -> None:
    registry = PanelRegistry(
        [PanelDefinition(panel_id="bare-panel", display_name="Bare", mac_addresses=(PANEL_ONE_MAC,))]
    )
    bench = Bench(panel_registry=registry)
    result = bench.prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.RESOLVING_FIRMWARE
    assert bench.compiler.sources == []


def test_compile_failure_stops_before_flashing() -> None:
    bench = Bench(compiles=False)
    result = bench.prepare(SessionMode.HACK)

    assert result.failed_stage is PreparationStage.COMPILING
    assert "compilation failed" in result.message
    assert "expected ';'" in result.detail
    assert bench.flasher.flashed == []
    assert not {"flashing", "verifying", "loading_scenario", "ready"} & _stages_reached(bench)


def test_flash_failure_stops_before_verification() -> None:
    bench = Bench(flashes=False)
    result = bench.prepare(SessionMode.BUILD)

    assert result.failed_stage is PreparationStage.FLASHING
    assert "flash failed" in result.message
    assert not {"verifying", "loading_scenario", "ready"} & _stages_reached(bench)


def test_a_board_that_changed_port_is_never_flashed() -> None:
    # Identified on COM7; by the flash's own discovery a board sits on COM9.
    bench = Bench((_esp32("COM7"),), (_esp32("COM9"),))
    result = bench.prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.FLASHING
    assert "changed" in result.message
    assert bench.flasher.flashed == []


def test_a_board_that_does_not_return_after_flashing_fails_verification() -> None:
    bench = Bench((_esp32(),), (_esp32(),), ())
    result = bench.prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.VERIFYING
    assert "ready" not in _stages_reached(bench)


def test_an_unimplemented_scenario_fails_loading_for_hack_mode() -> None:
    bench = Bench(scenario_registry=ScenarioRegistry())
    result = bench.prepare(SessionMode.HACK)
    assert result.failed_stage is PreparationStage.LOADING_SCENARIO
    # The board was still restored — only the mode's own state is missing.
    assert len(bench.flasher.flashed) == 1


# --- 6. a flash failure releases every resource ------------------------------


def test_flash_failure_releases_build_dir_probe_hold_and_lock() -> None:
    bench = Bench(flashes=False)
    bench.prepare(SessionMode.HACK)

    [sketch_dir] = bench.compiler.sketch_dirs
    assert not sketch_dir.exists()
    assert not sketch_dir.parent.exists()
    assert bench.monitor._probe_holds == 0
    assert not bench.service._lock.locked()


def test_success_also_removes_the_throwaway_build() -> None:
    bench = Bench()
    bench.prepare(SessionMode.BUILD)
    assert not bench.compiler.sketch_dirs[0].parent.exists()


def test_cancellation_mid_compile_releases_everything() -> None:
    bench = Bench()
    entered = asyncio.Event()

    async def hang(request):
        bench.compiler.sketch_dirs.append(request.sketch_dir)
        entered.set()
        await asyncio.sleep(3600)

    bench.compiler.run_compile = hang

    async def scenario():
        task = asyncio.create_task(bench.service.prepare(SessionMode.HACK))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not bench.compiler.sketch_dirs[0].parent.exists()
    assert not bench.service._lock.locked()
    assert bench.flasher.flashed == []


# --- 7. retry is a fresh run -------------------------------------------------


def test_retry_after_a_failure_starts_over_from_detection() -> None:
    bench = Bench(flashes=False)
    first = bench.prepare(SessionMode.HACK)
    assert first.failed_stage is PreparationStage.FLASHING

    bench.flasher.success = True
    second = bench.prepare(SessionMode.HACK)

    assert second.success
    assert bench.transitions() == _full_run()
    assert len(bench.compiler.sources) == 2  # compiled again, not reused
    first_dir, second_dir = bench.compiler.sketch_dirs
    assert first_dir != second_dir


# --- 8. a candidate never becomes the next baseline -------------------------


def _gate(command: str, call: str) -> ConditionalStatement:
    return ConditionalStatement(
        condition=ComparisonValue(
            left=SymbolValue(name="message"),
            operator="==",
            right=LiteralValue(value=command, value_type=SemanticType.TEXT),
        ),
        body=(CallStatement(function_name=call),),
    )


def test_a_flashed_candidate_is_not_the_next_sessions_baseline() -> None:
    bench = Bench()
    baseline = BASELINE_INO.read_text(encoding="utf-8")

    # A previous participant's Build Mode session: the panel's project,
    # remediated through the real Blockly write path, compiled and FLASHED.
    selection = BuildProjectSelector(resources=bench.resources).select_for(
        asyncio.run(bench.resources.refresh())
    )
    session = BuildSession(session_id="previous-participant", workspace=selection.workspace)
    current = program_for_source(session.workspace.full_source(SKETCH_NAME)).section(SECURITY)
    remediated = SemanticSection(
        section_id=SECURITY,
        operation=current.operation,
        statements=(
            _gate("START PANEL1-CMD-AUTH-K7", "motorStart"),
            _gate("STOP PANEL1-CMD-AUTH-K7", "motorStop"),
        ),
    )
    payload = program_to_blockly(SemanticProgram(sections=(remediated,))).section(SECURITY)
    representation = payload.to_representation()
    session.workspace.apply_section_blockly(
        SKETCH_NAME, SECURITY, representation["workspace"], representation["preserved"]
    )
    candidate = session.workspace.full_source(SKETCH_NAME)
    assert candidate != baseline

    build = BuildService(compiler=bench.compiler, flasher=bench.flasher, monitor=bench.monitor)
    asyncio.run(build.compile_workspace(session))
    asyncio.run(build.flash_workspace(session))
    assert bench.flasher.flashed[-1] == ("COM7", candidate)

    # The next participant enters a mode: the baseline goes back on.
    assert bench.prepare(SessionMode.HACK).success
    assert bench.compiler.sources[-1] == baseline
    assert bench.flasher.flashed[-1] == ("COM7", baseline)
    # And the courseware on disk was never touched by the candidate.
    assert BASELINE_INO.read_text(encoding="utf-8") == baseline


# --- 9. package metadata, not a Panel 1 branch ------------------------------


def test_an_alternate_panel_is_prepared_from_its_own_package(tmp_path) -> None:
    alt_id = "alt-training-panel"
    alt_dir = tmp_path / alt_id
    shutil.copytree(PACKAGE_DIR, alt_dir)
    manifest = json.loads((alt_dir / "panel.json").read_text(encoding="utf-8"))
    manifest["panel_id"] = alt_id
    manifest["scenario"]["scenario_id"] = "alt-scenario"
    manifest["firmware"]["firmware_id"] = "alt-firmware"
    (alt_dir / "panel.json").write_text(json.dumps(manifest), encoding="utf-8")
    sketch = alt_dir / "firmware" / "smart_home_mqtt_control" / SKETCH_NAME
    alt_source = sketch.read_text(encoding="utf-8").replace(
        "Panel 1 vulnerable firmware", "ALTERNATE PANEL baseline firmware", 1
    )
    sketch.write_text(alt_source, encoding="utf-8")

    alt_mac = "02:00:00:00:00:02"
    bench = Bench(
        mac=alt_mac,
        panel_registry=PanelRegistry(
            [
                PanelDefinition(
                    panel_id=alt_id,
                    display_name="Alternate Panel",
                    mac_addresses=(alt_mac,),
                    package_id=alt_id,
                )
            ]
        ),
        loader=PanelPackageLoader(root=tmp_path),
        scenario_registry=ScenarioRegistry({"alt-scenario": EnvironmentalMonitoringScenario}),
    )

    result = bench.prepare(SessionMode.HACK)
    assert result.success, result
    assert result.data["panel_id"] == alt_id
    assert result.data["firmware_id"] == "alt-firmware"
    assert result.data["scenario_id"] == "alt-scenario"
    assert bench.compiler.sources == [alt_source]
    assert bench.flasher.flashed == [("COM7", alt_source)]


def test_the_preparation_layer_names_no_panel() -> None:
    for module in ("mode_preparation.py", "preparation_websocket.py"):
        source = (BACKEND / "app" / module).read_text(encoding="utf-8").lower()
        for forbidden in (PANEL_ONE, PANEL_ONE_MAC, "panel1", "panel_1", "smart_home", "smart-home"):
            assert forbidden not in source, (module, forbidden)
        # No branch on an identity: nothing naming a panel, MAC, package or
        # scenario is ever compared to a literal (enums and tables only).
        tree = ast.parse((BACKEND / "app" / module).read_text(encoding="utf-8"))
        identity = ("panel", "mac", "package", "scenario", "firmware")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            if any(isinstance(o, ast.Constant) and isinstance(o.value, str) for o in operands):
                text = ast.unparse(node).lower()
                assert not any(word in text for word in identity), (module, text)


def test_the_preparation_layer_spawns_nothing_itself() -> None:
    """It composes the adapters; `app/build/process.py` stays the one spawner."""
    for module in ("mode_preparation.py", "preparation_websocket.py"):
        tree = ast.parse((BACKEND / "app" / module).read_text(encoding="utf-8"))
        imported = set()
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        forbidden = {"subprocess", "app.build.process", "run_capture", "create_subprocess_exec", "system"}
        assert not (imported | used) & forbidden, module


# --- the /ws/prepare transport ------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    from app import mode_preparation
    from app.main import app

    holder = {}

    def install(bench: Bench) -> None:
        monkeypatch.setattr(mode_preparation, "default_preparation_service", bench.service)
        holder["bench"] = bench

    with TestClient(app) as test_client:
        test_client.install = install
        yield test_client


def _drain(ws) -> tuple[list[dict], dict]:
    stages = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "result":
            return stages, frame
        assert frame["type"] == "stage", frame
        stages.append(frame)


def test_the_socket_streams_real_stages_then_one_result(client) -> None:
    bench = Bench()
    client.install(bench)
    with client.websocket_connect("/ws/prepare") as ws:
        ws.send_json({"type": "prepare", "mode": "hack"})
        stages, result = _drain(ws)

    assert [(s["stage"], s["status"]) for s in stages] == _full_run()
    assert result["success"] is True
    assert result["mode"] == "hack"
    assert result["failed_stage"] is None
    assert result["data"]["panel_id"] == PANEL_ONE


def test_the_socket_reports_the_failed_stage_and_no_ready(client) -> None:
    client.install(Bench(flashes=False))
    with client.websocket_connect("/ws/prepare") as ws:
        ws.send_json({"type": "prepare", "mode": "build"})
        stages, result = _drain(ws)

    assert result["success"] is False
    assert result["failed_stage"] == "flashing"
    assert stages[-1] == {**stages[-1], "stage": "flashing", "status": "failed"}
    assert all(s["stage"] != "ready" for s in stages)


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "prepare", "mode": "hack", "panel_id": PANEL_ONE},
        {"type": "prepare", "mode": "hack", "port": "COM7"},
        {"type": "prepare", "mode": "dashboard"},
        {"type": "prepare"},
        {"type": "compile"},
    ],
)
def test_the_client_can_only_choose_a_mode(client, frame) -> None:
    bench = Bench()
    client.install(bench)
    with client.websocket_connect("/ws/prepare") as ws:
        ws.send_json(frame)
        assert ws.receive_json()["type"] == "error"
    assert bench.compiler.sources == [] and bench.flasher.flashed == []
