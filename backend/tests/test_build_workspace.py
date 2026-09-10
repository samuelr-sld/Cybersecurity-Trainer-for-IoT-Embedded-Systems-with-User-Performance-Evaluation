"""Phase 3A/3B verification: the Build Workspace region model.

Covers loading the Environmental Monitoring project, that locked and
editable regions are correctly identified, that editing the editable region
succeeds and is reflected in `full_source`, that editing locked content or
an unknown region is rejected, that two workspaces never share state, and
(Phase 3B) that `materialize` writes a correct, isolated, non-live sketch
copy for `app/build/compiler.py` to compile.

The `workspace` fixture below wraps the Environmental Monitoring project
directly (`create_environmental_monitoring_project`), not
`create_default_workspace` — it is the richer of the two projects (three
files, two locked regions plus one editable) and is what exercises the
region model fully. It is no longer the project a fresh session actually
loads (see `test_default_workspace_loads_the_blink_poc_project` below and
`app/build/__init__.py::create_default_workspace`), but it remains valid,
independently constructible project data for the later five-panel phase.
"""

from __future__ import annotations

import pathlib
import tokenize

import pytest

from app.build import (
    BLINK_REGION_ID,
    SECURITY_REGION_ID,
    BuildWorkspace,
    ProjectFileNotFoundError,
    RegionKind,
    RegionNotEditableError,
    RegionNotFoundError,
    create_default_workspace,
    create_environmental_monitoring_project,
)

BUILD_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "build"


@pytest.fixture
def workspace() -> BuildWorkspace:
    return BuildWorkspace(create_environmental_monitoring_project())


# --- 1 & 2: the workspace and the Environmental Monitoring project load ---


def test_environmental_monitoring_project_loads_correctly(workspace: BuildWorkspace) -> None:
    project = workspace.project
    assert project.scenario_id == "environmental-monitoring"
    assert project.security_region_id == SECURITY_REGION_ID
    assert {f.path for f in project.files} == {
        "main.ino",
        "mqtt_config.h",
        "wifi_secrets.h",
    }
    assert project.board.fqbn == "esp32:esp32:esp32"


def test_default_workspace_loads_the_blink_poc_project() -> None:
    project = create_default_workspace().project
    assert project.scenario_id == "led-blink-poc"
    assert project.security_region_id == BLINK_REGION_ID
    assert {f.path for f in project.files} == {"main.ino"}
    assert project.board.fqbn == "esp32:esp32:esp32"


def test_two_fresh_projects_are_identical() -> None:
    a = create_environmental_monitoring_project()
    b = create_environmental_monitoring_project()
    assert a == b


