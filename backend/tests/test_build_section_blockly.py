"""Phase B8 correction: Build Mode is a SECTION-BASED BLOCKLY EDITOR.

B8 shipped the interaction POLICY (which section a student may read, explore
or edit) and the VALIDATION (what counts as fixed), but not the thing those
two exist to serve: the contract a Blockly frontend consumes.

    [Code Section]  --click-->  [Blockly Workspace]  --edit-->  firmware

B4 (semantic -> Blockly) and B5 (Blockly -> semantic) both existed and were
both correct, and nothing outside their own package imported either of them.
There was no way on the wire to ask for a section's blocks and no way to submit
any, so the only mechanism a frontend could actually use was `edit_region`'s
raw C++ text. That made Blockly optional in practice however complete it was in
principle, and these tests are what stops it becoming optional again.

WHAT THIS FILE PINS, in the order the student's flow runs:

    1. every discovered section is individually addressable, with its policy
    2. an EDITABLE section OPENS as blocks and is EDITED as blocks
    3. EXPLORE is readable and unwritable — a real third state, not editable
    4. MANY editable sections work; Panel 1 having one is its own policy
    5. a construct Blockly cannot draw is REFUSED, never faked as a text edit
    6. the C++ the compiler sees is generated from blocks, never typed
    7. the block path is as protected as every other path into the workspace

WHAT IT DOES NOT CLAIM. That Panel 1's remediation can be done in Blockly
today. It cannot: the five implemented operations cannot express an
authorization check, `test_panel_one_names_exactly_what_blockly_still_needs`
enumerates precisely what is missing, and the backend says so out loud rather
than routing the student to a C++ editor.
"""

from __future__ import annotations

import ast
import copy
import pathlib

import pytest

from app.build import BuildWorkspace, board_info_from_fqbn, load_sketch_project
from app.build.blockly_bridge import (
    InvalidBlocklyWorkspaceError,
    UnsupportedBlocklyStructureError,
    blockly_section_from_state,
    program_to_blockly,
)
from app.build.discovery import analyze_source
from app.build.document_project import build_project_from_document
from app.build.models import BuildProject, RegionKind
from app.build.policy import InteractionPolicy
from app.build.program_source import program_for_source
from app.build.section_blockly import (
    SectionBlocklyError,
    SectionNotFoundError,
    SectionNotRepresentableError,
    program_with_section,
    section_blockly_for,
    section_representation,
)
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
from app.build.workspace import (
    ProgramApplyError,
    RegionNotEditableError,
    RegionNotFoundError,
    SecurityRegionOwnershipError,
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = BACKEND / "panels" / PANEL_ONE / "firmware" / "smart_home_mqtt_control"
SKETCH_NAME = "smart_home_mqtt_control.ino"

#: The one section of the shipped Panel 1 firmware the five implemented
#: operations DO give a container form, so it is what the representable half of
#: this contract is exercised against. It is not Panel 1's security region —
#: that is the whole point of the "still needed" tests at the end.
DRAWABLE = "setup"


def project(
    *, editable: tuple[str, ...] = (DRAWABLE,), explore: tuple[str, ...] = ()
) -> BuildProject:
    """Panel 1's real firmware under a policy this test chooses.

    The POLICY is a parameter because that is exactly the point being proved:
    which sections are open is a per-activity decision the engine carries, not
    a property of the engine. Panel 1's own declared policy is asserted by
    `tests/test_build_pipeline_b8.py`; here the same firmware is opened
    differently to show the engine does not care.
    """
    return load_sketch_project(
        PANEL_SKETCH,
        project_id="smart-home-mqtt-control-firmware",
        scenario_id=PANEL_ONE,
        module_id=PANEL_ONE,
        firmware_name="Smart Home MQTT Control System",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=editable,
        explore_section_ids=explore,
        security_region_id=None,
    )


def workspace(**kwargs) -> BuildWorkspace:
    return BuildWorkspace(project(**kwargs))


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# =============================================================================
# 1. Sections are individually addressable, and they carry their policy
# =============================================================================


def test_every_discovered_section_is_addressable_by_its_stable_id() -> None:
    """The section IS the interaction surface — see the module docstring."""
    snapshot = workspace().snapshot()
    segments = snapshot["files"][SKETCH_NAME]["segments"]
    ids = [segment["region_id"] for segment in segments]

    assert "helper_applyCommand" in ids and DRAWABLE in ids
    # Stable ids derived from the construct, never display names, never
    # positions, and never a C++ identifier the frontend has to match.
    assert len(set(ids)) == len(ids)
    for segment in segments:
        assert segment["policy"] in {"locked", "explore", "editable"}


def test_a_section_can_be_opened_without_reconstructing_the_firmware() -> None:
    """One request, one section — the contract B9's section navigator needs."""
    representation = workspace().section_blockly(SKETCH_NAME, DRAWABLE)

    assert representation["sectionId"] == DRAWABLE
    assert representation["representable"] is True
    # A Blockly workspace state, loadable as-is, holding this section ALONE.
    blocks = representation["workspace"]["blocks"]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "arduino_setup"


def test_asking_for_an_unknown_section_is_refused_by_name() -> None:
    with pytest.raises(RegionNotFoundError):
        workspace().section_blockly(SKETCH_NAME, "helper_thatDoesNotExist")


def test_a_sections_representation_is_the_same_alone_or_in_the_whole_program() -> None:
    """One conversion path, so an opened section cannot drift from the file."""
    program = program_for_source(workspace().full_source(SKETCH_NAME))
    assert section_blockly_for(program, DRAWABLE) == program_to_blockly(program).section(DRAWABLE)


# =============================================================================
# 2. An EDITABLE section opens as blocks and is edited as blocks
# =============================================================================


def test_an_editable_section_opens_as_blocks_and_edits_as_blocks() -> None:
    """The whole intended workflow, in one test, with no C++ typed anywhere."""
    live = workspace()
    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)

    # The student rearranges blocks. Here: one pinMode's MODE field.
    edited = copy.deepcopy(representation["workspace"])
    first = edited["blocks"]["blocks"][0]["inputs"]["DO"]["block"]
    assert first["fields"]["MODE"] == "INPUT_PULLUP"
    first["fields"]["MODE"] = "OUTPUT"

    live.apply_section_blockly(
        SKETCH_NAME, DRAWABLE, edited, representation["preserved"]
    )

    # The firmware now says what the BLOCKS say. Nobody wrote this C++.
    source = live.region_source(SKETCH_NAME, DRAWABLE)
    assert "pinMode(START_BUTTON, OUTPUT);" in source
    assert "pinMode(STOP_BUTTON, INPUT_PULLUP);" in source


