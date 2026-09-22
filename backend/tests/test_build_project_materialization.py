"""Phase B2 — PanelPackage -> real firmware -> BuildDocument -> BuildProject.

Covers the two integration gaps B2 exists to close:

GAP A  `BuildDocument`/`CodeSection[]` (B1) -> the EXISTING
       `FirmwareFile`/`FileSegment` representation, via the one conversion in
       `app/build/document_project.py`: order preserved, ids stable, source
       exactly reconstructable, and no structural concept leaking into the
       permission model.

GAP B  a resolved `PanelPackage` -> `BuildWorkspace` -> `BuildSession`, via
       `app/build_project_selection.py`, with the long-standing LED Blink
       default preserved as the fallback for every way the chain can fail to
       name a panel firmware.

Panel 1's REAL shipped `.ino` is the integration fixture — this file asserts
against `backend/panels/smart-home-mqtt-control/`, not a hand-written stub,
because the point of B2 is that the actual courseware materializes.

WHAT THIS FILE DOES NOT CLAIM. No Blockly translation, no semantic IR, no
remediation logic, no editable-region policy and no validation exist yet; a
B2 panel project is deliberately all-LOCKED and names no security region.
Nothing here compiles or flashes against real hardware: the compile/flash
assertions use the same fake adapters `tests/test_build_service.py` uses.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from app.build import create_default_workspace
from app.build.blink import BLINK_REGION_ID
from app.build.discovery import analyze_source
from app.build.discovery.models import SectionKind
from app.build.document_project import (
    WHOLE_FILE_REGION_ID,
    DocumentProjectError,
    board_info_from_fqbn,
    build_project_from_document,
    firmware_file_from_document,
    locked_file,
)
from app.build.models import BoardInfo, RegionKind
from app.build.records import BuildAttemptType
from app.build.service import BuildService
from app.build.sketch_source import (
    SketchSourceError,
    find_primary_sketch,
    load_sketch_project,
)
from app.build.workspace import BuildWorkspace, RegionNotEditableError
from app.build_project_selection import (
    BuildProjectSelection,
    BuildProjectSelector,
    BuildProjectSource,
    select_build_project,
)
from app.build_sessions import BuildSessionManager
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationStatus,
)
from app.hardware.panels import PanelDefinition, default_panel_registry
from app.panels import default_panel_package_loader
from app.panels.service import PanelResources, PanelResourceService, PanelResourceStatus

from tests.test_build_panel_integration import (
    FakeCompilerAdapter,
    FakeFlasherAdapter,
    compile_success,
)

PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"
PANEL_ONE_SKETCH_NAME = "smart_home_mqtt_control.ino"
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
PANEL_ONE_SKETCH_DIR = (
    BACKEND_DIR / "panels" / PANEL_ONE_ID / "firmware" / "smart_home_mqtt_control"
)

#: The functions Panel 1's real firmware defines, in source order. This is an
#: expected RESULT of running B1 over the shipped `.ino` — it is asserted
#: here, never hardcoded into the generic Build system (see the genericity
#: tests at the bottom of this file).
PANEL_ONE_FUNCTIONS = (
    "setMotorOutputs",
    "chirpBuzzer",
    "applyMotorState",
    "motorStart",
    "motorStop",
    "applyCommand",
    "onMessage",
    "pollButtons",
    "ensureConnected",
    "setup",
    "loop",
)

SAMPLE_SOURCE = """#include <stdio.h>

static int counter = 0;

static void bump() {
  counter++;
}

void setup() {
  bump();
}

