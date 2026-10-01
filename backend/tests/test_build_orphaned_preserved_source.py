"""An ORPHANED `preserved_source` drawing is not a second construct.

Live symptom this file pins shut (Panel 1's `helper_applyCommand`):

    helper_applyCommand: a section is one construct, but this workspace has 2
    top-level blocks

The read-only `preserved_source` block is unmovable and undeletable, and real
Blockly cannot always keep it attached: healing a stack skips a non-movable
successor, and inserting a block with no next socket (`return_void`) bumps the
displaced tail out of its chain. Either leaves the drawing as its own TOP-LEVEL
block in the saved workspace, which the one-construct rule counted as a second
construct. The drawing is display only - its id and its chain position are all
the reader ever uses of it, never its text - so such a root must not count.

What stays true, and is asserted below:

* function + an orphaned drawing                -> accepted, fragment from the RECORD
* function + a genuine stray block              -> refused, naming its type and id
* several genuine roots                         -> refused, every one named
* a real block chained under a drawing, or a drawing carrying inputs
                                                -> still a construct, still refused
* a lone drawing                                -> never the section's construct
* in-chain ordering                             -> unchanged by an orphan beside it

The last group drives the REAL Blockly and the real Panel 1 `helper_applyCommand`.
Skipped, not failed, where Node or the frontend dependencies are unavailable.
"""

from __future__ import annotations

import copy
import pathlib
import re

import pytest

from app.build.blockly_bridge import (
    UnsupportedBlocklyStructureError,
    blockly_section_from_state,
)
from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    SectionNotRepresentableError,
    program_with_section,
    section_from_state,
    section_representation,
)
from app.build.workspace import ProgramApplyError

from tests.test_build_preserved_ordering import (
    EVIL,
    OPAQUE,
    apply,
    by_id,
    deface,
    loop_source,
    reorder,
    relink,
    representation,
    unlink,
    walk,
)
from tests.test_p4_0_real_blockly_remediation import (
    SECTION,
    SKETCH_NAME,
    new,
    panel_one_workspace,
)
from tests.test_real_blockly import blockly, pytestmark as _needs_blockly  # noqa: F401

PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parent.parent
    / "panels" / "smart-home-mqtt-control" / "firmware" / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)
PROGRAM = program_for_source(PANEL_ONE_INO.read_text(encoding="utf-8"))

DRAWING_ID = f"{SECTION}.1"  # helper_applyCommand's trailing explanatory comment
STRAY = {"type": "call_existing_function", "id": "stray1", "fields": {"NAME": "somewhere"}}


# --- the saved workspace as Blockly leaves it after the drawing is bumped out --


def roots(workspace: dict) -> list[dict]:
    return workspace["blocks"]["blocks"]


def body_input(container: dict) -> str:
    """The name of the statement input that holds a container's body."""
    return "DO" if "DO" in container["inputs"] else "BODY"


def detach(workspace: dict, block_id: str, *, heal: bool = False) -> tuple[dict, dict]:
    """`workspace` with the block `block_id` cut out of its chain, plus that block.

    Without `heal` the block leaves WITH whatever was chained under it - what
    Blockly's bump does to a displaced tail, and what a plain drag does. With
    `heal` the rest of the chain is reconnected in its place and the block
    leaves alone. The caller decides where the block goes next.
    """
    edited = copy.deepcopy(workspace)
    cut: list[dict] = []

    def visit(node: dict) -> None:
        for key, value in list(node.items()):
            if isinstance(value, dict) and isinstance(value.get("block"), dict):
                block = value["block"]
                if block.get("id") == block_id:
                    cut.append(block)
                    if heal and "next" in block:
                        node[key] = block.pop("next")
                    else:
                        del node[key]

    walk(edited, visit)
    (block,) = cut
    return edited, block