def test_an_unedited_round_trip_changes_what_the_firmware_says_not_at_all() -> None:
    """Opening a section and saving it back is a no-op in meaning.

    B6 owns layout, so the text may be re-laid-out; what may never change is
    the statements. Comparing token streams is what says "same program,
    possibly different whitespace" without pretending B6 reproduces the
    original file's spacing.
    """
    live = workspace()
    before = live.region_source(SKETCH_NAME, DRAWABLE)
    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)

    live.apply_section_blockly(
        SKETCH_NAME, DRAWABLE, representation["workspace"], representation["preserved"]
    )
    assert live.region_source(SKETCH_NAME, DRAWABLE).split() == before.split()


def test_source_the_toolbox_cannot_draw_survives_an_edit_at_its_own_position() -> None:
    """`Serial.begin`, `WiFi.begin`, a while-loop: kept, in order, untouched.

    This is what makes editing ONE section safe at all. A student editing the
    blocks of a section must not lose the statements beside them that the
    toolbox has no block for.
    """
    live = workspace()
    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)
    carried = [record["text"] for record in representation["preserved"]]
    assert any("Serial.begin(115200);" in text for text in carried)
    assert any("WiFi.begin(" in text for text in carried)

    edited = copy.deepcopy(representation["workspace"])
    edited["blocks"]["blocks"][0]["inputs"]["DO"]["block"]["fields"]["MODE"] = "OUTPUT"
    live.apply_section_blockly(
        SKETCH_NAME, DRAWABLE, edited, representation["preserved"]
    )

    source = live.region_source(SKETCH_NAME, DRAWABLE)
    for text in carried:
        assert text.splitlines()[0].strip() in source
    # And still in the order they were in: Serial.begin before WiFi.begin.
    assert source.index("Serial.begin") < source.index("WiFi.begin")


def test_the_generated_cpp_is_an_output_of_the_blocks_not_an_input() -> None:
    """There is no way to put C++ INTO the block path — see `EditSectionBlocksMessage`."""
    from app.models.build_messages import EditSectionBlocksMessage

    assert "source" not in EditSectionBlocksMessage.model_fields
    assert set(EditSectionBlocksMessage.model_fields) >= {
        "path",
        "section_id",
        "workspace",
        "preserved",
    }


# =============================================================================
# 3. EXPLORE is a real third state: readable, never writable
# =============================================================================


def test_an_explore_section_can_be_opened_for_reading() -> None:
    """Understanding the code around the fix is the point of EXPLORE."""
    live = workspace(editable=(DRAWABLE,), explore=("callback_onMessage", "loop"))
    representation = live.section_blockly(SKETCH_NAME, "loop")
    assert representation["sectionId"] == "loop"
    assert representation["representable"] is True


def test_an_explore_section_cannot_be_written_through_the_block_path() -> None:
    live = workspace(editable=(DRAWABLE,), explore=("loop",))
    representation = live.section_blockly(SKETCH_NAME, "loop")

    with pytest.raises(RegionNotEditableError):
        live.apply_section_blockly(
            SKETCH_NAME, "loop", representation["workspace"], representation["preserved"]
        )


def test_explore_is_not_editable_and_is_not_merely_locked() -> None:
    """Three interaction policies over two enforcement kinds, as designed."""
    built = project(editable=(DRAWABLE,), explore=("loop",))
    assert built.section_policy("loop") is InteractionPolicy.EXPLORE
    assert built.section_policy(DRAWABLE) is InteractionPolicy.EDITABLE
    assert built.section_policy("helper_chirpBuzzer") is InteractionPolicy.LOCKED

    kinds = {segment.region_id: segment.kind for segment in built.file(SKETCH_NAME).segments}
    assert kinds["loop"] is RegionKind.LOCKED          # protected exactly like
    assert kinds["helper_chirpBuzzer"] is RegionKind.LOCKED  # an unclassified one
    assert kinds[DRAWABLE] is RegionKind.EDITABLE


def test_a_locked_section_is_refused_before_its_workspace_is_even_read() -> None:
    """Permission first, parsing second — a hostile payload for a locked
    section must never reach the reader."""
    live = workspace(editable=(DRAWABLE,))
    with pytest.raises(RegionNotEditableError):
        live.apply_section_blockly(
            SKETCH_NAME, "helper_chirpBuzzer", {"nonsense": object()}, []
        )


