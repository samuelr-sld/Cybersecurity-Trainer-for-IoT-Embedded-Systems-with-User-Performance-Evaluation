"""A preserved fragment keeps the POSITION its read-only block was saved at.

    block position = editable (it moves with the chain around it)
    block content  = read-only (always the trusted record's text)

P4.0 found the defect this file pins shut: the reverse reader spliced every
preserved fragment back at the body index it was OPENED at, ignoring where its
read-only `preserved_source` block sat when the workspace was saved. Inserting
a block above it, or dragging a statement past it, therefore silently
reordered real C++:

    one();                    inserted();
    a = table[i];     ->      a = table[i];    <- pulled up to its old index
    two();                    one();
                              two();

Every test below states two things separately: WHERE the fragment ends up (from
the saved chain) and WHAT it says (from the record, never the drawing's TEXT).
The real-Blockly cases at the bottom drive the student's own block definitions;
the rest edit the saved JSON directly, which is also how a hostile payload
would arrive.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    SectionBlocklyError,
    program_with_section,
    section_from_state,
    section_representation,
)
from app.build.workspace import _opaque_preserved_texts

from tests.test_real_blockly import blockly, pytestmark as _needs_blockly  # noqa: F401

OPAQUE = "table[i]"  # outside the expression grammar, so it stays preserved source
EVIL = "system(evil);"

PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parent.parent
    / "panels" / "smart-home-mqtt-control" / "firmware" / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)


def loop_source(*lines: str) -> str:
    return "void loop() {\n" + "".join(f"  {line}\n" for line in lines) + "}\n"


def representation(source: str, section: str = "loop") -> dict:
    return section_representation(program_for_source(source), section)


def apply(source: str, workspace: dict, records: list[dict], section: str = "loop") -> str:
    program = program_for_source(source)
    return source_for_program(program_with_section(program, section_from_state(section, workspace, records)))


# --- chain surgery on saved JSON: what a drag ends in -----------------------


def walk(node, visit) -> None:
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk(value, visit)
    elif isinstance(node, list):
        for value in node:
            walk(value, visit)


def by_id(workspace: dict, block_id: str) -> dict:
    found: list[dict] = []
    walk(workspace, lambda n: found.append(n) if n.get("id") == block_id else None)
    (block,) = found
    return block


def unlink(link: dict | None) -> list[dict]:
    """A `next`-linked chain as a list of nodes, each with its `next` removed."""
    nodes: list[dict] = []
    node = None if link is None else link["block"]
    while node is not None:
        following = node.pop("next", None)
        nodes.append(node)
        node = None if following is None else following["block"]
    return nodes


def relink(nodes: list[dict]) -> dict | None:
    link = None
    for node in reversed(nodes):
        if link is not None:
            node["next"] = link
        link = {"block": node}
    return link


def reorder(workspace: dict, owner_id: str | None, input_name: str, order: list[str]) -> dict:
    """The workspace with one body's chain rebuilt in `order` (block ids).

    `owner_id` None is the section's container. Every id must already be in
    that chain; one left out is deleted. The result is what Blockly saves
    after the matching drags.
    """
    edited = copy.deepcopy(workspace)
    owner = edited["blocks"]["blocks"][0] if owner_id is None else by_id(edited, owner_id)
    nodes = {node["id"]: node for node in unlink(owner["inputs"].get(input_name))}
    assert set(order) <= set(nodes), (sorted(nodes), order)
    owner["inputs"][input_name] = relink([nodes[i] for i in order])
    return edited


def deface(workspace: dict, text: str = EVIL) -> dict:
    """Every drawing's TEXT overwritten - content the backend must never read."""
    walk(
        workspace,
        lambda n: n["fields"].__setitem__(PRESERVED_TEXT_FIELD, text)
        if n.get("type") == PRESERVED_BLOCK_TYPE
        else None,
    )
    return workspace