def test_two_fresh_workspaces_are_isolated() -> None:
    a = create_default_workspace()
    b = create_default_workspace()
    a.update_region("main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
    assert a.region_source("main.ino", BLINK_REGION_ID) != b.region_source(
        "main.ino", BLINK_REGION_ID
    )
    # The starting default is untouched on the other workspace.
    assert "pinMode(2, OUTPUT)" in b.region_source("main.ino", BLINK_REGION_ID)


# --- 3: locked regions are identified correctly ----------------------------


def test_main_ino_has_two_locked_segments_and_one_editable(workspace: BuildWorkspace) -> None:
    main_ino = workspace.project.file("main.ino")
    assert main_ino is not None
    kinds = [segment.kind for segment in main_ino.segments]
    assert kinds == [RegionKind.LOCKED, RegionKind.EDITABLE, RegionKind.LOCKED]


def test_mqtt_config_h_is_entirely_locked(workspace: BuildWorkspace) -> None:
    mqtt_config = workspace.project.file("mqtt_config.h")
    assert mqtt_config is not None
    assert all(segment.kind is RegionKind.LOCKED for segment in mqtt_config.segments)


def test_locked_segments_carry_the_expected_system_code(workspace: BuildWorkspace) -> None:
    source = workspace.full_source("main.ino")
    assert "#include <WiFi.h>" in source
    assert "void setup()" in source
    assert "void loop()" in source
    assert "MQTT_BROKER" in workspace.full_source("mqtt_config.h")


# --- 4: the security region is identified correctly ------------------------


def test_security_region_is_the_editable_segment(workspace: BuildWorkspace) -> None:
    main_ino = workspace.project.file("main.ino")
    assert main_ino is not None
    segment = main_ino.segment(SECURITY_REGION_ID)
    assert segment is not None
    assert segment.kind is RegionKind.EDITABLE


def test_full_source_is_locked_and_editable_text_concatenated(workspace: BuildWorkspace) -> None:
    main_ino = workspace.project.file("main.ino")
    assert main_ino is not None
    rendered = main_ino.render()
    assert rendered == "".join(segment.text for segment in main_ino.segments)
    assert workspace.full_source("main.ino") == rendered


# --- 5: editing the security region succeeds --------------------------------


def test_editing_security_region_updates_source(workspace: BuildWorkspace) -> None:
    new_source = "\n  telemetryAccepted = (incomingTemperature < 60.0);\n"
    workspace.update_region("main.ino", SECURITY_REGION_ID, new_source)
    assert workspace.region_source("main.ino", SECURITY_REGION_ID) == new_source
    assert new_source in workspace.full_source("main.ino")


def test_editing_security_region_leaves_locked_text_untouched(workspace: BuildWorkspace) -> None:
    before_locked_pre = workspace.project.file("main.ino").segment("locked_pre").text
    before_locked_post = workspace.project.file("main.ino").segment("locked_post").text
    workspace.update_region("main.ino", SECURITY_REGION_ID, "telemetryAccepted = false;")
    after = workspace.project.file("main.ino")
    assert after.segment("locked_pre").text == before_locked_pre
    assert after.segment("locked_post").text == before_locked_post


def test_repeated_edits_overwrite_rather_than_accumulate(workspace: BuildWorkspace) -> None:
    workspace.update_region("main.ino", SECURITY_REGION_ID, "first edit")
    workspace.update_region("main.ino", SECURITY_REGION_ID, "second edit")
    assert workspace.region_source("main.ino", SECURITY_REGION_ID) == "second edit"


def test_student_may_submit_syntactically_broken_source(workspace: BuildWorkspace) -> None:
    """No compiler exists yet — a real mistake must be representable."""
    broken = "telemetryAccepted = ((( ;;; not valid c++ at all"
    workspace.update_region("main.ino", SECURITY_REGION_ID, broken)
    assert workspace.region_source("main.ino", SECURITY_REGION_ID) == broken


# --- 6: editing locked content is rejected ----------------------------------


def test_editing_a_locked_region_is_rejected(workspace: BuildWorkspace) -> None:
    before = workspace.full_source("main.ino")
    with pytest.raises(RegionNotEditableError):
        workspace.update_region("main.ino", "locked_pre", "#include <Evil.h>")
    assert workspace.full_source("main.ino") == before


def test_editing_the_fully_locked_config_file_is_rejected(workspace: BuildWorkspace) -> None:
    before = workspace.full_source("mqtt_config.h")
    with pytest.raises(RegionNotEditableError):
        workspace.update_region("mqtt_config.h", "locked_mqtt_config", "#define MQTT_BROKER \"evil\"")
    assert workspace.full_source("mqtt_config.h") == before


def test_editing_the_fully_locked_wifi_secrets_file_is_rejected(workspace: BuildWorkspace) -> None:
    before = workspace.full_source("wifi_secrets.h")
    with pytest.raises(RegionNotEditableError):
        workspace.update_region("wifi_secrets.h", "locked_wifi_secrets", "#define WIFI_PASS \"evil\"")
    assert workspace.full_source("wifi_secrets.h") == before


# --- 7: missing/invalid region identifiers are rejected ---------------------


def test_unknown_region_id_is_rejected(workspace: BuildWorkspace) -> None:
    with pytest.raises(RegionNotFoundError):
        workspace.update_region("main.ino", "does_not_exist", "x")


def test_unknown_file_path_is_rejected(workspace: BuildWorkspace) -> None:
    with pytest.raises(ProjectFileNotFoundError):
        workspace.update_region("nonexistent.ino", SECURITY_REGION_ID, "x")


def test_region_source_lookup_on_unknown_region_raises(workspace: BuildWorkspace) -> None:
    with pytest.raises(RegionNotFoundError):
        workspace.region_source("main.ino", "does_not_exist")


# --- snapshot shape ----------------------------------------------------------


def test_snapshot_exposes_segments_with_kind_and_region_id(workspace: BuildWorkspace) -> None:
    snap = workspace.snapshot()
    assert snap["project"]["security_region_id"] == SECURITY_REGION_ID
    main_segments = snap["files"]["main.ino"]["segments"]
    assert [s["kind"] for s in main_segments] == ["locked", "editable", "locked"]
    assert main_segments[1]["region_id"] == SECURITY_REGION_ID


def test_snapshot_reflects_edits(workspace: BuildWorkspace) -> None:
    workspace.update_region("main.ino", SECURITY_REGION_ID, "telemetryAccepted = false;")
    snap = workspace.snapshot()
    editable = next(
        s for s in snap["files"]["main.ino"]["segments"] if s["region_id"] == SECURITY_REGION_ID
    )
    assert editable["text"] == "telemetryAccepted = false;"


def test_snapshot_includes_fqbn(workspace: BuildWorkspace) -> None:
    assert workspace.snapshot()["project"]["board"]["fqbn"] == "esp32:esp32:esp32"


# --- materialization for compilation (Phase 3B) -----------------------------


def test_materialize_writes_a_sketch_folder_named_after_the_ino_file(
    workspace: BuildWorkspace, tmp_path: pathlib.Path
) -> None:
    sketch_dir = workspace.materialize(tmp_path)
    assert sketch_dir == tmp_path / "main"
    assert sketch_dir.is_dir()


def test_materialize_writes_every_file_with_current_content(
    workspace: BuildWorkspace, tmp_path: pathlib.Path
) -> None:
    workspace.update_region("main.ino", SECURITY_REGION_ID, "telemetryAccepted = false;")
    sketch_dir = workspace.materialize(tmp_path)
    for path in ("main.ino", "mqtt_config.h", "wifi_secrets.h"):
        on_disk = (sketch_dir / path).read_text(encoding="utf-8")
        assert on_disk == workspace.full_source(path)
    assert "telemetryAccepted = false;" in (sketch_dir / "main.ino").read_text(encoding="utf-8")


def test_materialize_does_not_mutate_the_live_workspace(
    workspace: BuildWorkspace, tmp_path: pathlib.Path
) -> None:
    before = workspace.full_source("main.ino")
    workspace.materialize(tmp_path)
    assert workspace.full_source("main.ino") == before


def test_materialize_can_be_called_repeatedly_into_fresh_directories(
    workspace: BuildWorkspace, tmp_path: pathlib.Path
) -> None:
    first = workspace.materialize(tmp_path / "one")
    workspace.update_region("main.ino", SECURITY_REGION_ID, "telemetryAccepted = false;")
    second = workspace.materialize(tmp_path / "two")
    # The first materialized copy is untouched by the later edit — it is a
    # snapshot at the time it was written, not a live view.
    assert "TODO" in (first / "main.ino").read_text(encoding="utf-8")
    assert "telemetryAccepted = false;" in (second / "main.ino").read_text(encoding="utf-8")


# --- no execution primitives in the build layer -----------------------------

FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "os",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "eval",
        "exec",
        "compile",
        "shell",
        "socket",
        "importlib",
        "__import__",
    }
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def _build_modules() -> list[pathlib.Path]:
    paths = sorted(BUILD_DIR.rglob("*.py"))
    assert paths, "no build modules found"
    return paths