# =============================================================================
# 4. MANY editable sections. Panel 1 has one; that is ITS policy.
# =============================================================================


def test_a_project_may_declare_several_editable_sections() -> None:
    """No part of the Build Engine assumes a single editable section."""
    built = project(editable=(DRAWABLE, "loop"))
    assert built.policy.editable_section_ids == ("loop", DRAWABLE)
    editable = [
        segment.region_id
        for segment in built.file(SKETCH_NAME).segments
        if segment.kind is RegionKind.EDITABLE
    ]
    assert sorted(editable) == ["loop", DRAWABLE]


def test_every_editable_section_is_independently_openable_and_editable() -> None:
    """Two sections, two Blockly workspaces, two accepted edits."""
    live = workspace(editable=(DRAWABLE, "loop"))
    for section_id in (DRAWABLE, "loop"):
        representation = live.section_blockly(SKETCH_NAME, section_id)
        assert representation["sectionId"] == section_id
        live.apply_section_blockly(
            SKETCH_NAME,
            section_id,
            representation["workspace"],
            representation["preserved"],
        )
    # Both still present and still correct after both edits.
    source = live.full_source(SKETCH_NAME)
    assert "void setup()" in source and "void loop()" in source


def test_editing_one_editable_section_does_not_disturb_the_other() -> None:
    live = workspace(editable=(DRAWABLE, "loop"))
    untouched = live.region_source(SKETCH_NAME, "loop")

    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)
    edited = copy.deepcopy(representation["workspace"])
    edited["blocks"]["blocks"][0]["inputs"]["DO"]["block"]["fields"]["MODE"] = "OUTPUT"
    live.apply_section_blockly(SKETCH_NAME, DRAWABLE, edited, representation["preserved"])

    assert live.region_source(SKETCH_NAME, "loop").split() == untouched.split()


def test_a_project_may_declare_no_editable_section_at_all() -> None:
    """`editable_section_ids = ()` is a legitimate, fully read-only activity."""
    built = project(editable=(), explore=("loop",))
    assert built.policy.editable_section_ids == ()
    assert all(
        segment.kind is RegionKind.LOCKED for segment in built.file(SKETCH_NAME).segments
    )


# =============================================================================
# 5. A construct Blockly cannot draw is refused, never faked
# =============================================================================


#: The one still-genuinely-unrepresentable section the "cannot draw" tests
#: below are exercised against. CORRECTED: `helper_applyCommand` no longer
#: demonstrates this — the no-device/Blockly-integration correction gave
#: every HELPER_FUNCTION/CALLBACK a container form (`functions.implementation`),
#: `helper_applyCommand` included (see `test_panel_one_names_exactly_what_
#: blockly_still_needs`, which now enumerates what is STILL missing for its
#: full remediation, not whether it opens at all). A run of GLOBAL_DECLARATIONS
#: text remains the one kind with no container form, because it names no
#: single construct a container could represent.
UNDRAWABLE = "global_2"


def test_a_section_blockly_cannot_draw_reports_itself_honestly() -> None:
    """`representable: false` is the difference between "no blocks" and
    "the student deleted everything"."""
    representation = workspace(editable=(UNDRAWABLE,)).section_blockly(
        SKETCH_NAME, UNDRAWABLE
    )
    assert representation["representable"] is False
    assert representation["workspace"]["blocks"]["blocks"] == []
    # Nothing is lost: the construct is there, carried verbatim, for a
    # read-only view to show.
    assert len(representation["preserved"]) == 1
    assert "Apply a new motor state" in representation["preserved"][0]["text"]


def test_a_section_blockly_cannot_draw_is_refused_not_faked() -> None:
    """THE PROHIBITED SHORTCUT, CLOSED.

    Accepting this submission would mean writing the fragment's text back into
    the firmware — a C++ text edit wearing a Blockly-shaped frame. The backend
    refuses and says the toolbox has no vocabulary for the construct yet, which
    is what keeps the missing vocabulary visible.
    """
    live = workspace(editable=(UNDRAWABLE,))
    representation = live.section_blockly(SKETCH_NAME, UNDRAWABLE)
    before = live.region_source(SKETCH_NAME, UNDRAWABLE)

    with pytest.raises(ProgramApplyError, match="no Blockly representation yet"):
        live.apply_section_blockly(
            SKETCH_NAME,
            UNDRAWABLE,
            representation["workspace"],
            representation["preserved"],
        )
    assert live.region_source(SKETCH_NAME, UNDRAWABLE) == before


def test_tampered_preserved_text_cannot_smuggle_cpp_through_the_block_path() -> None:
    """The refusal above is what closes this, and it is worth pinning alone."""
    live = workspace(editable=(UNDRAWABLE,))
    representation = live.section_blockly(SKETCH_NAME, UNDRAWABLE)
    tampered = [{**representation["preserved"][0], "text": "static void evil() { evil(); }"}]

    with pytest.raises(ProgramApplyError):
        live.apply_section_blockly(SKETCH_NAME, UNDRAWABLE, representation["workspace"], tampered)
    assert "evil()" not in live.full_source(SKETCH_NAME)


def test_a_sections_construct_cannot_be_deleted_by_emptying_its_workspace() -> None:
    live = workspace()
    empty = {"blocks": {"languageVersion": 0, "blocks": []}}
    with pytest.raises(ProgramApplyError, match="cannot be removed"):
        live.apply_section_blockly(SKETCH_NAME, DRAWABLE, empty, [])
    assert "void setup()" in live.full_source(SKETCH_NAME)