# loop.0 one / loop.1 a=OPAQUE / loop.2 two / loop.3 b=OPAQUE / loop.4 three
FLAT = loop_source("one();", f"a = {OPAQUE};", "two();", f"b = {OPAQUE};", "three();")
LINE = {
    "loop.0": "one();",
    "loop.1": f"a = {OPAQUE};",
    "loop.2": "two();",
    "loop.3": f"b = {OPAQUE};",
    "loop.4": "three();",
}


def flat_in(order: list[str]) -> tuple[str, str]:
    """(what the backend wrote, what the order says it must write)."""
    rep = representation(FLAT)
    edited = deface(reorder(rep["workspace"], None, "DO", order))
    return apply(FLAT, edited, rep["preserved"]), loop_source(*(LINE[i] for i in order))


IN_IF = loop_source(
    "if (ready) {", "  start();", f"  a = {OPAQUE};", "  finish();", "}", "after();"
)

DEEP = loop_source(
    "if (ready) {",
    "  for (int i = 0; i < 3; i++) {",
    "    if (i > 1) {",
    f"      inner = {OPAQUE};",
    "      tick();",
    "    } else if (i > 0) {",
    "      mid();",
    f"      middle = {OPAQUE};",
    "    }",
    "  }",
    "} else {",
    f"  outer = {OPAQUE};",
    "  stop();",
    "}",
)


# =============================================================================
# A / L. Nothing moved: byte-identical, every shape
# =============================================================================


@pytest.mark.parametrize("source", [FLAT, IN_IF, DEEP], ids=["flat", "in-if", "deep"])
def test_a_unmoved_preserved_blocks_round_trip_byte_identical(source: str) -> None:
    rep = representation(source)
    assert apply(source, copy.deepcopy(rep["workspace"]), rep["preserved"]) == source


def test_l_unedited_panel_one_source_is_byte_identical_through_every_section() -> None:
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    program = program_for_source(source)
    rebuilt = program
    for section in program.sections:
        if section.operation is None:
            continue
        rep = section_representation(program, section.section_id)
        rebuilt = program_with_section(
            rebuilt, section_from_state(section.section_id, rep["workspace"], rep["preserved"])
        )
    assert source_for_program(rebuilt) == source_for_program(program)


# =============================================================================
# B-E. Moves in one body. Position from the chain, content from the record.
# =============================================================================


def test_b_moving_a_preserved_block_upward() -> None:
    written, expected = flat_in(["loop.1", "loop.0", "loop.2", "loop.3", "loop.4"])
    assert written == expected
    assert written.index(f"a = {OPAQUE};") < written.index("one();")  # position
    assert EVIL not in written                                         # content


def test_c_moving_a_preserved_block_downward() -> None:
    written, expected = flat_in(["loop.0", "loop.2", "loop.1", "loop.3", "loop.4"])
    assert written == expected
    assert written.index("two();") < written.index(f"a = {OPAQUE};")
    assert EVIL not in written


def test_d_moving_several_preserved_blocks() -> None:
    written, expected = flat_in(["loop.3", "loop.0", "loop.4", "loop.2", "loop.1"])
    assert written == expected
    assert written.index(f"b = {OPAQUE};") < written.index(f"a = {OPAQUE};")
    assert written.count(OPAQUE) == 2 and EVIL not in written


def test_e_moving_an_editable_block_around_preserved_ones() -> None:
    written, expected = flat_in(["loop.4", "loop.0", "loop.1", "loop.2", "loop.3"])
    assert written == expected
    # The fragments themselves did not move relative to their neighbours.
    assert written.index("one();") < written.index(f"a = {OPAQUE};") < written.index("two();")


def test_e_inserting_a_new_block_above_a_preserved_block_does_not_pull_it_up() -> None:
    """The exact P4.0 reproduction, as saved JSON."""
    rep = representation(loop_source("one();", f"a = {OPAQUE};", "two();"))
    edited = copy.deepcopy(rep["workspace"])
    container = edited["blocks"]["blocks"][0]
    container["inputs"]["DO"] = {
        "block": {
            "type": "call_existing_function",
            "id": "fresh-uuid",
            "fields": {"NAME": "inserted"},
            "next": container["inputs"]["DO"],
        }
    }
    written = apply(loop_source("one();", f"a = {OPAQUE};", "two();"), edited, rep["preserved"])
    assert written == loop_source("inserted();", "one();", f"a = {OPAQUE};", "two();")