#: The build layer's only deliberate exception: `process.py`, which owns the
#: single `subprocess` invocation the real Arduino CLI toolchain needs.
#: `compiler.py` and `flasher.py` build the argument arrays but no longer
#: spawn anything themselves, so they are scanned with the *full* forbidden
#: set like every other module here — see
#: `test_build_compiler.py::test_compiler_module_stays_safe_and_delegates_execution`
#: and `test_build_flasher.py::test_flasher_module_stays_safe_and_delegates_execution`,
#: which also assert that the delegation is real. Every *other* forbidden
#: name remains banned even in `process.py` — only "subprocess" is removed
#: from the set when scanning it — and
#: `test_build_process.py::test_process_module_stays_safe_except_for_its_one_sanctioned_exception`
#: covers that one file's own safety (argument list, no `shell=True`, no
#: `create_subprocess_shell`). Nothing else under app/build/ may spawn a
#: process, which is what keeps service.py, workspace.py and the project
#: definitions free of toolchain execution.
_TOOLCHAIN_MODULES = frozenset({"process.py"})


def test_build_layer_has_no_execution_primitives() -> None:
    offenders = []
    for path in _build_modules():
        forbidden = (
            FORBIDDEN_NAMES - {"subprocess"}
            if path.name in _TOOLCHAIN_MODULES
            else FORBIDDEN_NAMES
        )
        offenders.extend(
            f"{path.relative_to(BUILD_DIR)}: {name}"
            for name in sorted(_code_names(path) & forbidden)
        )
    assert offenders == [], f"execution primitive in build layer: {offenders}"


def test_build_layer_does_not_import_transport() -> None:
    forbidden_imports = ("fastapi", "starlette", "websockets", "app.build_websocket")
    offenders = []
    for path in _build_modules():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            offenders.extend(
                f"{path.name}: {stripped}"
                for forbidden in forbidden_imports
                if forbidden in stripped
            )
    assert offenders == [], f"forbidden import in build layer: {offenders}"


# --- Phase 3C: the compile-to-flash integrity fingerprint -------------------


def test_fingerprint_is_stable_for_an_unchanged_workspace(
    workspace: BuildWorkspace,
) -> None:
    assert workspace.fingerprint() == workspace.fingerprint()


def test_two_fresh_workspaces_fingerprint_identically() -> None:
    assert create_default_workspace().fingerprint() == create_default_workspace().fingerprint()


def test_editing_the_security_region_changes_the_fingerprint(
    workspace: BuildWorkspace,
) -> None:
    before = workspace.fingerprint()
    workspace.update_region("main.ino", SECURITY_REGION_ID, "telemetryAccepted = false;")
    assert workspace.fingerprint() != before


def test_an_undone_edit_restores_the_original_fingerprint(
    workspace: BuildWorkspace,
) -> None:
    """Content-addressed, not an 'edited' flag — see BuildWorkspace.fingerprint."""
    original = workspace.region_source("main.ino", SECURITY_REGION_ID)
    before = workspace.fingerprint()

    workspace.update_region("main.ino", SECURITY_REGION_ID, "something else entirely")
    workspace.update_region("main.ino", SECURITY_REGION_ID, original)

    assert workspace.fingerprint() == before


def test_a_rejected_edit_leaves_the_fingerprint_untouched(
    workspace: BuildWorkspace,
) -> None:
    before = workspace.fingerprint()
    with pytest.raises(RegionNotEditableError):
        workspace.update_region("main.ino", "locked_pre", "#include <Evil.h>")
    assert workspace.fingerprint() == before