def test_a_sections_construct_cannot_be_turned_into_a_different_one() -> None:
    """A workspace that turns `setup` into `loop` renames the function."""
    live = workspace()
    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)
    swapped = copy.deepcopy(representation["workspace"])
    swapped["blocks"]["blocks"][0]["type"] = "arduino_loop"

    with pytest.raises(ProgramApplyError):
        live.apply_section_blockly(
            SKETCH_NAME, DRAWABLE, swapped, representation["preserved"]
        )


# =============================================================================
# 6. The workspace reader treats its input as untrusted
# =============================================================================


@pytest.mark.parametrize(
    "payload",
    [
        "not an object",
        {"blocks": "not an object"},
        {"blocks": {"languageVersion": 99, "blocks": []}},
        {"blocks": {"languageVersion": 0, "blocks": "not a list"}},
        {"blocks": {"languageVersion": 0, "blocks": [{"no": "type"}]}},
    ],
)
def test_a_malformed_workspace_is_reported_never_repaired(payload) -> None:
    with pytest.raises((InvalidBlocklyWorkspaceError, UnsupportedBlocklyStructureError)):
        blockly_section_from_state(DRAWABLE, payload)


def test_a_workspace_naming_an_undrawable_block_type_is_refused() -> None:
    from app.build.blockly_bridge import UnknownBlocklyBlockError

    with pytest.raises(UnknownBlocklyBlockError):
        blockly_section_from_state(
            DRAWABLE,
            {"blocks": {"languageVersion": 0, "blocks": [{"type": "no_such_block"}]}},
        )


def test_a_section_is_one_construct_so_two_top_level_blocks_are_refused() -> None:
    with pytest.raises(UnsupportedBlocklyStructureError, match="top-level"):
        blockly_section_from_state(
            DRAWABLE,
            {
                "blocks": {
                    "languageVersion": 0,
                    "blocks": [{"type": "arduino_setup"}, {"type": "arduino_loop"}],
                }
            },
        )


def test_deeply_nested_blocks_are_refused_rather_than_exhausting_the_stack() -> None:
    from app.build.blockly_bridge import MAX_BLOCK_DEPTH

    node: dict = {"type": "arduino_setup"}
    for _ in range(MAX_BLOCK_DEPTH + 5):
        node = {"type": "arduino_setup", "inputs": {"DO": {"block": node}}}
    with pytest.raises((InvalidBlocklyWorkspaceError, UnsupportedBlocklyStructureError)):
        blockly_section_from_state(
            DRAWABLE, {"blocks": {"languageVersion": 0, "blocks": [node]}}
        )


def test_a_preserved_fragment_past_the_end_of_the_body_is_refused() -> None:
    with pytest.raises(InvalidBlocklyWorkspaceError, match="past the end"):
        blockly_section_from_state(
            DRAWABLE,
            {"blocks": {"languageVersion": 0, "blocks": [{"type": "arduino_setup"}]}},
            [{"index": 99, "text": "whatever();", "reason": "not_a_call"}],
        )


def test_an_unknown_preservation_reason_is_refused() -> None:
    with pytest.raises(InvalidBlocklyWorkspaceError, match="preservation reason"):
        blockly_section_from_state(
            DRAWABLE,
            {"blocks": {"languageVersion": 0, "blocks": [{"type": "arduino_setup"}]}},
            [{"index": 0, "text": "x();", "reason": "invented_reason"}],
        )


def test_a_section_id_a_program_does_not_have_is_refused() -> None:
    program = program_for_source(workspace().full_source(SKETCH_NAME))
    with pytest.raises(SectionNotFoundError):
        section_representation(program, "helper_nope")


# =============================================================================
# 7. The block path is as protected as every other path in
# =============================================================================


def test_a_block_edit_cannot_reach_a_locked_region_through_reconstruction() -> None:
    """`apply_program_to_file`'s independent second proof still applies.

    `program_with_section` structurally cannot touch another section, so this
    forces the situation directly: a program whose LOCKED section differs.
    """
    live = workspace()
    program = live.program(SKETCH_NAME)
    section = section_blockly_for(program, DRAWABLE)
    spliced = program_with_section(program, section)
    # Same program back, so nothing is rejected; every other section is the
    # very object it was.
    for original in program.sections:
        if original.section_id != DRAWABLE:
            assert spliced.section(original.section_id) is original


def test_a_block_edit_marks_the_session_dirty_and_records_the_same_events() -> None:
    """An evaluator cannot tell a block edit from a text edit, and should not."""
    import asyncio

    from app.build.events import BuildEventType
    from app.build.service import default_service
    from app.build_sessions import BuildSession

    async def run() -> None:
        session = BuildSession(session_id="s1", workspace=workspace(editable=(DRAWABLE,)))
        representation = session.workspace.section_blockly(SKETCH_NAME, DRAWABLE)
        result = await default_service.edit_section_blocks(
            session,
            SKETCH_NAME,
            DRAWABLE,
            representation["workspace"],
            representation["preserved"],
        )
        assert result.success
        assert session.dirty is True
        assert [event.type for event in result.events] == [BuildEventType.CODE_EDITED]

    asyncio.run(run())