def test_e_editing_an_ordinary_block_leaves_preserved_positions_alone() -> None:
    rep = representation(FLAT)
    edited = copy.deepcopy(rep["workspace"])
    by_id(edited, "loop.2")["fields"]["NAME"] = "deux"
    assert apply(FLAT, edited, rep["preserved"]) == FLAT.replace("two()", "deux()")


# =============================================================================
# F. Nested bodies, several levels, and across bodies
# =============================================================================


def test_f_reordering_inside_an_if_body() -> None:
    rep = representation(IN_IF)
    # loop.0 = the if; its DO holds loop.0.0 start / loop.0.1 fragment / loop.0.2 finish
    edited = deface(reorder(rep["workspace"], "loop.0", "DO", ["loop.0.1", "loop.0.0", "loop.0.2"]))
    written = apply(IN_IF, edited, rep["preserved"])
    assert written == loop_source(
        "if (ready) {", f"  a = {OPAQUE};", "  start();", "  finish();", "}", "after();"
    )


def test_f_reordering_at_two_nested_levels_at_once() -> None:
    rep = representation(DEEP)
    ws = rep["workspace"]
    # innermost if (i > 1): DO = [inner fragment, tick()] -> [tick(), inner fragment]
    inner_if = "loop.0.0.0"
    assert by_id(ws, inner_if)["type"] == "logic_if"
    ws = reorder(ws, inner_if, "DO", [f"{inner_if}.0", f"{inner_if}.1"][::-1])
    # outermost else: [outer fragment, stop()] -> [stop(), outer fragment]
    ws = deface(reorder(ws, "loop.0", "ELSE", ["loop.0.ELSE.1", "loop.0.ELSE.0"]))
    written = apply(DEEP, ws, rep["preserved"])
    assert written == loop_source(
        "if (ready) {",
        "  for (int i = 0; i < 3; i++) {",
        "    if (i > 1) {",
        "      tick();",
        f"      inner = {OPAQUE};",
        "    } else if (i > 0) {",
        "      mid();",
        f"      middle = {OPAQUE};",  # untouched level: untouched position
        "    }",
        "  }",
        "} else {",
        "  stop();",
        f"  outer = {OPAQUE};",
        "}",
    )


def test_f_a_preserved_block_moved_into_another_body_goes_there() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    if_block = by_id(edited, "loop.0")
    body = unlink(if_block["inputs"]["DO"])
    fragment = next(node for node in body if node["type"] == PRESERVED_BLOCK_TYPE)
    if_block["inputs"]["DO"] = relink([node for node in body if node is not fragment])
    # ... and into the section's own body, after `after();`
    after = by_id(edited, "loop.1")
    after["next"] = {"block": fragment}
    written = apply(IN_IF, deface(edited), rep["preserved"])
    assert written == loop_source(
        "if (ready) {", "  start();", "  finish();", "}", "after();", f"a = {OPAQUE};"
    )


def test_f_a_fragment_inside_a_deleted_block_still_goes_with_it() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    container = edited["blocks"]["blocks"][0]
    container["inputs"]["DO"] = {"block": by_id(edited, "loop.1")}  # the if is gone
    written = apply(IN_IF, edited, rep["preserved"])
    assert written == loop_source("after();")


# =============================================================================
# 9. Deletion keeps the EXISTING policy
# =============================================================================


def test_deleting_a_drawing_restores_the_fragment_at_its_record_index() -> None:
    """Unchanged policy: a drawing is not how a fragment is removed (the block
    is undeletable in the editor); a record with no drawing is placed by index."""
    rep = representation(FLAT)
    edited = reorder(rep["workspace"], None, "DO", ["loop.0", "loop.2", "loop.3", "loop.4"])
    assert apply(FLAT, edited, rep["preserved"]) == FLAT