def orphaned(workspace: dict, block_id: str = DRAWING_ID) -> dict:
    """`workspace` after the drawing `block_id` was bumped out as its own root."""
    edited, drawing = detach(workspace, block_id, heal=True)
    drawing.update(x=240, y=240)
    roots(edited).append(drawing)
    return edited


def helper() -> dict:
    return section_representation(PROGRAM, SECTION)


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def refusal(workspace: dict, section_id: str = SECTION) -> str:
    with pytest.raises(UnsupportedBlocklyStructureError) as caught:
        blockly_section_from_state(section_id, workspace, helper()["preserved"])
    return str(caught.value)


# =============================================================================
# 1. function + an orphaned drawing  ->  accepted, content from the RECORD
# =============================================================================


def test_the_shipped_helper_really_has_the_drawing_in_its_body_chain() -> None:
    """Anchors the fixture: one root, the drawing is the tail of the BODY chain."""
    rep = helper()
    (container,) = roots(rep["workspace"])
    assert container["type"] == "function_implementation"
    assert by_id(rep["workspace"], DRAWING_ID)["type"] == PRESERVED_BLOCK_TYPE
    assert [r["id"] for r in rep["preserved"]] == [DRAWING_ID]


def test_a_function_beside_an_orphaned_drawing_is_accepted_and_round_trips() -> None:
    rep = helper()
    saved = orphaned(rep["workspace"])
    assert [r["type"] for r in roots(saved)] == ["function_implementation", PRESERVED_BLOCK_TYPE]

    section = section_from_state(SECTION, saved, rep["preserved"])
    assert section.block is not None and section.representable
    rebuilt = program_with_section(PROGRAM, section)
    assert source_for_program(rebuilt) == source_for_program(PROGRAM)


def test_the_orphaned_drawings_text_is_never_trusted() -> None:
    """Content from the record, however the drawing was defaced."""
    rep = helper()
    saved = deface(orphaned(rep["workspace"]))
    assert by_id(saved, DRAWING_ID)["fields"][PRESERVED_TEXT_FIELD] == EVIL

    section = section_from_state(SECTION, saved, rep["preserved"])
    text = source_for_program(program_with_section(PROGRAM, section))
    assert EVIL not in text
    assert rep["preserved"][0]["text"] in text
    assert text == source_for_program(PROGRAM)


def test_an_orphaned_drawing_that_names_no_record_is_ignored_too() -> None:
    rep = helper()
    saved = orphaned(rep["workspace"])
    roots(saved)[-1]["id"] = "forged.7"
    section = section_from_state(SECTION, saved, rep["preserved"])
    assert source_for_program(program_with_section(PROGRAM, section)) == source_for_program(PROGRAM)


def test_two_orphaned_drawings_stacked_together_are_still_only_drawings() -> None:
    source = loop_source("one();", f"a = {OPAQUE};", f"b = {OPAQUE};", "two();")  # two fragments
    rep = representation(source)
    ws = copy.deepcopy(rep["workspace"])
    (container,) = roots(ws)
    name = body_input(container)
    one, first, second, two = unlink(container["inputs"][name])
    container["inputs"][name] = relink([one, two])
    first["next"] = {"block": second}
    first.update(x=300, y=300)
    roots(ws).append(first)
    assert [r["type"] for r in roots(ws)] == [container["type"], PRESERVED_BLOCK_TYPE]

    assert apply(source, ws, rep["preserved"]) == source


def test_an_orphaned_drawings_record_takes_the_index_rule_exactly_once() -> None:
    """No drawing is in the body, so the record lands at the index it was opened at.

    The same rule that already restores a fragment whose drawing was deleted; an
    orphan merely has no position of its own. Inserting a block above shifts
    nothing: index 1 of the new body is where the fragment goes.
    """
    source = loop_source("one();", f"a = {OPAQUE};", "two();")
    rep = representation(source)
    ws, drawing = detach(rep["workspace"], "loop.1", heal=True)
    (container,) = roots(ws)
    name = body_input(container)
    nodes = unlink(container["inputs"][name])
    inserted = {"type": "call_existing_function", "fields": {"NAME": "inserted"}}
    container["inputs"][name] = relink([inserted, *nodes])
    roots(ws).append(drawing)

    text = apply(source, ws, rep["preserved"])
    assert text == loop_source("inserted();", f"a = {OPAQUE};", "one();", "two();")
    assert text.count(OPAQUE) == 1