def test_reading_a_section_is_not_an_edit_and_records_nothing() -> None:
    """Clicking a section to look at it must never be a remediation attempt."""
    import asyncio

    from app.build.service import default_service
    from app.build_sessions import BuildSession

    async def run() -> None:
        session = BuildSession(session_id="s2", workspace=workspace())
        result = await default_service.read_section_blockly(session, SKETCH_NAME, DRAWABLE)
        assert result.success
        assert result.events == ()
        assert result.data is not None and result.data["sectionId"] == DRAWABLE
        assert session.dirty is False

    asyncio.run(run())


def _connect(monkeypatch, live: BuildWorkspace):
    """A `/ws/build` client whose session loads `live`.

    The connection lifecycle chooses the session's workspace through
    `select_build_project` (`app/build_project_selection.py`), so a test drives
    it the way a panel does — by supplying the selection — rather than by
    reaching into the session afterwards. Without hardware the real selector
    returns the default LED Blink project, which is hand-authored and therefore
    deliberately outside this contract (see
    `test_a_hand_authored_project_is_refused_rather_than_re_sectioned`).
    """
    import app.build_websocket as build_websocket
    from app.build_project_selection import BuildProjectSelection, BuildProjectSource
    from app.panels.service import PanelResourceStatus

    monkeypatch.setattr(
        build_websocket,
        "select_build_project",
        lambda: BuildProjectSelection(
            workspace=live,
            source=BuildProjectSource.PANEL_PACKAGE,
            panel_status=PanelResourceStatus.READY,
            panel_id=PANEL_ONE,
        ),
    )
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _state(socket) -> dict:
    while True:
        frame = socket.receive_json()
        if frame["type"] == "state":
            return frame["data"]


def test_the_contract_is_reachable_over_the_real_socket(monkeypatch) -> None:
    """THE B9 CONTRACT, END TO END: state -> pick a section -> get its blocks.

    What this pins is the WIRE: the frames exist, every section arrives in the
    snapshot with its stable id and its policy, a section can be selected by
    that id, and its Blockly representation comes back on its own frame with no
    `state` resend — because nothing changed.
    """
    from app.models.build_messages import BUILD_PROTOCOL_VERSION

    with _connect(monkeypatch, workspace(editable=(DRAWABLE,), explore=("loop",))) as client:
        with client.websocket_connect("/ws/build") as socket:
            assert socket.receive_json()["protocol_version"] == BUILD_PROTOCOL_VERSION >= 6
            state = _state(socket)

            segments = state["files"][SKETCH_NAME]["segments"]
            policies = {s["region_id"]: s["policy"] for s in segments}
            assert policies[DRAWABLE] == "editable"
            assert policies["loop"] == "explore"
            assert policies["helper_chirpBuzzer"] == "locked"

            socket.send_json(
                {"type": "section_blockly", "path": SKETCH_NAME, "section_id": DRAWABLE}
            )
            answer = socket.receive_json()
            assert answer["type"] == "section"
            assert answer["data"]["sectionId"] == DRAWABLE
            assert answer["data"]["path"] == SKETCH_NAME
            assert answer["data"]["representable"] is True
            assert answer["data"]["workspace"]["blocks"]["blocks"][0]["type"] == "arduino_setup"


def test_a_block_edit_over_the_socket_rewrites_the_firmware(monkeypatch) -> None:
    """The full intended student action, on the wire, with no C++ submitted."""
    with _connect(monkeypatch, workspace(editable=(DRAWABLE,))) as client:
        with client.websocket_connect("/ws/build") as socket:
            socket.receive_json()
            _state(socket)

            socket.send_json(
                {"type": "section_blockly", "path": SKETCH_NAME, "section_id": DRAWABLE}
            )
            representation = socket.receive_json()["data"]

            edited = copy.deepcopy(representation["workspace"])
            edited["blocks"]["blocks"][0]["inputs"]["DO"]["block"]["fields"]["MODE"] = "OUTPUT"
            socket.send_json(
                {
                    "type": "edit_section_blocks",
                    "path": SKETCH_NAME,
                    "section_id": DRAWABLE,
                    "workspace": edited,
                    "preserved": representation["preserved"],
                }
            )

            assert socket.receive_json()["type"] == "event"
            state = _state(socket)
            setup = next(
                segment
                for segment in state["files"][SKETCH_NAME]["segments"]
                if segment["region_id"] == DRAWABLE
            )
            assert "pinMode(START_BUTTON, OUTPUT);" in setup["text"]
            assert state["dirty"] is True


def test_the_socket_refuses_a_block_edit_to_a_section_the_policy_locks(monkeypatch) -> None:
    """One `error` frame and no state change — the shape a rejected
    `edit_region` has always had."""
    with _connect(monkeypatch, workspace(editable=(DRAWABLE,), explore=("loop",))) as client:
        with client.websocket_connect("/ws/build") as socket:
            socket.receive_json()
            _state(socket)

            for section_id in ("loop", "helper_chirpBuzzer"):
                socket.send_json(
                    {
                        "type": "edit_section_blocks",
                        "path": SKETCH_NAME,
                        "section_id": section_id,
                        "workspace": {"blocks": {"languageVersion": 0, "blocks": []}},
                        "preserved": [],
                    }
                )
                error = socket.receive_json()
                assert error["type"] == "error"
                assert "locked" in error["message"]


