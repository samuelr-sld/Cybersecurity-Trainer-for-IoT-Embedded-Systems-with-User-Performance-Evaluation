"""Nested preserved source is AUTHORITATIVE, exactly like section-level source.

Source the toolbox cannot draw yet is drawn as a read-only `preserved_source`
block. At the top of a function the authoritative copy of each fragment is the
`preserved` record list that travels beside the workspace. These tests pin that
the same is true INSIDE a block's body, at any depth and in any statement input
(`if`, `else if`, `else`, `for`):

* every record locates its fragment by the id of the block whose body holds it
  (`parentId`), which input (`input`) and where (`index`), and the workspace
  carries those ids;
* the reverse reader takes the fragment's CONTENT from the RECORD - never from
  the drawn block's text - and its POSITION from where the drawing sits (see
  `test_build_preserved_ordering.py` for the ordering rule in full);
* so a normal round trip is byte-identical, editing a sibling cannot lose the
  fragment, a mutated or deleted drawing changes no content, a forged drawing
  is ignored, and a fragment leaves the program only with the block that held
  it (or with its record).

Nothing here checks cryptographic integrity - the goal is authoritative
preservation and deterministic round tripping.
"""

from __future__ import annotations

import copy

import pytest

from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    SectionBlocklyError,
    program_with_section,
    section_from_state,
    section_representation,
)

OPAQUE = "table[i]"  # indexing: outside the expression grammar, so it stays source


def loop_source(*lines: str) -> str:
    return "void loop() {\n" + "".join(f"  {line}\n" for line in lines) + "}\n"


def representation(source: str) -> dict:
    return section_representation(program_for_source(source), "loop")


def apply(source: str, workspace: dict, records: list[dict]) -> str:
    """Send a (possibly edited) workspace back and return the regenerated C++."""
    program = program_for_source(source)
    section = section_from_state("loop", workspace, records)
    return source_for_program(program_with_section(program, section))


def untouched(source: str) -> str:
    rep = representation(source)
    return apply(source, copy.deepcopy(rep["workspace"]), rep["preserved"])


def walk(node, visit) -> None:
    """Depth-first over every dict in a workspace state."""
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk(value, visit)
    elif isinstance(node, list):
        for value in node:
            walk(value, visit)


def drawn_fragments(workspace: dict) -> list[dict]:
    found: list[dict] = []
    walk(workspace, lambda n: found.append(n) if n.get("type") == PRESERVED_BLOCK_TYPE else None)
    return found


def by_id(workspace: dict, block_id: str) -> dict:
    found: list[dict] = []
    walk(workspace, lambda n: found.append(n) if n.get("id") == block_id else None)
    (block,) = found
    return block


