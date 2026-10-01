"""Panel 2 (Environmental Monitoring System) — Build Mode on its REAL firmware.

Panel 2 is a foundation module: real firmware, no vulnerability, no remediation.
Build Mode must still open that firmware as a Blockly-editable project, through
the SAME generic pipeline Panel 1 uses — no Panel-2-specific Build code exists
and none is tested here.

    PanelPackage (panel.json)  ->  BuildProjectSelector  ->  BuildProject
        -> discovery (B1) -> semantic IR (B3) -> Blockly (B4/B5) -> C++ (B6)
        -> BuildService.compile_workspace -> the real arduino-cli

Verified here:

A. The package declares real firmware and a section policy through the generic
   `build` block — and declares no remediation, vulnerability, security region,
   learning content or evaluation.
B. The selector builds a `BuildProject` from the shipped sketch, byte for byte,
   with exactly the expected regions and classification.
C. Locked regions are enforced, and the hardware constants live only in them.
D. The Blockly workspace initialises for every editable section, loads into
   the REAL Blockly, and an edit made there reaches the generated C++.
E. The generated C++ is valid, stable and compiles through `BuildService` with
   the real toolchain (skipped only when `arduino-cli` is absent).
F. The `/ws/build` connection path serves Panel 2 as an active project.
G. Panel 1, the production-serving surface and the database are untouched.

No board is touched: nothing is flashed, no port is opened, and the board
detector is a constructed identification. The compile tests write only to a
temporary directory.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import pathlib
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from app import config
from app.build import ArduinoCliCompiler, BuildWorkspace, CompileRequest
from app.build.compiler import default_compiler
from app.build.discovery import analyze_source
from app.build.models import RegionKind
from app.build.policy import InteractionPolicy
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import program_with_section, section_from_state
from app.build.service import BuildService
from app.build.workspace import RegionNotEditableError
from app.build_project_selection import (
    BuildProjectSelection,
    BuildProjectSelector,
    BuildProjectSource,
)
from app.build_sessions import BuildSession
from app.hardware.panel_identification import PanelIdentification, PanelIdentificationStatus
from app.hardware.panels import default_panel_registry
from app.main import app
from app.panels import (
    BuildDeclaration,
    PanelPackageInvalidError,
    PanelPackageLoader,
    PanelResourceService,
    PanelResourceStatus,
    default_panel_package_loader,
)
from tests.test_real_blockly import all_ids, blockly, pytestmark as needs_blockly  # noqa: F401

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANELS = BACKEND / "panels"
PANEL_TWO_ID = "environmental-monitoring"
PANEL_TWO_MAC = "20:50:0d:4d:4e:a8"
PANEL_ONE_ID = "smart-home-mqtt-control"
SKETCH_DIR = PANELS / PANEL_TWO_ID / "firmware" / "environmental_monitoring"
SKETCH_NAME = "environmental_monitoring.ino"
SKETCH = SKETCH_DIR / SKETCH_NAME

#: The sketch's discovered sections, in file order.
REGIONS = ["global", "setup", "loop", "helper_updateDisplay", "helper_showError", "global_2"]
EDITABLE = ["setup", "loop", "helper_updateDisplay", "helper_showError"]
LOCKED = ["global", "global_2"]

_REAL_ARDUINO_CLI = shutil.which(config.ARDUINO_CLI_PATH)
needs_toolchain = pytest.mark.skipif(
    _REAL_ARDUINO_CLI is None,
    reason=f"arduino-cli not available via config.ARDUINO_CLI_PATH={config.ARDUINO_CLI_PATH!r}",
)


class _FixedIdentification:
    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


def panel_two_resources():
    panel = default_panel_registry().resolve(PANEL_TWO_MAC).panel
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED, port="COM3", mac=PANEL_TWO_MAC, panel=panel
    )
    return PanelResourceService(
        identification=_FixedIdentification(identification),
        loader=default_panel_package_loader(),
    ).resolve()


def selection() -> BuildProjectSelection:
    return BuildProjectSelector().select_for(panel_two_resources())


def fresh_workspace() -> BuildWorkspace:
    """Panel 2's project exactly as a real Build connection builds it."""
    chosen = selection()
    assert chosen.from_panel, chosen.detail
    return chosen.workspace