def test_a_hand_authored_project_is_refused_rather_than_re_sectioned() -> None:
    """The default LED Blink project is OUTSIDE this contract, on purpose.

    Its regions are named by hand (`blink_program`) and predate B1, so no
    discovered section corresponds to them. Re-sectioning it to make the
    contract apply would silently redraw its locked/editable boundaries, which
    is a permission decision no phase has made for it (see
    `app/build/program_source.py::UnstructuredFileError`). It is therefore a
    clean refusal, not a crash and not an invented section list — and it costs
    nothing, because the projects students remediate are panel firmware,
    which is discovered structurally.
    """
    from app.build import create_default_workspace

    live = create_default_workspace()
    with pytest.raises(ProgramApplyError):
        live.section_blockly("main.ino", "blink_program")


# =============================================================================
# 8. Layering: the correction introduced no shortcut
# =============================================================================


def test_the_section_boundary_knows_no_panel_no_session_and_no_io() -> None:
    body = (BACKEND / "app" / "build" / "section_blockly.py").read_text(encoding="utf-8")
    code = body.split('"""', 2)[-1]
    for term in ("smart-home", "smart_home", "applyCommand", "panel", "subprocess", "open("):
        assert term not in code, f"section_blockly.py code mentions {term!r}"


def test_the_section_boundary_does_not_join_the_catalog_to_the_ir() -> None:
    """Only `app/build/blockly_bridge/` may import both — the bridge is the seam."""
    imported = _imports(BACKEND / "app" / "build" / "section_blockly.py")
    assert not any(name.startswith("app.blockly") for name in imported)


def test_the_workspace_reader_writes_down_no_blockly_type() -> None:
    """The catalog is still the only place a Blockly type is named.

    Scanned without the module docstring, which cites the absent
    `"pinmode" -> gpio.pin_mode` table by name precisely to say it does not
    exist — the same convention the other layering tests follow.
    """
    body = (BACKEND / "app" / "build" / "blockly_bridge" / "workspace_state.py").read_text(
        encoding="utf-8"
    )
    code = body.split('"""', 2)[-1]
    for literal in ("arduino_setup", "arduino_loop", "pinmode", "digitalwrite", "delay"):
        assert f'"{literal}"' not in code


def test_no_module_on_the_block_edit_path_can_execute_anything() -> None:
    for module in (
        BACKEND / "app" / "build" / "section_blockly.py",
        BACKEND / "app" / "build" / "blockly_bridge" / "workspace_state.py",
    ):
        body = module.read_text(encoding="utf-8")
        for term in ("subprocess", "eval(", "exec(", "os.system", "shell=True"):
            assert term not in body, f"{module.name} mentions {term!r}"


# =============================================================================
# 9. What Blockly still needs before Panel 1's own remediation is drawable
# =============================================================================


def test_panel_one_remediation_is_now_representable_through_dedicated_blocks() -> None:
    """CORRECTED: the gap this test used to enumerate is closed — not by
    implementing the GENERIC composition path this test originally named
    (`functions.define` + `functions.parameter` + `text.literal` +
    `logic.equal` + `logic.if_else` + `functions.call`, which would need
    nested VALUE-input blocks the Blockly bridge still does not model), but by
    three deliberately narrow, dedicated blocks scoped to exactly what an
    authorization gate needs:

        static void applyCommand(const String &message) {
          if (message == "START <token>") { motorStart(); }
          if (message == "STOP <token>") { motorStop(); }
        }

    (two independent `if`s, not `if`/`else if` — see `ConditionalStatement`'s
    docstring for why that is semantically identical here and needs no nested
    ELSE body at all.)

    UPDATED by the token-parsing hardening: the bridge now models nested VALUE
    inputs, so the GENERIC `text.literal`/`logic.equal` (and the rest of the
    token-parser vocabulary — see
    `test_the_documented_token_parser_vocabulary_is_generic_and_implemented`)
    are implemented. Authoring a brand-new function (`functions.define`/
    `functions.parameter`/`functions.call`) and `if`/`else` stay unimplemented.
    """
    from app.blockly.catalog import default_block_catalog
    from app.blockly.models import ImplementationStatus

    still_generic_and_unimplemented = {
        "functions.define",
        "functions.parameter",
        "logic.if_else",
        "functions.call",
    }
    by_id = {block.block_id: block for block in default_block_catalog.blocks}
    not_implemented = sorted(
        block_id
        for block_id in still_generic_and_unimplemented
        if by_id[block_id].status is not ImplementationStatus.IMPLEMENTED
    )
    assert not_implemented == sorted(still_generic_and_unimplemented)

    dedicated_and_implemented = {
        "functions.implementation",  # applyCommand()'s own body, as a container
        "functions.call_existing",  # motorStart() / motorStop()
        "logic.if_equals",  # message == "<token>"
    }
    not_yet_implemented = sorted(
        block_id
        for block_id in dedicated_and_implemented
        if by_id[block_id].status is not ImplementationStatus.IMPLEMENTED
    )
    assert not_yet_implemented == [], f"expected these implemented: {not_yet_implemented}"