# =============================================================================
# 2. in-chain ordering is unchanged by an orphan beside it
# =============================================================================


def test_an_in_chain_drawing_still_places_its_fragment_with_an_orphan_beside_it() -> None:
    """The drawing in the body is authoritative for POSITION; the orphan twin is inert.

    The twin carries the same id. It must neither raise 'drawn more than once'
    nor place the fragment a second time.
    """
    source = loop_source("one();", f"a = {OPAQUE};", "two();")
    rep = representation(source)
    name = body_input(roots(rep["workspace"])[0])
    moved = reorder(rep["workspace"], None, name, ["loop.0", "loop.2", "loop.1"])
    twin = copy.deepcopy(by_id(moved, "loop.1"))
    twin.pop("next", None)
    twin.update(x=400, y=400)
    roots(moved).append(twin)

    text = apply(source, moved, rep["preserved"])
    assert text == loop_source("one();", "two();", f"a = {OPAQUE};")
    assert text.count(OPAQUE) == 1


# =============================================================================
# 3. a genuine extra root is still refused - and now NAMED
# =============================================================================


def test_a_function_beside_a_genuine_stray_block_is_refused_and_names_it() -> None:
    saved = copy.deepcopy(helper()["workspace"])
    roots(saved).append(copy.deepcopy(STRAY))
    message = refusal(saved)
    assert "a section is one construct" in message
    assert "2 top-level blocks" in message
    assert "function_implementation[helper_applyCommand]" in message
    assert "call_existing_function[stray1]" in message


def test_an_orphaned_drawing_does_not_hide_a_genuine_stray_block() -> None:
    saved = orphaned(helper()["workspace"])
    roots(saved).append(copy.deepcopy(STRAY))
    message = refusal(saved)
    # The drawing is not counted: container + stray = 2, and only those are named.
    assert "2 top-level blocks" in message
    assert "call_existing_function[stray1]" in message
    assert "preserved_source" not in message


def test_several_genuine_roots_are_refused_and_every_one_is_named() -> None:
    saved = copy.deepcopy(helper()["workspace"])
    for index in range(2):
        stray = copy.deepcopy(STRAY)
        stray["id"] = f"stray{index + 1}"
        roots(saved).append(stray)
    message = refusal(saved)
    assert "3 top-level blocks" in message
    for name in ("function_implementation[helper_applyCommand]", "[stray1]", "[stray2]"):
        assert name in message


def test_the_refusal_names_a_bounded_number_of_roots() -> None:
    saved = copy.deepcopy(helper()["workspace"])
    for index in range(40):
        stray = copy.deepcopy(STRAY)
        stray["id"] = f"stray{index}"
        roots(saved).append(stray)
    message = refusal(saved)
    assert "41 top-level blocks" in message
    assert "and 36 more" in message
    assert len(message) < 600


def test_a_stray_stack_that_still_holds_the_drawing_is_a_genuine_root() -> None:
    """Dragging the `if` out takes its tail: [if -> drawing] is real work, not an orphan."""
    rep = helper()
    saved, stack = detach(rep["workspace"], f"{SECTION}.0")
    assert stack["next"]["block"]["id"] == DRAWING_ID
    roots(saved).append(stack)
    message = refusal(saved)
    assert "logic_if[helper_applyCommand.0]" in message


def test_a_real_block_chained_under_an_orphaned_drawing_is_not_ignored() -> None:
    saved = orphaned(helper()["workspace"])
    roots(saved)[-1]["next"] = {"block": copy.deepcopy(STRAY)}
    message = refusal(saved)
    assert "2 top-level blocks" in message
    assert "preserved_source[helper_applyCommand.1] carrying call_existing_function[stray1]" in message