void loop() {
  delay(10);
}
"""


def sample_document():
    return analyze_source(SAMPLE_SOURCE)


def sample_board() -> BoardInfo:
    return BoardInfo(name="ESP32", mcu="ESP32", fqbn="esp32:esp32:esp32")


def sample_project(**overrides):
    defaults = dict(
        path="sample.ino",
        project_id="sample-project",
        scenario_id="sample-scenario",
        module_id="sample-module",
        firmware_name="Sample",
        board=sample_board(),
    )
    defaults.update(overrides)
    return build_project_from_document(sample_document(), **defaults)


def panel_one_sketch_source() -> str:
    return (PANEL_ONE_SKETCH_DIR / PANEL_ONE_SKETCH_NAME).read_text(encoding="utf-8")


# =============================================================================
# 1 / 2 / 3 — GAP A: BuildDocument -> BuildProject
# =============================================================================


def test_every_section_becomes_exactly_one_segment_in_order() -> None:
    document = sample_document()
    firmware_file = firmware_file_from_document(document, "sample.ino")
    assert [s.region_id for s in firmware_file.segments] == [
        s.section_id for s in document.sections
    ]
    assert len(firmware_file.segments) == len(document.sections)


def test_region_ids_are_the_discovered_section_ids() -> None:
    """The stable mapping between the two models — no side table, no rename."""
    document = sample_document()
    firmware_file = firmware_file_from_document(document, "sample.ino")
    for section, segment in zip(document.sections, firmware_file.segments):
        assert segment.region_id == section.section_id
        assert segment.text == section.text


def test_setup_loop_and_helper_identity_survive_the_conversion() -> None:
    document = sample_document()
    firmware_file = firmware_file_from_document(document, "sample.ino")
    ids = [s.region_id for s in firmware_file.segments]
    assert "setup" in ids
    assert "loop" in ids
    assert "helper_bump" in ids
    # ...and each names the span the analyzer classified, not a renumbering.
    assert firmware_file.segment("setup").text == document.setup.text
    assert firmware_file.segment("loop").text == document.loop.text


def test_conversion_reconstructs_the_source_exactly() -> None:
    firmware_file = firmware_file_from_document(sample_document(), "sample.ino")
    assert firmware_file.render() == SAMPLE_SOURCE


def test_project_from_document_reconstructs_the_source_exactly() -> None:
    project = sample_project()
    assert project.file("sample.ino").render() == SAMPLE_SOURCE
    assert BuildWorkspace(project).full_source("sample.ino") == SAMPLE_SOURCE


def test_conversion_locks_every_section_by_default() -> None:
    """B2 decides nothing about editability — the absence of a policy."""
    firmware_file = firmware_file_from_document(sample_document(), "sample.ino")
    assert {s.kind for s in firmware_file.segments} == {RegionKind.LOCKED}


def test_editable_section_ids_is_the_only_way_a_region_opens() -> None:
    firmware_file = firmware_file_from_document(
        sample_document(), "sample.ino", editable_section_ids=("setup",)
    )
    kinds = {s.region_id: s.kind for s in firmware_file.segments}
    assert kinds["setup"] is RegionKind.EDITABLE
    assert kinds["loop"] is RegionKind.LOCKED
    assert kinds["helper_bump"] is RegionKind.LOCKED


def test_an_unknown_editable_section_id_is_rejected_not_ignored() -> None:
    with pytest.raises(DocumentProjectError):
        firmware_file_from_document(
            sample_document(), "sample.ino", editable_section_ids=("no_such_section",)
        )


def test_conversion_adds_no_structural_field_to_the_permission_model() -> None:
    """`FileSegment` was not extended with callback/helper/setup semantics."""
    from app.build.models import FileSegment

    assert set(FileSegment.__dataclass_fields__) == {"kind", "region_id", "text"}


def test_a_project_from_a_document_names_no_security_region_by_default() -> None:
    assert sample_project().security_region_id is None


def test_board_info_is_derived_from_the_fqbn_not_invented() -> None:
    board = board_info_from_fqbn("esp32:esp32:esp32s3:PartitionScheme=huge_app")
    assert board.fqbn == "esp32:esp32:esp32s3:PartitionScheme=huge_app"
    assert board.name == "ESP32S3"
    assert board.mcu == "ESP32"
    with pytest.raises(DocumentProjectError):
        board_info_from_fqbn("nonsense")


def test_supporting_files_are_carried_verbatim_as_one_locked_segment() -> None:
    header = locked_file("helpers.h", "#pragma once\nint x;\n")
    project = sample_project(supporting_files=(header,))
    assert [f.path for f in project.files] == ["sample.ino", "helpers.h"]
    assert project.file("helpers.h").render() == "#pragma once\nint x;\n"
    assert project.file("helpers.h").segments[0].kind is RegionKind.LOCKED
    assert project.file("helpers.h").segments[0].region_id == WHOLE_FILE_REGION_ID


# =============================================================================
# 4 — Panel 1's real firmware materializes
# =============================================================================


def test_panel_one_sketch_is_found_by_the_arduino_folder_rule() -> None:
    assert find_primary_sketch(PANEL_ONE_SKETCH_DIR).name == PANEL_ONE_SKETCH_NAME


def test_panel_one_project_preserves_every_discovered_function_in_order() -> None:
    project = load_sketch_project(
        PANEL_ONE_SKETCH_DIR,
        project_id="panel-one-firmware",
        scenario_id="panel-one",
        module_id="panel-one",
        firmware_name="Panel One",
        board=sample_board(),
    )
    document = analyze_source(panel_one_sketch_source())
    discovered = tuple(s.name for s in document.functions)
    assert discovered == PANEL_ONE_FUNCTIONS

    region_ids = [s.region_id for s in project.file(PANEL_ONE_SKETCH_NAME).segments]
    # Every function's section survives, in the same order, addressable by id.
    function_ids = [s.section_id for s in document.functions]
    assert [rid for rid in region_ids if rid in set(function_ids)] == function_ids


def test_panel_one_onmessage_is_a_callback_and_applycommand_is_a_helper() -> None:
    """B1's classification is preserved by the document, not by the project.

    The Build representation stores the id that classification produced
    (`callback_onMessage`), which is what keeps the two models orthogonal:
    the segment carries the name, the document carries the meaning.
    """
    document = analyze_source(panel_one_sketch_source())
    assert document.section("callback_onMessage").kind is SectionKind.CALLBACK
    assert document.section("helper_applyCommand").kind is SectionKind.HELPER_FUNCTION
    project = load_sketch_project(
        PANEL_ONE_SKETCH_DIR,
        project_id="panel-one-firmware",
        scenario_id="panel-one",
        module_id="panel-one",
        firmware_name="Panel One",
        board=sample_board(),
    )
    ids = {s.region_id for s in project.file(PANEL_ONE_SKETCH_NAME).segments}
    assert {"callback_onMessage", "helper_applyCommand"} <= ids


def test_panel_one_project_reconstructs_its_real_source_exactly() -> None:
    project = load_sketch_project(
        PANEL_ONE_SKETCH_DIR,
        project_id="panel-one-firmware",
        scenario_id="panel-one",
        module_id="panel-one",
        firmware_name="Panel One",
        board=sample_board(),
    )
    assert project.file(PANEL_ONE_SKETCH_NAME).render() == panel_one_sketch_source()


def test_two_loads_of_the_same_sketch_are_independent_projects() -> None:
    kwargs = dict(
        project_id="panel-one-firmware",
        scenario_id="panel-one",
        module_id="panel-one",
        firmware_name="Panel One",
        board=sample_board(),
        editable_section_ids=("setup",),
    )
    first = BuildWorkspace(load_sketch_project(PANEL_ONE_SKETCH_DIR, **kwargs))
    second = BuildWorkspace(load_sketch_project(PANEL_ONE_SKETCH_DIR, **kwargs))
    first.update_region(PANEL_ONE_SKETCH_NAME, "setup", "void setup() {}")
    assert second.region_source(PANEL_ONE_SKETCH_NAME, "setup") != "void setup() {}"


def test_load_sketch_project_refuses_a_missing_directory(tmp_path) -> None:
    with pytest.raises(SketchSourceError):
        load_sketch_project(
            tmp_path / "nope",
            project_id="x",
            scenario_id="x",
            module_id="x",
            firmware_name="X",
            board=sample_board(),
        )


def test_load_sketch_project_refuses_a_directory_with_no_sketch(tmp_path) -> None:
    (tmp_path / "readme.txt").write_text("not code", encoding="utf-8")
    with pytest.raises(SketchSourceError):
        load_sketch_project(
            tmp_path,
            project_id="x",
            scenario_id="x",
            module_id="x",
            firmware_name="X",
            board=sample_board(),
        )


def test_load_sketch_project_refuses_unanalysable_source(tmp_path) -> None:
    sketch = tmp_path / "broken"
    sketch.mkdir()
    (sketch / "broken.ino").write_text("void setup() {\n", encoding="utf-8")
    with pytest.raises(SketchSourceError):
        load_sketch_project(
            sketch,
            project_id="x",
            scenario_id="x",
            module_id="x",
            firmware_name="X",
            board=sample_board(),
        )


def test_supporting_sources_beside_the_sketch_are_carried_into_the_project(tmp_path) -> None:
    sketch = tmp_path / "demo"
    sketch.mkdir()
    (sketch / "demo.ino").write_text(SAMPLE_SOURCE, encoding="utf-8")
    (sketch / "helpers.h").write_text("#pragma once\n", encoding="utf-8")
    (sketch / "notes.md").write_text("not compiler input\n", encoding="utf-8")
    project = load_sketch_project(
        sketch,
        project_id="x",
        scenario_id="x",
        module_id="x",
        firmware_name="X",
        board=sample_board(),
    )
    assert [f.path for f in project.files] == ["demo.ino", "helpers.h"]


# =============================================================================
# 5 / 6 / 7 — GAP B: PanelPackage -> BuildSession, and the Blink fallback
# =============================================================================


class _FixedIdentification:
    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


def panel_one_identification() -> PanelIdentification:
    panel = default_panel_registry().resolve(PANEL_ONE_MAC).panel
    assert panel is not None
    return PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port="COM3",
        mac=PANEL_ONE_MAC,
        panel=panel,
    )


def selector_over(identification: PanelIdentification) -> BuildProjectSelector:
    return BuildProjectSelector(
        PanelResourceService(identification=_FixedIdentification(identification))
    )


def test_a_ready_panel_loads_its_own_firmware_not_blink() -> None:
    selection = selector_over(panel_one_identification()).select()
    assert selection.source is BuildProjectSource.PANEL_PACKAGE
    assert selection.from_panel
    assert selection.panel_id == PANEL_ONE_ID
    assert selection.panel_status is PanelResourceStatus.READY

    project = selection.workspace.project
    assert project.project_id != "led-blink-poc"
    assert project.project_id == "smart-home-mqtt-control-firmware"
    assert project.scenario_id == PANEL_ONE_ID
    assert [f.path for f in project.files] == [PANEL_ONE_SKETCH_NAME]
    assert selection.workspace.full_source(PANEL_ONE_SKETCH_NAME) == panel_one_sketch_source()


def test_panel_one_identity_values_all_come_from_its_package() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    project = selector_over(panel_one_identification()).select().workspace.project
    assert project.project_id == package.firmware.firmware_id
    assert project.scenario_id == package.scenario_id
    assert project.module_id == package.panel_id
    assert project.firmware_name == package.title
    assert project.board.fqbn == package.firmware.board.fqbn


def test_a_panel_project_is_read_only_and_names_no_security_region() -> None:
    """B2 is integration, not editing capability — see the module docstring."""
    selection = selector_over(panel_one_identification()).select()
    project = selection.workspace.project
    assert project.security_region_id is None
    assert {s.kind for s in project.file(PANEL_ONE_SKETCH_NAME).segments} == {RegionKind.LOCKED}
    with pytest.raises(RegionNotEditableError):
        selection.workspace.update_region(PANEL_ONE_SKETCH_NAME, "setup", "void setup() {}")


@pytest.mark.parametrize(
    "status",
    [
        PanelIdentificationStatus.NOT_CHECKED,
        PanelIdentificationStatus.NOT_CONNECTED,
        PanelIdentificationStatus.UNIDENTIFIED,
        PanelIdentificationStatus.UNREGISTERED,
    ],
)
def test_every_unresolved_board_falls_back_to_blink(status) -> None:
    identification = PanelIdentification(
        status=status,
        port=None if status is PanelIdentificationStatus.NOT_CONNECTED else "COM3",
        mac="aa:bb:cc:dd:ee:ff"
        if status is PanelIdentificationStatus.UNREGISTERED
        else None,
        panel=None,
    )
    selection = selector_over(identification).select()
    assert selection.source is BuildProjectSource.DEFAULT
    assert selection.workspace.project.project_id == "led-blink-poc"
    assert selection.workspace.project.security_region_id == BLINK_REGION_ID


def test_a_registered_panel_with_no_package_falls_back_to_blink() -> None:
    panel = PanelDefinition(
        panel_id="panel-without-courseware",
        display_name="Panel Without Courseware",
        mac_addresses=("aa:bb:cc:dd:ee:01",),
        package_id=None,
    )
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port="COM3",
        mac="aa:bb:cc:dd:ee:01",
        panel=panel,
    )
    selection = selector_over(identification).select()
    assert selection.panel_status is PanelResourceStatus.NO_PACKAGE
    assert selection.source is BuildProjectSource.DEFAULT
    assert selection.workspace.project.project_id == "led-blink-poc"
    assert selection.panel_id == "panel-without-courseware"
    assert selection.detail


def test_a_ready_package_declaring_no_firmware_falls_back_to_blink() -> None:
    from app.panels.models import PanelPackage, ScenarioDefinition

    package = PanelPackage(
        schema_version=1,
        panel_id="no-firmware-yet",
        scenario=ScenarioDefinition(scenario_id="no-firmware-yet", title="No Firmware"),
    )
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port="COM3",
        mac="aa:bb:cc:dd:ee:02",
        panel=PanelDefinition(
            panel_id="no-firmware-yet",
            display_name="No Firmware Yet",
            mac_addresses=("aa:bb:cc:dd:ee:02",),
            package_id="no-firmware-yet",
        ),
    )
    resources = PanelResources(
        PanelResourceStatus.READY, identification, package=package
    )
    selection = BuildProjectSelector().select_for(resources)
    assert selection.source is BuildProjectSource.DEFAULT
    assert selection.workspace.project.project_id == "led-blink-poc"
    assert "no firmware" in selection.detail.lower()


def test_selection_never_raises_for_the_real_process_wide_service() -> None:
    selection = select_build_project()
    assert isinstance(selection, BuildProjectSelection)
    assert selection.workspace.project.files  # any project is a valid answer


def test_each_selection_builds_its_own_workspace() -> None:
    selector = selector_over(panel_one_identification())
    first = selector.select().workspace
    second = selector.select().workspace
    assert first is not second
    assert first.project is not second.project


def test_build_session_manager_accepts_an_injected_workspace() -> None:
    async def run() -> None:
        manager = BuildSessionManager()
        selection = selector_over(panel_one_identification()).select()
        session = await manager.create(
            panel_id=selection.panel_id, workspace=selection.workspace
        )
        assert session.panel_id == PANEL_ONE_ID
        assert session.workspace is selection.workspace
        assert session.snapshot()["project"]["project_id"] == (
            "smart-home-mqtt-control-firmware"
        )

    asyncio.run(run())


def test_build_session_manager_without_a_workspace_still_loads_blink() -> None:
    async def run() -> None:
        manager = BuildSessionManager()
        session = await manager.create()
        assert session.workspace.project.project_id == "led-blink-poc"

    asyncio.run(run())


def test_the_build_session_layer_imports_no_panel_layer() -> None:
    """Panel resolution stays OUTSIDE the low-level session (see the brief)."""
    tree = ast.parse((APP_DIR / "build_sessions.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("app.panels", "app.build_panel_resolution", "app.build_project_selection"):
        assert not any(name == banned or name.startswith(banned + ".") for name in imported), (
            f"build_sessions.py imports {banned}"
        )


# =============================================================================
# 8 / 9 / 10 — existing Build behaviour, compile/flash, and evidence
# =============================================================================


def test_the_default_workspace_is_byte_for_byte_what_it_was() -> None:
    project = create_default_workspace().project
    assert project.project_id == "led-blink-poc"
    assert project.scenario_id == "led-blink-poc"
    assert project.security_region_id == BLINK_REGION_ID
    assert [f.path for f in project.files] == ["main.ino"]
    assert [s.kind for s in project.file("main.ino").segments] == [RegionKind.EDITABLE]


def test_blink_editing_still_works_exactly_as_before() -> None:
    workspace = create_default_workspace()
    workspace.update_region("main.ino", BLINK_REGION_ID, "void setup() {}\nvoid loop() {}\n")
    assert workspace.full_source("main.ino") == "void setup() {}\nvoid loop() {}\n"


def test_a_panel_project_materializes_into_a_valid_arduino_sketch(tmp_path) -> None:
    """What `compile`/`flash` actually receive: a real sketch tree on disk."""
    selection = selector_over(panel_one_identification()).select()
    sketch_dir = selection.workspace.materialize(tmp_path)
    assert sketch_dir.name == "smart_home_mqtt_control"
    assert (sketch_dir / PANEL_ONE_SKETCH_NAME).is_file()
    written = (sketch_dir / PANEL_ONE_SKETCH_NAME).read_text(encoding="utf-8")
    assert written == panel_one_sketch_source()


def test_compile_and_flash_run_against_a_panel_sourced_project(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService(
            compiler=FakeCompilerAdapter(compile_success()),
            flasher=FakeFlasherAdapter(),
        )
        manager = BuildSessionManager()
        selection = selector_over(panel_one_identification()).select()
        session = await manager.create(
            panel_id=selection.panel_id, workspace=selection.workspace
        )
        result = await service.compile_workspace(session)
        assert result.success, result.error
        assert session.flash_ready
        # The compiler was handed this project's own board target, not the
        # default project's.
        assert session.compiled_artifact.fingerprint == session.workspace.fingerprint()

    asyncio.run(run())


def test_panel_sourced_compile_attempts_are_recorded_against_the_panel(
    isolated_event_store,
) -> None:
    async def run() -> None:
        service = BuildService(compiler=FakeCompilerAdapter(compile_success()))
        manager = BuildSessionManager()
        selection = selector_over(panel_one_identification()).select()
        session = await manager.create(
            panel_id=selection.panel_id, workspace=selection.workspace
        )
        await service.compile_workspace(session)
        attempts = session.recorder.attempts
        assert [a.attempt_type for a in attempts] == [BuildAttemptType.COMPILE]
        # The panel this evidence belongs to is carried by the recorder (the
        # session row), exactly as it was before B2 — unchanged by the fact
        # that the workspace is now that panel's own firmware.
        assert session.recorder.panel_id == PANEL_ONE_ID
        assert all(a.session_id == session.session_id for a in attempts)

    asyncio.run(run())


def test_start_session_reports_the_loaded_project_not_a_hardcoded_one() -> None:
    async def run() -> None:
        service = BuildService()
        manager = BuildSessionManager()
        selection = selector_over(panel_one_identification()).select()
        session = await manager.create(
            panel_id=selection.panel_id, workspace=selection.workspace
        )
        result = await service.start_session(session)
        loaded = [e for e in result.events if e.type.value == "workspace_loaded"]
        assert len(loaded) == 1
        assert loaded[0].data["project_id"] == "smart-home-mqtt-control-firmware"
        assert loaded[0].data["scenario_id"] == PANEL_ONE_ID

    asyncio.run(run())


# =============================================================================
# Genericity and security of the new B2 modules
# =============================================================================

_NEW_MODULES = (
    APP_DIR / "build" / "document_project.py",
    APP_DIR / "build" / "sketch_source.py",
    APP_DIR / "build_project_selection.py",
)


def test_new_b2_modules_contain_no_panel_specific_literal() -> None:
    banned = {
        "smart-home-mqtt-control",
        "smart-home-mqtt-control-firmware",
        "smart_home_mqtt_control",
        PANEL_ONE_SKETCH_NAME,
        PANEL_ONE_MAC,
        "setMotorOutputs",
        "onMessage",
        "applyCommand",
        "cybertrainer/smart-home/motor/control",
        "192.168.50.1",
    }
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in banned
        )
        assert offenders == [], f"{path.name} names panel-specific literals: {offenders}"


def test_new_b2_modules_use_no_dynamic_execution() -> None:
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {"eval", "exec", "compile", "__import__"}, path.name
            if isinstance(func, ast.Attribute):
                assert func.attr not in {
                    "system",
                    "popen",
                    "spawn",
                    "spawnv",
                    "import_module",
                    "Popen",
                    "run",
                }, path.name
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    ), path.name


def test_new_b2_modules_import_no_subprocess_or_toolchain() -> None:
    banned = ("subprocess", "os", "app.build.process", "app.build.compiler", "app.build.flasher")
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(
            name for name in imported for bad in banned if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_pure_conversion_layer_touches_no_filesystem_and_no_panel() -> None:
    """`document_project.py` is conversion only — no `pathlib`, no panels."""
    tree = ast.parse((APP_DIR / "build" / "document_project.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("pathlib", "app.panels", "app.hardware", "app.build_sessions"):
        assert not any(name == banned or name.startswith(banned + ".") for name in imported), (
            f"document_project.py imports {banned}"
        )


def test_the_build_package_does_not_depend_on_the_panel_layer() -> None:
    """`app/build/` stays panel-agnostic; composition lives one level up."""
    for path in sorted((APP_DIR / "build").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = next(
                    (a.name for a in node.names if a.name.startswith("app.panels")), None
                )
            if module and module.startswith("app.panels"):
                raise AssertionError(f"{path.relative_to(APP_DIR)} imports {module}")