def test_the_ir_now_has_a_comparison_expression_and_a_conditional_statement() -> None:
    """The two STRUCTURAL additions behind the dedicated blocks above.

    Deliberately narrow, not a general expression grammar or nested-body
    machinery: one comparison form (`ComparisonValue`, always boolean) and one
    control-flow statement with a body and no `else`
    (`ConditionalStatement`) — exactly what an authorization gate needs, named
    explicitly rather than inferred from a changed assertion count.
    """
    from app.build.semantic.models import (
        CallStatement,
        ComparisonValue,
        ConditionalStatement,
        LiteralValue,
        SemanticValue,
        SymbolValue,
    )
    from app.build.semantic.operations import FUNCTIONS_IMPLEMENTATION, SemanticType

    # The token-parsing hardening added exactly two value forms: `+` over
    # numbers, and a VALUE operation (the `String` queries) applied to operands.
    assert {cls.__name__ for cls in SemanticValue.__subclasses__()} == {
        "LiteralValue",
        "SymbolValue",
        "ComparisonValue",
        "ArithmeticValue",
        "OperationValue",
    }
    assert ComparisonValue(
        left=SymbolValue("message"), operator="==", right=LiteralValue("START", SemanticType.TEXT)
    ).fits(SemanticType.BOOLEAN)
    assert "body" in ConditionalStatement.__dataclass_fields__
    assert "function_name" in CallStatement.__dataclass_fields__
    # Still no parameterised container: `functions.implementation` declares no
    # operands, and a function's own signature is preserved verbatim on its
    # section instead — see `SemanticSection.signature`.
    from app.build.semantic.operations import default_semantic_operations

    implementation = default_semantic_operations.require(FUNCTIONS_IMPLEMENTATION)
    assert implementation.parameters == ()


def test_panel_one_security_region_is_still_reachable_through_the_text_path() -> None:
    """The gap costs the student nothing TODAY — the legacy path still works.

    Which is the reason the refusal above is acceptable: Panel 1 remains
    completable while the blocks are built, and no interface decision was made
    by default.
    """
    live = workspace(editable=("helper_applyCommand",))
    live.update_region(
        SKETCH_NAME,
        "helper_applyCommand",
        '\nstatic void applyCommand(const String &message) {\n'
        '  if (message == "START TOKEN") {\n    motorStart();\n  }\n}',
    )
    assert "START TOKEN" in live.full_source(SKETCH_NAME)


# =============================================================================
# 10. Full ownership of the security region
# =============================================================================
#
# `helper_applyCommand`'s committed body is `if (message == "START") { ... }
# else if (message == "STOP") { ... }`. The FIRST `if` is representable — see
# section 9 above — but the analyzer only ever recognizes the first `if` of an
# `if`/`else if` chain (`ConditionalStatement`'s own docstring says why), so
# the `else if` branch — the ORIGINAL, unauthenticated STOP — can never become
# a block. It stays a `PreservedSource` the IR never understood.
#
# The danger: a student drags in a NEW `logic.if_equals` block for an
# authenticated STOP, and a submission that (as an unmodified frontend
# round-trip naturally would) still carries that old fragment back puts BOTH
# branches in the regenerated firmware — a "fix" that compiles and still has
# the vulnerability sitting right underneath it. These tests pin the fix:
# once a security region is edited as blocks, no such opaque fragment may
# survive the submission.

SECURITY_SECTION = "helper_applyCommand"


def security_project(*, editable: tuple[str, ...] = (SECURITY_SECTION,)) -> BuildProject:
    """Panel 1's real firmware, with its own declared `security_region_id` set.

    `project()` above deliberately leaves it `None` so the generic Blockly
    contract can be shown not to care; this is the one place in this file that
    turns it on, to prove the STRICTER rule that only applies when it is set.
    """
    return load_sketch_project(
        PANEL_SKETCH,
        project_id="smart-home-mqtt-control-firmware",
        scenario_id=PANEL_ONE,
        module_id=PANEL_ONE,
        firmware_name="Smart Home MQTT Control System",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=editable,
        security_region_id=SECURITY_SECTION,
    )


def security_workspace(**kwargs) -> BuildWorkspace:
    return BuildWorkspace(security_project(**kwargs))


def _current_security_section(live: BuildWorkspace) -> SemanticSection:
    program = program_for_source(live.full_source(SKETCH_NAME))
    section = program.section(SECURITY_SECTION)
    assert section is not None
    return section


def _authenticated_stop_statement() -> ConditionalStatement:
    """A student-authored, fully block-representable authenticated STOP gate.

    No `text` — authored in the editor, not read from source — exactly like
    any other block a student places rather than one converted from firmware.
    """
    return ConditionalStatement(
        condition=ComparisonValue(
            left=SymbolValue(name="message"),
            operator="==",
            right=LiteralValue(value="STOP PANEL1-CMD-AUTH-K7", value_type=SemanticType.TEXT),
        ),
        body=(CallStatement(function_name="motorStop"),),
    )


def _representation_for(section: SemanticSection) -> dict:
    """One section's Blockly representation, produced the same way B4 always
    does — a one-section `SemanticProgram` is enough; `program_to_blockly`
    never looks past the section it is converting."""
    converted = program_to_blockly(SemanticProgram(sections=(section,)))
    representation = converted.section(section.section_id)
    assert representation is not None
    return representation.to_representation()


def test_a_naive_carry_through_of_the_old_branch_is_refused() -> None:
    """Requirement 7's failure mode, submitted exactly as an unmodified
    frontend round-trip would: the new authenticated STOP block, PLUS the old
    unauthenticated `else if` fetched moments earlier and sent straight back.
    """
    live = security_workspace()
    current = _current_security_section(live)
    start_branch, old_unauthenticated_branch = current.statements[0], current.statements[1]
    modified = SemanticSection(
        section_id=SECURITY_SECTION,
        operation=current.operation,
        statements=(start_branch, _authenticated_stop_statement(), old_unauthenticated_branch),
    )
    representation = _representation_for(modified)
    assert representation["preserved"], "the old branch must still be the one opaque fragment"

    with pytest.raises(SecurityRegionOwnershipError) as excinfo:
        live.apply_section_blockly(
            SKETCH_NAME,
            SECURITY_SECTION,
            representation["workspace"],
            representation["preserved"],
        )
    assert "security region" in str(excinfo.value)
    assert "STOP" in str(excinfo.value)
    # The rejection changed nothing: the committed vulnerability is still
    # sitting there, in the text, exactly as it was — never silently applied.
    assert 'else if (message == "STOP")' in live.region_source(SKETCH_NAME, SECURITY_SECTION)