def test_a_drawing_that_carries_inputs_is_not_a_drawing() -> None:
    saved = orphaned(helper()["workspace"])
    roots(saved)[-1]["inputs"] = {"X": {"block": {"type": "logic_true"}}}
    assert "2 top-level blocks" in refusal(saved)


def test_a_block_with_no_id_is_still_named_by_its_type() -> None:
    saved = copy.deepcopy(helper()["workspace"])
    roots(saved).append({"type": "logic_true"})
    assert "logic_true" in refusal(saved)


def test_the_original_two_construct_refusal_is_unchanged() -> None:
    with pytest.raises(UnsupportedBlocklyStructureError, match="top-level"):
        blockly_section_from_state(
            "setup",
            {"blocks": {"languageVersion": 0, "blocks": [{"type": "arduino_setup"}, {"type": "arduino_loop"}]}},
        )


# =============================================================================
# 4. a lone drawing is never the section's construct
# =============================================================================


def lone_drawing_workspace() -> dict:
    ws = orphaned(helper()["workspace"])
    ws["blocks"]["blocks"] = [roots(ws)[-1]]
    return ws


def test_a_lone_drawing_reads_as_no_construct_at_all() -> None:
    section = blockly_section_from_state(SECTION, lone_drawing_workspace(), helper()["preserved"])
    assert section.block is None
    assert not section.representable


def test_a_lone_drawing_cannot_replace_a_sections_construct() -> None:
    rep = helper()
    section = section_from_state(SECTION, lone_drawing_workspace(), rep["preserved"])
    with pytest.raises(SectionNotRepresentableError):
        program_with_section(PROGRAM, section)


def test_a_lone_drawing_does_not_make_an_unrepresentable_section_editable() -> None:
    drawing = {"type": PRESERVED_BLOCK_TYPE, "id": "global.0", "fields": {PRESERVED_TEXT_FIELD: "int x;"}}
    section = section_from_state(
        "global", {"blocks": {"languageVersion": 0, "blocks": [drawing]}}, []
    )
    with pytest.raises(SectionNotRepresentableError):
        program_with_section(PROGRAM, section)


# =============================================================================
# 5. the same through the real BuildWorkspace write path (Panel 1's policy)
# =============================================================================


def test_the_workspace_accepts_an_orphaned_drawing_and_leaves_the_firmware_identical() -> None:
    live = panel_one_workspace()
    rep = live.section_blockly(SKETCH_NAME, SECTION)
    before = live.fingerprint()
    live.apply_section_blockly(SKETCH_NAME, SECTION, orphaned(rep["workspace"]), rep["preserved"])
    assert live.fingerprint() == before


def test_the_workspace_refuses_a_stray_block_and_changes_nothing() -> None:
    live = panel_one_workspace()
    rep = live.section_blockly(SKETCH_NAME, SECTION)
    saved = copy.deepcopy(rep["workspace"])
    roots(saved).append(copy.deepcopy(STRAY))
    before = live.fingerprint()
    with pytest.raises(ProgramApplyError, match=r"call_existing_function\[stray1\]"):
        live.apply_section_blockly(SKETCH_NAME, SECTION, saved, rep["preserved"])
    assert live.fingerprint() == before


def test_the_workspace_refuses_a_lone_drawing_and_changes_nothing() -> None:
    live = panel_one_workspace()
    rep = live.section_blockly(SKETCH_NAME, SECTION)
    before = live.fingerprint()
    with pytest.raises(ProgramApplyError):
        live.apply_section_blockly(
            SKETCH_NAME, SECTION, lone_drawing_workspace(), rep["preserved"]
        )
    assert live.fingerprint() == before


# =============================================================================
# 6. REAL Blockly: the exact orphan from the live failure
# =============================================================================


