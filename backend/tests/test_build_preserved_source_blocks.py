"""Preserved C++ is VISIBLE in a section's Blockly workspace (Phase P1).

Before P1, `BlocklyBlock.to_state()` dropped every `PreservedSource`, so a
section whose statements the semantic layer does not understand yet
(Panel 1's `onMessage`, `ensureConnected`, ...) opened as an empty
`function body`. These tests pin the fix: each fragment is drawn as a
read-only `preserved_source` block at its original position, and the reverse
path is unchanged - the `preserved` list, not the drawn block, is what is
written back.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from app.blockly.catalog import default_block_catalog
from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    program_with_section,
    section_from_state,
    section_representation,
)

PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parents[1]
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)
SOURCE = PANEL_ONE_INO.read_text(encoding="utf-8")
PROGRAM = program_for_source(SOURCE)

FUNCTION_SECTIONS = tuple(
    section.section_id for section in PROGRAM.sections if section.operation is not None
)


def body_input(container: dict) -> dict:
    inputs = container.get("inputs") or {}
    return inputs.get("DO") or inputs.get("BODY")


def body_chain(representation: dict) -> list[dict]:
    """The top-level container's body as a flat list of block states."""
    (container,) = representation["workspace"]["blocks"]["blocks"]
    node = body_input(container)
    chain: list[dict] = []
    block = node["block"] if node else None
    while block is not None:
        chain.append(block)
        block = (block.get("next") or {}).get("block")
    return chain


def section_level(representation: dict) -> list[dict]:
    """The records of fragments in the SECTION's own body (not nested ones)."""
    return [
        record
        for record in representation["preserved"]
        if record["parentId"] == representation["sectionId"]
    ]


def texts(chain: list[dict]) -> list[str]:
    return [
        block["fields"][PRESERVED_TEXT_FIELD]
        for block in chain
        if block["type"] == PRESERVED_BLOCK_TYPE
    ]


def test_on_message_is_no_longer_an_empty_function_body() -> None:
    chain = body_chain(section_representation(PROGRAM, "callback_onMessage"))
    assert chain, "callback_onMessage still opens as an empty function body"
    # The declaration, the three String method calls and the `for` loop are real
    # blocks; only `String(topic) == COMMAND_TOPIC` is outside the expression
    # grammar and stays VISIBLE, read-only, exactly where it was.
    assert [block["type"] for block in chain] == [
        "variables_declare",
        "call_method",
        "for_loop",
        "call_method",
        "call_method",
        PRESERVED_BLOCK_TYPE,
    ]


def test_on_message_shows_its_remaining_source_exactly_and_in_order() -> None:
    representation = section_representation(PROGRAM, "callback_onMessage")
    chain = body_chain(representation)
    (shown,) = texts(chain)
    assert shown.startswith("if (String(topic) == COMMAND_TOPIC) {")
    assert shown == section_level(representation)[0]["text"]

    # ... and the statement inside the `for` body that is still outside the
    # grammar is drawn INSIDE it, recorded against the `for` block.
    (loop,) = [block for block in chain if block["type"] == "for_loop"]
    inner = loop["inputs"]["DO"]["block"]
    assert "next" not in inner
    assert inner["type"] == PRESERVED_BLOCK_TYPE
    assert inner["fields"][PRESERVED_TEXT_FIELD] == "message += static_cast<char>(payload[i]);"
    (record,) = [r for r in representation["preserved"] if r["parentId"] != "callback_onMessage"]
    assert record["parentId"] == loop["id"] and record["id"] == inner["id"]


def test_ensure_connected_does_not_appear_empty() -> None:
    representation = section_representation(PROGRAM, "helper_ensureConnected")
    chain = body_chain(representation)
    assert len(chain) == len(PROGRAM.section("helper_ensureConnected").statements) == 14
    assert PRESERVED_BLOCK_TYPE in [block["type"] for block in chain]
    assert texts(chain) == [record["text"] for record in section_level(representation)]


@pytest.mark.parametrize("section_id", FUNCTION_SECTIONS)
def test_every_body_item_is_drawn_and_none_is_dropped(section_id: str) -> None:
    representation = section_representation(PROGRAM, section_id)
    section = PROGRAM.section(section_id)
    chain = body_chain(representation)
    assert len(chain) == len(section.statements)
    assert texts(chain) == [record["text"] for record in section_level(representation)]


def test_blocks_and_preserved_source_keep_their_relative_order() -> None:
    representation = section_representation(PROGRAM, "setup")
    chain = body_chain(representation)
    types = [block["type"] for block in chain]
    assert len(types) == len(PROGRAM.section("setup").statements)
    # Serial.begin(...) first, then the seven pinMode calls, in source order.
    assert types[0] == "call_method"
    assert types[1:8] == ["pinmode"] * 7
    # The two comments are the only preserved items left and keep their order.
    assert types.count(PRESERVED_BLOCK_TYPE) == 2
    assert texts(chain)[0].startswith("// Started, not awaited")
    assert texts(chain)[1].startswith("// Bound each MQTT")
    first, second = (i for i, t in enumerate(types) if t == PRESERVED_BLOCK_TYPE)
    assert first < second