def segments(workspace: BuildWorkspace) -> dict[str, str]:
    return {s.region_id: s.text for s in workspace.project.files[0].segments}


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def package():
    return default_panel_package_loader().load(PANEL_TWO_ID)


@pytest.fixture(scope="module")
def sketch_text() -> str:
    return SKETCH.read_text(encoding="utf-8")


# =============================================================================
# A. The package: real firmware + a policy, and nothing invented
# =============================================================================


def test_the_package_declares_the_build_section_policy(package) -> None:
    assert isinstance(package.build, BuildDeclaration)
    assert package.build.editable_section_ids == tuple(EDITABLE)
    assert package.build.explore_section_ids == ()


def test_the_package_declares_no_remediation_or_activity(package) -> None:
    """The foundation rule: a Build policy must not smuggle in courseware."""
    assert package.remediation is None
    assert package.learning.objectives == ()
    assert package.learning.activity_instructions == ()
    assert package.workflow == ()
    assert package.evaluation.objectives == ()
    assert package.evaluation.success_conditions == ()


def test_the_manifest_has_exactly_the_foundation_blocks() -> None:
    manifest = json.loads((PANELS / PANEL_TWO_ID / "panel.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema_version", "panel_id", "scenario", "firmware", "build"}
    assert set(manifest["build"]) == {"editable_section_ids"}


def test_a_package_may_not_declare_the_policy_in_both_places(tmp_path: pathlib.Path) -> None:
    """One list per panel: two could disagree and the loser would silently not apply."""
    from tests.test_panel_packages import valid_manifest, write_package

    manifest = valid_manifest("both-blocks")
    manifest["build"] = {"editable_section_ids": ["setup"]}
    manifest["remediation"] = {
        "vulnerability": "v",
        "remediation_goal": "g",
        "validation_requirement": "r",
        "editable_section_ids": ["loop"],
    }
    write_package(tmp_path, "both-blocks", manifest)

    with pytest.raises(PanelPackageInvalidError, match="both `build` and `remediation`"):
        PanelPackageLoader(root=tmp_path).load("both-blocks")


def test_a_prose_only_remediation_may_coexist_with_a_build_block(tmp_path: pathlib.Path) -> None:
    from tests.test_panel_packages import valid_manifest, write_package

    manifest = valid_manifest("prose-and-build")
    manifest["build"] = {"editable_section_ids": ["setup"]}
    manifest["remediation"] = {
        "vulnerability": "v",
        "remediation_goal": "g",
        "validation_requirement": "r",
    }
    write_package(tmp_path, "prose-and-build", manifest)

    loaded = PanelPackageLoader(root=tmp_path).load("prose-and-build")
    assert loaded.build.editable_section_ids == ("setup",)


@pytest.mark.parametrize(
    "block, message",
    [
        ({}, "no editable or explore"),
        ({"editable_section_ids": ["setup", "setup"]}, "repeats"),
        ({"editable_section_ids": ["not a section"]}, "invalid section id"),
        ({"editable_section_ids": ["loop"], "explore_section_ids": ["loop"]}, "both editable and explore"),
        ({"editable_section_ids": ["loop"], "security_section_id": "loop"}, "unknown field"),
    ],
)
def test_a_malformed_build_block_is_rejected_by_the_loader(
    tmp_path: pathlib.Path, block: dict, message: str
) -> None:
    from tests.test_panel_packages import valid_manifest, write_package

    manifest = valid_manifest("bad-build")
    manifest["build"] = block
    write_package(tmp_path, "bad-build", manifest)

    with pytest.raises(PanelPackageInvalidError, match=message):
        PanelPackageLoader(root=tmp_path).load("bad-build")


def test_a_prose_only_remediation_does_not_hide_the_build_policy() -> None:
    """The selector must read the one policy that exists: a remediation that
    names no sections carries none, so the `build` block is what applies."""
    import dataclasses

    from app.panels import RemediationDeclaration

    resources = panel_two_resources()
    prose_only = RemediationDeclaration(
        vulnerability="v", remediation_goal="g", validation_requirement="r"
    )
    assert prose_only.declares_section_policy is False
    package = dataclasses.replace(resources.package, remediation=prose_only)

    chosen = BuildProjectSelector().select_for(dataclasses.replace(resources, package=package))

    assert chosen.from_panel, chosen.detail
    assert chosen.workspace.project.policy.editable_section_ids == tuple(sorted(EDITABLE))
    assert chosen.workspace.project.security_region_id is None


# =============================================================================
# B. The BuildProject
# =============================================================================


def test_the_package_resolves_ready_and_selects_a_panel_project() -> None:
    resources = panel_two_resources()
    chosen = BuildProjectSelector().select_for(resources)

    assert resources.status is PanelResourceStatus.READY
    assert chosen.source is BuildProjectSource.PANEL_PACKAGE
    assert chosen.from_panel and chosen.has_active_project
    assert chosen.panel_id == PANEL_TWO_ID
    assert chosen.detail == ""


def test_the_project_identity_is_read_off_the_package(package) -> None:
    project = fresh_workspace().project

    assert project.project_id == package.firmware.firmware_id == "environmental-monitoring-firmware"
    assert project.scenario_id == package.scenario_id == "environmental-sensing"
    assert project.module_id == PANEL_TWO_ID
    assert project.firmware_name == "Environmental Monitoring System"
    assert project.board.fqbn == "esp32:esp32:esp32"
    assert [f.path for f in project.files] == [SKETCH_NAME]


def test_the_project_names_no_security_region() -> None:
    """A panel with no remediation has no remediation region."""
    project = fresh_workspace().project
    assert project.security_region_id is None
    assert project.policy.explore_section_ids == ()


def test_the_loaded_source_is_the_shipped_sketch_byte_for_byte(sketch_text: str) -> None:
    workspace = fresh_workspace()
    assert workspace.full_source(SKETCH_NAME) == sketch_text


def test_loading_does_not_touch_the_sketch_on_disk() -> None:
    before = sha256(SKETCH)
    workspace = fresh_workspace()
    workspace.full_source(SKETCH_NAME)
    assert sha256(SKETCH) == before


def test_the_expected_regions_exist_in_file_order() -> None:
    assert [s.region_id for s in fresh_workspace().project.files[0].segments] == REGIONS


def test_the_regions_are_the_ones_structural_discovery_finds(sketch_text: str) -> None:
    """Region ids are not hand-authored: they are B1's own, so a panel's policy
    cannot drift from the firmware."""
    discovered = [section.section_id for section in analyze_source(sketch_text).sections]
    assert discovered == REGIONS


def test_the_editable_regions_are_classified_exactly() -> None:
    project = fresh_workspace().project
    by_region = {s.region_id: s for s in project.files[0].segments}

    for region in EDITABLE:
        assert by_region[region].kind is RegionKind.EDITABLE, region
        assert project.section_policy(region) is InteractionPolicy.EDITABLE, region
    for region in LOCKED:
        assert by_region[region].kind is RegionKind.LOCKED, region
        assert project.section_policy(region) is InteractionPolicy.LOCKED, region


def test_no_region_is_an_explore_region() -> None:
    """EXPLORE means "read this to understand the vulnerability"; there is none."""
    project = fresh_workspace().project
    assert all(
        project.section_policy(s.region_id) is not InteractionPolicy.EXPLORE
        for s in project.files[0].segments
    )


def test_the_whole_sketch_is_neither_editable_nor_read_only() -> None:
    kinds = {s.kind for s in fresh_workspace().project.files[0].segments}
    assert kinds == {RegionKind.EDITABLE, RegionKind.LOCKED}


def test_every_build_attempt_gets_an_independent_project() -> None:
    first, second = fresh_workspace(), fresh_workspace()
    assert first is not second
    assert first.project is not second.project


# --- the state frame a client renders ----------------------------------------


def test_the_snapshot_carries_the_policy_the_ui_renders() -> None:
    snapshot = fresh_workspace().snapshot()

    assert snapshot["project"]["security_region_id"] is None
    assert snapshot["project"]["policy"]["editable_section_ids"] == sorted(EDITABLE)
    assert snapshot["project"]["policy"]["explore_section_ids"] == []
    rendered = {
        seg["region_id"]: (seg["kind"], seg["policy"])
        for seg in snapshot["files"][SKETCH_NAME]["segments"]
    }
    assert rendered == {
        **{region: ("editable", "editable") for region in EDITABLE},
        **{region: ("locked", "locked") for region in LOCKED},
    }


# =============================================================================
# C. Locked regions are enforced; the hardware constants live only in them
# =============================================================================

#: Every fact the real firmware pins down. Each must be in the loaded source.
HARDWARE_FACTS = {
    "DHT11 on GPIO 4": r"#define\s+DHTPIN\s+4\b",
    "DHT11 sensor type": r"#define\s+DHTTYPE\s+DHT11\b",
    "LED on GPIO 5": r"#define\s+LED_PIN\s+5\b",
    "fan driver on GPIO 25": r"#define\s+FAN_PIN\s+25\b",
    "OLED address 0x3C": r"#define\s+SCREEN_ADDRESS\s+0x3C\b",
    "OLED 128 wide": r"#define\s+SCREEN_WIDTH\s+128\b",
    "OLED 64 high": r"#define\s+SCREEN_HEIGHT\s+64\b",
    "threshold 31.0 C": r"TEMP_THRESHOLD\s*=\s*31\.0\s*;",
    "sensor interval 2000 ms": r"READ_INTERVAL\s*=\s*2000\s*;",
    "LED blink interval 500 ms": r"BLINK_INTERVAL\s*=\s*500\s*;",
    "serial 115200": r"Serial\.begin\(\s*115200\s*\)",
    "OLED SDA on GPIO 21": r"SSD1306 SDA\s*->\s*GPIO 21",
    "OLED SCL on GPIO 22": r"SSD1306 SCL\s*->\s*GPIO 22",
}


@pytest.mark.parametrize("fact", HARDWARE_FACTS)
def test_the_project_source_preserves_every_hardware_fact(fact: str) -> None:
    assert re.search(HARDWARE_FACTS[fact], fresh_workspace().full_source(SKETCH_NAME)), fact


def test_the_configuration_constants_sit_in_the_locked_global_region() -> None:
    workspace = fresh_workspace()
    configuration = workspace.region_source(SKETCH_NAME, "global")

    for fact in (
        "DHT11 on GPIO 4",
        "LED on GPIO 5",
        "fan driver on GPIO 25",
        "OLED address 0x3C",
        "threshold 31.0 C",
        "sensor interval 2000 ms",
        "LED blink interval 500 ms",
        "OLED SDA on GPIO 21",
        "OLED SCL on GPIO 22",
    ):
        assert re.search(HARDWARE_FACTS[fact], configuration), fact


@pytest.mark.parametrize("region", LOCKED)
def test_a_locked_region_refuses_a_text_edit_and_stays_unchanged(region: str) -> None:
    workspace = fresh_workspace()
    before = workspace.full_source(SKETCH_NAME)

    with pytest.raises(RegionNotEditableError):
        workspace.update_region(SKETCH_NAME, region, "// tampered\n")

    assert workspace.full_source(SKETCH_NAME) == before


@pytest.mark.parametrize("region", LOCKED)
def test_a_locked_region_refuses_a_blockly_edit_before_reading_it(region: str) -> None:
    workspace = fresh_workspace()
    before = workspace.full_source(SKETCH_NAME)

    with pytest.raises(RegionNotEditableError):
        workspace.apply_section_blockly(SKETCH_NAME, region, {"not": "even a workspace"})

    assert workspace.full_source(SKETCH_NAME) == before


# =============================================================================
# D. The Blockly workspace
# =============================================================================

TOP_BLOCK = {
    "setup": "arduino_setup",
    "loop": "arduino_loop",
    "helper_updateDisplay": "function_implementation",
    "helper_showError": "function_implementation",
}


@pytest.mark.parametrize("region", EDITABLE)
def test_every_editable_section_initialises_as_blocks(region: str) -> None:
    opened = fresh_workspace().section_blockly(SKETCH_NAME, region)

    assert opened["sectionId"] == region
    assert opened["representable"] is True
    top = opened["workspace"]["blocks"]["blocks"]
    assert [block["type"] for block in top] == [TOP_BLOCK[region]]


@pytest.mark.parametrize("region", LOCKED)
def test_a_locked_declaration_run_is_readable_but_not_drawn(region: str) -> None:
    """Reading is allowed for any policy; a run of declarations has no block form."""
    opened = fresh_workspace().section_blockly(SKETCH_NAME, region)
    assert opened["representable"] is False


def test_opening_a_section_changes_nothing() -> None:
    workspace = fresh_workspace()
    before = workspace.fingerprint()
    for region in REGIONS:
        workspace.section_blockly(SKETCH_NAME, region)
    assert workspace.fingerprint() == before


def test_the_loop_opens_with_its_unsupported_logic_preserved_read_only() -> None:
    """The sensing/threshold logic is C++ the toolbox has no vocabulary for yet;
    it is carried verbatim as preserved source, never dropped or guessed at."""
    opened = fresh_workspace().section_blockly(SKETCH_NAME, "loop")
    preserved = " ".join(record["text"] for record in opened["preserved"])

    assert "dht.readTemperature()" in preserved
    assert "TEMP_THRESHOLD" in preserved
    assert "BLINK_INTERVAL" in preserved


@pytest.mark.parametrize("region", EDITABLE)
def test_an_untouched_section_applies_back_to_the_identical_file(region: str) -> None:
    """Open and re-apply with no change: the firmware must not move at all."""
    workspace = fresh_workspace()
    before = workspace.full_source(SKETCH_NAME)
    opened = workspace.section_blockly(SKETCH_NAME, region)

    workspace.apply_section_blockly(SKETCH_NAME, region, opened["workspace"], opened["preserved"])

    assert workspace.full_source(SKETCH_NAME) == before


@needs_blockly
def test_real_blockly_loads_and_saves_every_editable_section_unchanged(blockly) -> None:
    """The browser's own block definitions accept every workspace the backend
    produces, keep the ids the preserved records point at, and read back as the
    same C++."""
    workspace = fresh_workspace()
    before = workspace.full_source(SKETCH_NAME)
    opened = {region: workspace.section_blockly(SKETCH_NAME, region) for region in EDITABLE}

    result = blockly(states={region: rep["workspace"] for region, rep in opened.items()})
    assert result["errors"] == {}, result["errors"]

    for region, rep in opened.items():
        saved = result["saved"][region]
        for record in rep["preserved"]:
            assert record["parentId"] in all_ids(saved), (region, record)
            assert record["id"] in all_ids(saved), (region, record)
        workspace.apply_section_blockly(SKETCH_NAME, region, saved, rep["preserved"])

    assert workspace.full_source(SKETCH_NAME) == before


def _edited_show_error(blockly) -> tuple[BuildWorkspace, str, str]:
    """Open `showError`, change its first message in REAL Blockly's saved state,
    and apply it — what a student's text-block edit ends in."""
    workspace = fresh_workspace()
    before = workspace.full_source(SKETCH_NAME)
    opened = workspace.section_blockly(SKETCH_NAME, "helper_showError")
    saved = blockly(states={"edit": opened["workspace"]})["saved"]["edit"]

    edited = copy.deepcopy(saved)
    changed = []

    def retitle(node) -> None:
        if isinstance(node, dict):
            if node.get("fields", {}).get("VALUE") == "Sensor read error":
                node["fields"]["VALUE"] = "Sensor fault"
                changed.append(node)
            for value in node.values():
                retitle(value)
        elif isinstance(node, list):
            for value in node:
                retitle(value)

    retitle(edited)
    assert len(changed) == 1, "the sketch's first error line was not found as a text block"
    workspace.apply_section_blockly(SKETCH_NAME, "helper_showError", edited, opened["preserved"])
    return workspace, before, opened["sectionId"]


@needs_blockly
def test_an_edit_in_real_blockly_reaches_the_generated_cpp(blockly) -> None:
    workspace, before, _ = _edited_show_error(blockly)
    after = workspace.full_source(SKETCH_NAME)

    assert after == before.replace('F("Sensor read error")', 'F("Sensor fault")')


@needs_blockly
def test_an_edit_moves_only_the_edited_region(blockly) -> None:
    edited, _, _ = _edited_show_error(blockly)
    pristine = segments(fresh_workspace())
    after = segments(edited)

    assert list(after) == list(pristine) == REGIONS
    assert [region for region in REGIONS if after[region] != pristine[region]] == ["helper_showError"]
    # And the locked hardware configuration is byte-identical.
    for region in LOCKED:
        assert after[region] == pristine[region]


@needs_blockly
def test_the_hardware_facts_survive_an_edit(blockly) -> None:
    edited, _, _ = _edited_show_error(blockly)
    source = edited.full_source(SKETCH_NAME)
    for fact, pattern in HARDWARE_FACTS.items():
        assert re.search(pattern, source), fact


# =============================================================================
# E. The generated C++ is valid, stable, and compiles
# =============================================================================


def test_the_generator_reproduces_every_statement_of_the_real_sketch(sketch_text: str) -> None:
    """B6's output for the real sketch differs from the original in layout only."""
    generated = source_for_program(program_for_source(sketch_text))

    def squash(text: str) -> str:
        return re.sub(r"\s+", "", text)

    assert squash(generated) == squash(sketch_text)


def test_generation_is_a_fixpoint(sketch_text: str) -> None:
    once = source_for_program(program_for_source(sketch_text))
    twice = source_for_program(program_for_source(once))
    assert once == twice


def test_the_generated_source_has_the_same_structure(sketch_text: str) -> None:
    generated = source_for_program(program_for_source(sketch_text))
    assert [s.section_id for s in analyze_source(generated).sections] == REGIONS


def _compile_through_the_service(workspace: BuildWorkspace) -> BuildSession:
    """The existing BuildService + default compiler path, nothing mocked."""
    session = BuildSession(session_id="panel2-compile", workspace=workspace, panel_id=PANEL_TWO_ID)
    service = BuildService(compiler=default_compiler)
    asyncio.run(service.compile_workspace(session))
    return session


def _assert_compiled(session: BuildSession) -> None:
    out = session.compile_output
    assert session.compile_status.value == "succeeded", (out.stdout + out.stderr)[-3000:]
    assert session.compiled_artifact is not None
    # Compile only: the retained build is never uploaded here.
    assert session.flash_status.value == "not_started"
    shutil.rmtree(session.compiled_artifact.root, ignore_errors=True)


@needs_toolchain
def test_the_shipped_project_compiles_through_buildservice() -> None:
    _assert_compiled(_compile_through_the_service(fresh_workspace()))


@needs_toolchain
@needs_blockly
def test_the_blockly_generated_project_compiles_through_buildservice(blockly) -> None:
    """The firmware that reaches the compiler is REGENERATED from the blocks a
    student edited (B5 -> B6), not the text that was loaded."""
    edited, before, _ = _edited_show_error(blockly)
    assert edited.full_source(SKETCH_NAME) != before

    _assert_compiled(_compile_through_the_service(edited))


@needs_toolchain
def test_pure_b6_output_for_the_whole_sketch_compiles(tmp_path: pathlib.Path, sketch_text: str) -> None:
    """Every section regenerated by the semantic generator, none preserved from
    the original text: the strongest statement that GENERATED code is valid."""
    generated = source_for_program(program_for_source(sketch_text))
    sketch_dir = tmp_path / "environmental_monitoring"
    sketch_dir.mkdir()
    (sketch_dir / SKETCH_NAME).write_text(generated, encoding="utf-8")
    request = CompileRequest(
        sketch_dir=sketch_dir,
        fqbn="esp32:esp32:esp32",
        build_path=tmp_path / "build",
        timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
    )

    outcome = asyncio.run(ArduinoCliCompiler(config.ARDUINO_CLI_PATH).run_compile(request))

    assert outcome.success is True, (outcome.stdout + outcome.stderr)[-3000:]


# =============================================================================
# F. The /ws/build connection path
# =============================================================================


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    selector = BuildProjectSelector(
        resources=PanelResourceService(
            identification=_FixedIdentification(
                PanelIdentification(
                    status=PanelIdentificationStatus.IDENTIFIED,
                    port="COM3",
                    mac=PANEL_TWO_MAC,
                    panel=default_panel_registry().resolve(PANEL_TWO_MAC).panel,
                )
            ),
            loader=default_panel_package_loader(),
        )
    )
    monkeypatch.setattr("app.build_websocket.select_build_project", selector.select)
    with TestClient(app) as test_client:
        yield test_client


def _open_build(ws) -> dict:
    assert ws.receive_json()["type"] == "session"
    assert ws.receive_json()["event"] == "build_session_started"
    assert ws.receive_json()["event"] == "workspace_loaded"
    state = ws.receive_json()
    assert state["type"] == "state"
    return state["data"]


def test_the_build_connection_serves_panel_two_as_an_active_project(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        state = _open_build(ws)

    assert state["has_active_project"] is True
    assert state["project"]["project_id"] == "environmental-monitoring-firmware"
    assert state["project"]["security_region_id"] is None
    # No remediation to show, and no validator to claim anything.
    assert state["remediation"] is None
    segments_by_region = {
        seg["region_id"]: seg["policy"] for seg in state["files"][SKETCH_NAME]["segments"]
    }
    assert segments_by_region == {
        **{region: "editable" for region in EDITABLE},
        **{region: "locked" for region in LOCKED},
    }


def test_a_student_opens_a_panel_two_section_over_the_websocket(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_build(ws)
        ws.send_json({"type": "section_blockly", "path": SKETCH_NAME, "section_id": "setup"})
        frame = ws.receive_json()

    assert frame["type"] == "section"
    assert frame["data"]["representable"] is True
    assert frame["data"]["sectionId"] == "setup"


def test_a_locked_region_edit_over_the_websocket_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_build(ws)
        ws.send_json(
            {"type": "edit_region", "path": SKETCH_NAME, "region_id": "global", "source": "// x\n"}
        )
        frame = ws.receive_json()

    assert frame["type"] == "error"
    assert "locked" in frame["message"]


# =============================================================================
# G. Nothing else moved
# =============================================================================


def test_panel_one_still_declares_its_policy_in_remediation_only() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)

    assert package.build is None
    assert package.remediation.security_section_id == "helper_applyCommand"
    assert package.remediation.editable_section_ids  # unchanged, non-empty
    assert package.remediation.explore_section_ids == ("global", "global_3")


def test_panel_one_build_project_is_unchanged() -> None:
    """Same selector, same sketch: Panel 1's regions, policy and security region."""
    panel = default_panel_registry().resolve("20:9b:a9:88:0b:e4").panel
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED, port="COM3", mac="20:9b:a9:88:0b:e4", panel=panel
    )
    resources = PanelResourceService(
        identification=_FixedIdentification(identification), loader=default_panel_package_loader()
    ).resolve()
    project = BuildProjectSelector().select_for(resources).workspace.project

    assert project.project_id == "smart-home-mqtt-control-firmware"
    assert project.security_region_id == "helper_applyCommand"
    assert project.policy.editable_section_ids == tuple(
        sorted(default_panel_package_loader().load(PANEL_ONE_ID).remediation.editable_section_ids)
    )
    assert project.policy.explore_section_ids == ("global", "global_3")
    assert project.section_policy("helper_applyCommand") is InteractionPolicy.EDITABLE


def test_panel_one_manifest_declares_no_build_block() -> None:
    """Panel 1's manifest keeps its policy in `remediation`; the new block is
    Panel 2's. (That Panel 1's files are byte-unchanged is a `git diff`
    check, not a test: a content pin would break on a CRLF checkout.)"""
    manifest = json.loads((PANELS / PANEL_ONE_ID / "panel.json").read_text(encoding="utf-8"))
    assert "build" not in manifest
    assert "editable_section_ids" in manifest["remediation"]


def test_the_shipped_sketch_is_untouched_by_the_whole_module() -> None:
    """Every test above loaded and materialized it; none may have written to it."""
    text = SKETCH.read_text(encoding="utf-8")
    assert "Sensor read error" in text and "Sensor fault" not in text
    assert [p.name for p in SKETCH_DIR.iterdir()] == [SKETCH_NAME]


def test_the_default_build_selection_without_a_board_is_still_the_no_device_workspace() -> None:
    """The ordinary no-hardware flow: no board -> no activity, never Panel 2's firmware."""
    no_board = PanelResourceService(
        identification=_FixedIdentification(
            PanelIdentification(status=PanelIdentificationStatus.NOT_CONNECTED)
        ),
        loader=default_panel_package_loader(),
    ).resolve()
    chosen = BuildProjectSelector().select_for(no_board)

    assert chosen.source is BuildProjectSource.NONE
    assert not chosen.has_active_project
    assert chosen.workspace.project.project_id != "environmental-monitoring-firmware"