def test_full_ownership_edit_drops_the_old_unauthenticated_branch() -> None:
    """The regression test for the exact failure mode (requirement 7).

    Once the submission takes FULL ownership — no preserved material at all —
    it is accepted, and the generated C++ has the new authenticated gate and
    not one trace of the old bare comparison.
    """
    live = security_workspace()
    current = _current_security_section(live)
    modified = SemanticSection(
        section_id=SECURITY_SECTION,
        operation=current.operation,
        statements=(current.statements[0], _authenticated_stop_statement()),
    )
    representation = _representation_for(modified)
    assert representation["preserved"] == []

    live.apply_section_blockly(
        SKETCH_NAME,
        SECURITY_SECTION,
        representation["workspace"],
        representation["preserved"],
    )

    source = live.region_source(SKETCH_NAME, SECURITY_SECTION)
    assert "motorStart();" in source
    assert "motorStop();" in source
    assert '"STOP PANEL1-CMD-AUTH-K7"' in source
    # The old, unauthenticated branch is gone — not hidden, not re-attached.
    assert "else if" not in source
    assert '"STOP"' not in source


def test_legitimate_preserved_material_outside_the_security_region_is_still_kept() -> None:
    """The stricter rule is SCOPED to the security region alone.

    Every other editable section keeps the existing "carry unsupported C++
    through a Blockly edit" behavior untouched — even in a project that HAS a
    `security_region_id` declared, just for a different section.
    """
    live = security_workspace(editable=(SECURITY_SECTION, DRAWABLE))
    representation = live.section_blockly(SKETCH_NAME, DRAWABLE)
    carried = [record["text"] for record in representation["preserved"]]
    assert any("Serial.begin(115200);" in text for text in carried)
    assert any("WiFi.begin(" in text for text in carried)

    live.apply_section_blockly(
        SKETCH_NAME, DRAWABLE, representation["workspace"], representation["preserved"]
    )
    source = live.region_source(SKETCH_NAME, DRAWABLE)
    for text in carried:
        assert text.splitlines()[0].strip() in source


def test_taking_ownership_of_the_security_region_does_not_disturb_other_regions() -> None:
    """A successful security-region edit leaves every other region exactly as
    B7's existing single-section-replacement guarantee already promises."""
    live = security_workspace(editable=(SECURITY_SECTION, DRAWABLE, "loop"))
    untouched_setup = live.region_source(SKETCH_NAME, DRAWABLE)
    untouched_loop = live.region_source(SKETCH_NAME, "loop")

    current = _current_security_section(live)
    modified = SemanticSection(
        section_id=SECURITY_SECTION,
        operation=current.operation,
        statements=(current.statements[0], _authenticated_stop_statement()),
    )
    representation = _representation_for(modified)
    live.apply_section_blockly(
        SKETCH_NAME,
        SECURITY_SECTION,
        representation["workspace"],
        representation["preserved"],
    )

    assert live.region_source(SKETCH_NAME, DRAWABLE).split() == untouched_setup.split()
    assert live.region_source(SKETCH_NAME, "loop").split() == untouched_loop.split()
    # And the security edit itself really did take effect.
    assert '"STOP PANEL1-CMD-AUTH-K7"' in live.region_source(SKETCH_NAME, SECURITY_SECTION)


def test_ir_understood_material_may_still_be_preserved_in_the_security_region() -> None:
    """The other half of "full ownership": a fragment B3 DID understand, and
    only a real Blockly field could not hold, is not opaque C++ — it is not
    the risk this rule exists to close, so it is unaffected by it.

    A synthetic firmware is used because Panel 1's own security region has no
    fragment of this kind today; the distinction being tested
    (`understood_by_the_ir`) is generic, not specific to Panel 1's source.
    """
    source = (
        "void setup() {\n}\n\nvoid loop() {\n}\n\n"
        "static void chirp() {\n  pinMode(2, OUTPUT);\n  delay(BUZZER_CHIRP_MS);\n}\n"
    )
    document = analyze_source(source)
    project = build_project_from_document(
        document,
        path="main.ino",
        project_id="p",
        scenario_id="s",
        module_id="m",
        firmware_name="f",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=("helper_chirp",),
        security_region_id="helper_chirp",
    )
    live = BuildWorkspace(project)
    representation = live.section_blockly("main.ino", "helper_chirp")
    assert representation["preserved"][0]["understoodByTheIr"] is True

    edited = copy.deepcopy(representation["workspace"])
    edited["blocks"]["blocks"][0]["inputs"]["BODY"]["block"]["fields"]["MODE"] = "INPUT_PULLUP"
    live.apply_section_blockly("main.ino", "helper_chirp", edited, representation["preserved"])

    source_after = live.region_source("main.ino", "helper_chirp")
    assert "pinMode(2, INPUT_PULLUP);" in source_after
    assert "delay(BUZZER_CHIRP_MS);" in source_after