def strip_drawn(node):
    """The same workspace with EVERY drawn preserved block deleted and relinked."""
    if isinstance(node, list):
        return [strip_drawn(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: strip_drawn(value) for key, value in node.items()}
    while node.get("type") == PRESERVED_BLOCK_TYPE:
        following = (node.get("next") or {}).get("block")
        if following is None:
            return None
        node = following
    return node


def without_none(node):
    """Drop the empty `{"block": None}` links `strip_drawn` leaves behind."""
    if isinstance(node, list):
        return [without_none(item) for item in node]
    if isinstance(node, dict):
        return {
            key: without_none(value)
            for key, value in node.items()
            if not (isinstance(value, dict) and value == {"block": None})
        }
    return node


# --- the shapes under test --------------------------------------------------

TOP_LEVEL = loop_source(f"value = {OPAQUE};", "tick();", f"other = {OPAQUE};")

IN_IF = loop_source(
    "if (ready) {",
    "  start();",
    f"  a = {OPAQUE};",
    "  finish();",
    "}",
)

IN_ELSE = loop_source(
    "if (ready) {",
    "  start();",
    f"  a = {OPAQUE};",
    "} else {",
    "  stop();",
    f"  b = {OPAQUE};",
    "}",
)

IN_FOR = loop_source(
    "for (int i = 0; i < 3; i++) {",
    "  count += 1;",
    f"  a = {OPAQUE};",
    "}",
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
    "}",
)

SHAPES = {
    "top level": TOP_LEVEL,
    "inside an if": IN_IF,
    "inside if and else": IN_ELSE,
    "inside a for": IN_FOR,
    "inside nested control flow": DEEP,
}


# --- a normal round trip ----------------------------------------------------


@pytest.mark.parametrize("source", SHAPES.values(), ids=SHAPES.keys())
def test_an_untouched_round_trip_is_byte_identical(source: str) -> None:
    assert program_for_source(source) is not None
    assert source_for_program(program_for_source(source)) == source
    assert untouched(source) == source


def test_every_fragment_is_drawn_and_recorded_where_it_sits() -> None:
    rep = representation(DEEP)
    drawn = drawn_fragments(rep["workspace"])
    records = rep["preserved"]
    assert len(drawn) == len(records) == 3
    for record in records:
        block = by_id(rep["workspace"], record["id"])
        assert block["type"] == PRESERVED_BLOCK_TYPE
        assert block["fields"][PRESERVED_TEXT_FIELD] == record["text"]
        # ... and the parent the record names is a real, drawn block.
        assert by_id(rep["workspace"], record["parentId"])["type"] != PRESERVED_BLOCK_TYPE


def test_records_name_the_statement_input_they_live_in() -> None:
    inputs = {record["input"] for record in representation(IN_ELSE)["preserved"]}
    assert inputs == {"DO", "ELSE"}


def test_ids_are_deterministic() -> None:
    assert representation(DEEP) == representation(DEEP)


# --- editing an unrelated block cannot lose a fragment ----------------------


def test_editing_a_sibling_inside_an_if_keeps_the_fragment_in_place() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    by_id(edited, "loop.0.0")["fields"]["NAME"] = "begin"
    text = apply(IN_IF, edited, rep["preserved"])
    assert text == IN_IF.replace("start()", "begin()")
    assert text.index("begin();") < text.index(OPAQUE) < text.index("finish();")


def test_editing_a_sibling_inside_a_for_keeps_the_fragment_in_place() -> None:
    rep = representation(IN_FOR)
    edited = copy.deepcopy(rep["workspace"])
    by_id(edited, "loop.0.0")["fields"]["NAME"] = "total"
    text = apply(IN_FOR, edited, rep["preserved"])
    assert text == IN_FOR.replace("count += 1", "total += 1")


def test_editing_the_else_branch_keeps_both_branches_fragments() -> None:
    rep = representation(IN_ELSE)
    edited = copy.deepcopy(rep["workspace"])
    by_id(edited, "loop.0.ELSE.0")["fields"]["NAME"] = "halt"
    text = apply(IN_ELSE, edited, rep["preserved"])
    assert text == IN_ELSE.replace("stop()", "halt()")


def test_editing_deep_inside_nested_control_flow_keeps_every_fragment() -> None:
    rep = representation(DEEP)
    edited = copy.deepcopy(rep["workspace"])
    walk(
        edited,
        lambda n: n["fields"].update(NAME="beat") if n.get("fields", {}).get("NAME") == "tick" else None,
    )
    text = apply(DEEP, edited, rep["preserved"])
    assert text == DEEP.replace("tick()", "beat()")
    for marker in ("inner =", "middle =", "outer ="):
        assert f"{marker} {OPAQUE};" in text


# --- the drawing is display only: mutated, deleted, forged ------------------


@pytest.mark.parametrize("source", SHAPES.values(), ids=SHAPES.keys())
def test_mutating_every_drawn_fragment_changes_nothing(source: str) -> None:
    rep = representation(source)
    edited = copy.deepcopy(rep["workspace"])
    for block in drawn_fragments(edited):
        block["fields"][PRESERVED_TEXT_FIELD] = "system(evil);"
    text = apply(source, edited, rep["preserved"])
    assert text == source
    assert "evil" not in text


@pytest.mark.parametrize("source", SHAPES.values(), ids=SHAPES.keys())
def test_deleting_every_drawn_fragment_restores_them_from_the_records(source: str) -> None:
    rep = representation(source)
    edited = without_none(strip_drawn(copy.deepcopy(rep["workspace"])))
    assert drawn_fragments(edited) == []
    assert apply(source, edited, rep["preserved"]) == source


def test_a_forged_extra_drawn_fragment_is_ignored() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    inner = by_id(edited, "loop.0.0")
    inner["next"] = {
        "block": {
            "type": PRESERVED_BLOCK_TYPE,
            "id": "forged",
            "fields": {PRESERVED_TEXT_FIELD: "system(evil);"},
            "next": inner["next"],
        }
    }
    text = apply(IN_IF, edited, rep["preserved"])
    assert text == IN_IF


def test_a_drawn_fragment_moved_out_of_its_body_moves_the_source_with_it() -> None:
    """Position comes from where the drawing sits; content from the record.

    This test used to pin the opposite (the record's opened position won),
    which is the defect P4.0 found: it reordered real C++ behind the
    student's back.
    """
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    (container,) = edited["blocks"]["blocks"]
    outer = container["inputs"]["DO"]["block"]
    drawn = drawn_fragments(edited)[0]
    drawn["fields"][PRESERVED_TEXT_FIELD] = "system(evil);"
    # Detach the drawing from its chain and hang it at the section level.
    following = drawn.pop("next", None)
    chain_owner = by_id(edited, "loop.0.0")
    chain_owner["next"] = following
    outer["next"] = {"block": drawn}
    text = apply(IN_IF, edited, rep["preserved"])
    assert text == loop_source("if (ready) {", "  start();", "  finish();", "}", f"a = {OPAQUE};")
    assert "evil" not in text


# --- a fragment lives and dies with the block that holds it -----------------


def test_deleting_the_block_that_holds_a_fragment_deletes_the_fragment() -> None:
    rep = representation(IN_IF)
    edited = copy.deepcopy(rep["workspace"])
    (container,) = edited["blocks"]["blocks"]
    del container["inputs"]  # the whole `if` block (and what was in it) is gone
    text = apply(IN_IF, edited, rep["preserved"])
    assert OPAQUE not in text and "start()" not in text


def test_a_record_naming_a_block_the_workspace_does_not_have_is_dropped() -> None:
    rep = representation(IN_IF)
    stale = dict(rep["preserved"][0], parentId="loop.9", id="loop.9.0")
    assert apply(IN_IF, copy.deepcopy(rep["workspace"]), [stale]) == loop_source(
        "if (ready) {", "  start();", "  finish();", "}"
    )


# --- malformed records are refused, never repaired --------------------------


def test_two_records_for_the_same_position_are_refused() -> None:
    rep = representation(IN_IF)
    with pytest.raises(SectionBlocklyError):
        section_from_state("loop", rep["workspace"], [rep["preserved"][0], rep["preserved"][0]])


def test_a_record_past_the_end_of_its_body_is_refused() -> None:
    # The index is consulted only for a record with no drawing (a drawn one is
    # placed where it is drawn), so the drawing is removed to reach that path.
    rep = representation(IN_IF)
    far = dict(rep["preserved"][0], index=99)
    edited = without_none(strip_drawn(copy.deepcopy(rep["workspace"])))
    with pytest.raises(SectionBlocklyError):
        section_from_state("loop", edited, [far])


@pytest.mark.parametrize("field", ["parentId", "input"])
def test_a_record_locator_must_be_text(field: str) -> None:
    rep = representation(IN_IF)
    bad = dict(rep["preserved"][0])
    bad[field] = 7
    with pytest.raises(SectionBlocklyError):
        section_from_state("loop", rep["workspace"], [bad])


# --- legacy (locator-less) records keep meaning what they always meant ------


def test_a_record_without_a_locator_is_a_section_level_fragment() -> None:
    source = loop_source(f"value = {OPAQUE};", "tick();")
    rep = representation(source)
    legacy = [{k: v for k, v in r.items() if k not in ("id", "parentId", "input")} for r in rep["preserved"]]
    assert apply(source, rep["workspace"], legacy) == source


# --- a fragment is not editable merely because it is drawn ------------------


def test_the_drawn_block_is_a_dedicated_read_only_type() -> None:
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "src" / "blockly" / "arduinoBlocks.js").read_text(
        encoding="utf-8"
    )
    block = js[js.index("Blockly.Blocks['preserved_source']") :]
    block = block[: block.index("\n  }\n") + 5]
    for lock in ("setEditable(false)", "setMovable(false)", "setDeletable(false)"):
        assert lock in block
    # It is not a catalog block, so nothing can put it in a toolbox or read it as meaning.
    from app.blockly.catalog import default_block_catalog

    assert all(b.blockly_type != PRESERVED_BLOCK_TYPE for b in default_block_catalog.blocks)