def test_removing_the_record_removes_the_fragment_even_if_still_drawn() -> None:
    """Build Mode's CLEAR: the record is the fragment; a drawing without one is inert."""
    rep = representation(FLAT)
    kept = [r for r in rep["preserved"] if r["id"] != "loop.1"]
    written = apply(FLAT, copy.deepcopy(rep["workspace"]), kept)
    assert written == loop_source("one();", "two();", f"b = {OPAQUE};", "three();")


def test_a_moved_drawing_and_an_undrawn_record_in_one_body() -> None:
    rep = representation(FLAT)
    # loop.3's drawing deleted (index fallback: 3), loop.1's moved to the end.
    edited = reorder(rep["workspace"], None, "DO", ["loop.0", "loop.2", "loop.4", "loop.1"])
    written = apply(FLAT, edited, rep["preserved"])
    assert written == loop_source("one();", "two();", "three();", f"b = {OPAQUE};", f"a = {OPAQUE};")


# =============================================================================
# G / H. Forged text, forged ids, duplicated ids
# =============================================================================


def test_g_forged_text_on_a_moved_block_never_reaches_the_source() -> None:
    rep = representation(FLAT)
    edited = deface(reorder(rep["workspace"], None, "DO", ["loop.1", "loop.0", "loop.2", "loop.3", "loop.4"]))
    written = apply(FLAT, edited, rep["preserved"])
    assert EVIL not in written
    assert written.count(f"a = {OPAQUE};") == 1


def test_g_a_forged_block_with_an_unknown_id_is_ignored_wherever_it_sits() -> None:
    rep = representation(FLAT)
    edited = copy.deepcopy(rep["workspace"])
    one = by_id(edited, "loop.0")
    one["next"] = {
        "block": {"type": PRESERVED_BLOCK_TYPE, "id": "forged", "fields": {PRESERVED_TEXT_FIELD: EVIL},
                  "next": one["next"]}
    }
    assert apply(FLAT, edited, rep["preserved"]) == FLAT


def test_g_a_forged_block_with_no_id_is_ignored() -> None:
    rep = representation(FLAT)
    edited = copy.deepcopy(rep["workspace"])
    one = by_id(edited, "loop.0")
    one["next"] = {
        "block": {"type": PRESERVED_BLOCK_TYPE, "fields": {PRESERVED_TEXT_FIELD: EVIL}, "next": one["next"]}
    }
    assert apply(FLAT, edited, rep["preserved"]) == FLAT


def test_h_one_record_drawn_twice_is_refused() -> None:
    rep = representation(FLAT)
    edited = copy.deepcopy(rep["workspace"])
    copy_of_a = copy.deepcopy(by_id(edited, "loop.1"))
    copy_of_a.pop("next", None)
    copy_of_a["fields"][PRESERVED_TEXT_FIELD] = EVIL
    three = by_id(edited, "loop.4")
    three["next"] = {"block": copy_of_a}
    with pytest.raises(SectionBlocklyError, match="drawn more than once"):
        section_from_state("loop", edited, rep["preserved"])


def test_h_one_record_drawn_twice_across_nested_bodies_is_refused() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    duplicate = copy.deepcopy(by_id(edited, "loop.0.1"))
    duplicate.pop("next", None)
    by_id(edited, "loop.1")["next"] = {"block": duplicate}
    with pytest.raises(SectionBlocklyError, match="drawn more than once"):
        section_from_state("loop", edited, rep["preserved"])


def test_h_two_records_sharing_an_id_are_refused() -> None:
    rep = representation(FLAT)
    a, b = rep["preserved"]
    with pytest.raises(SectionBlocklyError, match="share the id"):
        section_from_state("loop", rep["workspace"], [a, dict(b, id=a["id"])])