def test_records_still_index_fragments_by_body_position() -> None:
    representation = section_representation(PROGRAM, "setup")
    chain = body_chain(representation)
    for record in section_level(representation):
        drawn = chain[record["index"]]
        assert drawn["type"] == PRESERVED_BLOCK_TYPE
        assert drawn["fields"][PRESERVED_TEXT_FIELD] == record["text"]


def test_understood_sections_still_draw_their_real_blocks() -> None:
    chain = body_chain(section_representation(PROGRAM, "helper_chirpBuzzer"))
    assert [block["type"] for block in chain] == ["digitalwrite", "delay", "digitalwrite"]
    # P3: `run ? HIGH : LOW` is a ternary value block, so nothing is preserved.
    set_outputs = body_chain(section_representation(PROGRAM, "helper_setMotorOutputs"))
    assert PRESERVED_BLOCK_TYPE not in [block["type"] for block in set_outputs]
    loop_types = [block["type"] for block in body_chain(section_representation(PROGRAM, "loop"))]
    assert loop_types == ["call_existing_function", "call_method", "call_existing_function"]


def test_a_section_without_a_container_still_has_no_workspace_blocks() -> None:
    representation = section_representation(PROGRAM, "global")
    assert representation["representable"] is False
    assert representation["workspace"]["blocks"]["blocks"] == []


@pytest.mark.parametrize("section_id", FUNCTION_SECTIONS)
def test_an_unedited_round_trip_is_byte_identical(section_id: str) -> None:
    representation = section_representation(PROGRAM, section_id)
    section = section_from_state(
        section_id, representation["workspace"], representation["preserved"]
    )
    rebuilt = program_with_section(PROGRAM, section)
    assert source_for_program(rebuilt) == source_for_program(PROGRAM)


def test_the_whole_firmware_round_trips_byte_identical() -> None:
    rebuilt = PROGRAM
    for section_id in FUNCTION_SECTIONS:
        representation = section_representation(rebuilt, section_id)
        section = section_from_state(
            section_id, representation["workspace"], representation["preserved"]
        )
        rebuilt = program_with_section(rebuilt, section)
    assert source_for_program(rebuilt) == source_for_program(PROGRAM)


def test_editing_a_real_block_keeps_every_drawn_fragment_in_place() -> None:
    representation = section_representation(PROGRAM, "setup")
    workspace = copy.deepcopy(representation["workspace"])
    (container,) = workspace["blocks"]["blocks"]
    first_pin_mode = container["inputs"]["DO"]["block"]["next"]["block"]
    assert first_pin_mode["type"] == "pinmode"
    first_pin_mode["fields"]["MODE"] = "OUTPUT"

    section = section_from_state("setup", workspace, representation["preserved"])
    text = source_for_program(program_with_section(PROGRAM, section))
    assert "pinMode(START_BUTTON, OUTPUT);" in text
    for record in representation["preserved"]:
        assert record["text"].splitlines()[0] in text
    assert text.index("Serial.begin(115200);") < text.index("pinMode(START_BUTTON, OUTPUT);")


def _strip_preserved(node: dict | None) -> dict | None:
    """A body chain with every `preserved_source` block deleted and relinked."""
    if node is None:
        return None
    following = _strip_preserved((node.get("next") or {}).get("block"))
    if node["type"] == PRESERVED_BLOCK_TYPE:
        return following
    node = {key: value for key, value in node.items() if key != "next"}
    if following is not None:
        node["next"] = {"block": following}
    return node


def test_the_reverse_path_ignores_the_drawn_block_and_trusts_the_preserved_list() -> None:
    """Editing a drawn section-level fragment changes nothing: it is display only."""
    representation = section_representation(PROGRAM, "callback_onMessage")
    workspace = copy.deepcopy(representation["workspace"])
    (container,) = workspace["blocks"]["blocks"]
    block = body_input(container)["block"]
    while block["type"] != PRESERVED_BLOCK_TYPE:
        block = block["next"]["block"]
    block["fields"][PRESERVED_TEXT_FIELD] = "evil();"

    section = section_from_state("callback_onMessage", workspace, representation["preserved"])
    text = source_for_program(program_with_section(PROGRAM, section))
    assert "evil" not in text
    assert text == source_for_program(PROGRAM)


def test_deleting_every_drawn_fragment_does_not_delete_the_source() -> None:
    representation = section_representation(PROGRAM, "callback_onMessage")
    edited = copy.deepcopy(representation["workspace"])
    (container,) = edited["blocks"]["blocks"]
    container["inputs"][next(iter(container["inputs"]))]["block"] = _strip_preserved(
        body_input(container)["block"]
    )
    assert PRESERVED_BLOCK_TYPE not in [block["type"] for block in body_chain({"workspace": edited})]
    section = section_from_state("callback_onMessage", edited, representation["preserved"])
    assert source_for_program(program_with_section(PROGRAM, section)) == source_for_program(
        PROGRAM
    )


def test_the_drawn_fragment_is_not_a_semantic_block() -> None:
    assert all(
        block.blockly_type != PRESERVED_BLOCK_TYPE for block in default_block_catalog.blocks
    )