@_needs_blockly
def test_real_blockly_bumps_the_drawing_out_and_the_backend_accepts_it(blockly) -> None:
    """`return_void` has no next socket: dropped between the `if` and the drawing,
    Blockly bumps the drawing out as its own top-level block. This is the saved
    workspace the live session produced, and it was refused before the fix."""
    live = panel_one_workspace()
    opened = live.section_blockly(SKETCH_NAME, SECTION)
    ops = [new("guard", "return_void"), {"op": "next", "parent": f"id:{SECTION}.0", "child": "guard"}]
    result = blockly(edits={"orphan": {"state": opened["workspace"], "ops": ops}})
    assert result["errors"] == {}, result["errors"]
    saved = result["saved"]["orphan"]

    # Real Blockly really did produce the two roots, the second being the drawing.
    top = roots(saved)
    assert [b["type"] for b in top] == ["function_implementation", PRESERVED_BLOCK_TYPE]
    assert top[1]["id"] == DRAWING_ID and "next" not in top[1]

    live.apply_section_blockly(SKETCH_NAME, SECTION, saved, opened["preserved"])
    region = normalized(live.region_source(SKETCH_NAME, SECTION))
    comment = normalized(opened["preserved"][0]["text"])
    assert region.count(comment) == 1
    assert region.count("return;") == 1
    assert region.index("motorStop();") < region.index(comment) < region.index("return;")


@_needs_blockly
def test_real_blockly_orphan_content_still_comes_from_the_record(blockly) -> None:
    live = panel_one_workspace()
    opened = live.section_blockly(SKETCH_NAME, SECTION)
    ops = [new("guard", "return_void"), {"op": "next", "parent": f"id:{SECTION}.0", "child": "guard"}]
    saved = blockly(edits={"orphan": {"state": opened["workspace"], "ops": ops}})["saved"]["orphan"]
    roots(saved)[1]["fields"][PRESERVED_TEXT_FIELD] = EVIL

    live.apply_section_blockly(SKETCH_NAME, SECTION, saved, opened["preserved"])
    region = live.region_source(SKETCH_NAME, SECTION)
    assert EVIL not in region
    assert normalized(opened["preserved"][0]["text"]) in normalized(region)


@_needs_blockly
def test_real_blockly_a_floating_block_is_still_refused_and_named(blockly) -> None:
    live = panel_one_workspace()
    opened = live.section_blockly(SKETCH_NAME, SECTION)
    ops = [new("loose", "call_existing_function", NAME="somewhere")]
    saved = blockly(edits={"stray": {"state": opened["workspace"], "ops": ops}})["saved"]["stray"]
    assert len(roots(saved)) == 2

    before = live.fingerprint()
    with pytest.raises(ProgramApplyError, match=r"2 top-level blocks.*call_existing_function\["):
        live.apply_section_blockly(SKETCH_NAME, SECTION, saved, opened["preserved"])
    assert live.fingerprint() == before


@_needs_blockly
def test_real_blockly_a_displaced_stack_that_holds_real_blocks_is_still_refused(blockly) -> None:
    """A stack ending in `return_void` dropped on the body displaces the whole
    chain - the `if` AND the drawing under it - which is real work, not an orphan."""
    live = panel_one_workspace()
    opened = live.section_blockly(SKETCH_NAME, SECTION)
    ops = [
        new("only", "return_void"),
        {"op": "statement", "parent": f"id:{SECTION}", "input": "BODY", "child": "only"},
    ]
    saved = blockly(edits={"displaced": {"state": opened["workspace"], "ops": ops}})["saved"]["displaced"]
    assert [b["type"] for b in roots(saved)] == ["function_implementation", "logic_if"]

    before = live.fingerprint()
    with pytest.raises(ProgramApplyError, match=r"logic_if\[helper_applyCommand\.0\]"):
        live.apply_section_blockly(SKETCH_NAME, SECTION, saved, opened["preserved"])
    assert live.fingerprint() == before