def test_security_ownership_still_sees_a_moved_opaque_fragment() -> None:
    """Moving a fragment cannot hide it from `SecurityRegionOwnershipError`."""
    rep = representation(FLAT)
    edited = reorder(rep["workspace"], None, "DO", ["loop.3", "loop.1", "loop.0", "loop.2", "loop.4"])
    submitted = section_from_state("loop", edited, rep["preserved"])
    assert sorted(_opaque_preserved_texts(submitted)) == [f"a = {OPAQUE};", f"b = {OPAQUE};"]


# =============================================================================
# 11. Backward compatibility
# =============================================================================


def test_drawings_without_ids_fall_back_to_record_indices() -> None:
    """A workspace whose drawings carry no ids reads exactly as it always did."""
    rep = representation(FLAT)
    edited = copy.deepcopy(rep["workspace"])
    walk(edited, lambda n: n.pop("id", None) if n.get("type") == PRESERVED_BLOCK_TYPE else None)
    assert apply(FLAT, edited, rep["preserved"]) == FLAT


def test_records_without_ids_fall_back_to_record_indices() -> None:
    rep = representation(FLAT)
    legacy = [{k: v for k, v in r.items() if k != "id"} for r in rep["preserved"]]
    assert apply(FLAT, copy.deepcopy(rep["workspace"]), legacy) == FLAT


# =============================================================================
# I. Panel 1's callback_onMessage fragments (one nested, one section-level)
# =============================================================================


def test_i_panel_one_callback_fragments_survive_an_edit_in_place() -> None:
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    program = program_for_source(source)
    rep = section_representation(program, "callback_onMessage")
    records = rep["preserved"]
    assert {r["parentId"] for r in records} == {"callback_onMessage", "callback_onMessage.2"}
    edited = deface(copy.deepcopy(rep["workspace"]))
    rebuilt = source_for_program(
        program_with_section(program, section_from_state("callback_onMessage", edited, records))
    )
    assert rebuilt == source_for_program(program)
    for record in records:
        assert rebuilt.count(record["text"]) == 1
    assert EVIL not in rebuilt


# =============================================================================
# Real Blockly: the moves a student can actually make
# =============================================================================


def blockly_apply(blockly, source: str, ops: list[dict]) -> str:
    rep = representation(source)
    result = blockly(edits={"x": {"state": rep["workspace"], "ops": ops}})
    assert result["errors"] == {}, result["errors"]
    return apply(source, result["saved"]["x"], rep["preserved"])


@_needs_blockly
def test_real_blockly_dragging_a_block_above_a_preserved_one_moves_it_down(blockly) -> None:
    source = loop_source("one();", f"a = {OPAQUE};", "two();")
    # two() dropped under one(): Blockly re-attaches the fragment below two().
    ops = [{"op": "next", "parent": "id:loop.0", "child": "id:loop.2"}]
    assert blockly_apply(blockly, source, ops) == loop_source("one();", "two();", f"a = {OPAQUE};")


@_needs_blockly
def test_real_blockly_lifting_a_block_carries_the_preserved_one_beneath_it(blockly) -> None:
    """Blockly's own rule, observed: lifting one() does not heal the stack past
    the unmovable fragment under it - the fragment travels with one(). The
    backend writes exactly the order Blockly saved, fragment included."""
    source = loop_source("one();", f"a = {OPAQUE};", "two();")
    ops = [
        {"op": "unplug", "block": "id:loop.0"},
        {"op": "next", "parent": "id:loop.2", "child": "id:loop.0"},
    ]
    assert blockly_apply(blockly, source, ops) == loop_source("two();", "one();", f"a = {OPAQUE};")


@_needs_blockly
def test_real_blockly_nested_fragment_follows_its_block(blockly) -> None:
    source = IN_IF
    ops = [{"op": "next", "parent": "id:loop.0.0", "child": "id:loop.0.2"}]
    assert blockly_apply(blockly, source, ops) == loop_source(
        "if (ready) {", "  start();", "  finish();", f"  a = {OPAQUE};", "}", "after();"
    )
