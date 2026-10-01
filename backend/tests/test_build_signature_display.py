"""P4.2: a function container shows its C++ signature READ-ONLY.

    SemanticSection.signature   authoritative - B6 writes it back verbatim
    extraState.signature        display only  - drawn as the block's header

A student opening `helper_applyCommand` used to see a container labelled
"function body" and nothing saying `message` is a parameter they can read.
The declarator already reached the bridge (`BlocklyBlock.container_signature`)
and was dropped at `to_state()`. These tests pin that it now reaches the
editor as a description the header draws with plain labels, that the
description survives Blockly's save/load, and - the part that matters - that
nothing about it, forged or not, can change the generated C++.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from app.build.blockly_bridge.signature_display import (
    parse_signature,
    signature_extra_state,
)
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    program_with_section,
    section_from_state,
    section_representation,
)

from tests.test_real_blockly import blockly, pytestmark as _needs_blockly  # noqa: F401

PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parent.parent
    / "panels" / "smart-home-mqtt-control" / "firmware" / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)
SECTION = "helper_applyCommand"


def panel_one() -> str:
    return PANEL_ONE_INO.read_text(encoding="utf-8")


def opened(source: str, section: str) -> dict:
    return section_representation(program_for_source(source), section)


def top(workspace: dict) -> dict:
    (block,) = workspace["blocks"]["blocks"]
    return block


def apply(source: str, section: str, workspace: dict, records: list[dict]) -> str:
    program = program_for_source(source)
    return source_for_program(program_with_section(program, section_from_state(section, workspace, records)))


# =============================================================================
# 1-4. The display parser
# =============================================================================


def test_1_panel_one_apply_command_is_described_in_full() -> None:
    parsed = parse_signature("static void applyCommand(const String &message)")
    assert parsed is not None
    assert parsed.specifiers == ("static",)
    assert parsed.return_type == "void"
    assert parsed.name == "applyCommand"
    (parameter,) = parsed.parameters
    assert parameter.name == "message"
    assert parameter.type_text == "const String &"
    assert (parameter.const, parameter.reference, parameter.pointer) == (True, True, False)
    assert parsed.text == "static void applyCommand(const String &message)"


@pytest.mark.parametrize("signature", ["static void chirpBuzzer()", "void tick(void)", "int  count ( )"])
def test_2_a_function_with_no_parameters(signature: str) -> None:
    parsed = parse_signature(signature)
    assert parsed is not None and parsed.parameters == ()


def test_3_a_function_with_several_parameters() -> None:
    parsed = parse_signature("static void onMessage(char *topic, byte *payload, unsigned int length)")
    assert parsed is not None
    assert [(p.name, p.type_text, p.pointer) for p in parsed.parameters] == [
        ("topic", "char *", True),
        ("payload", "byte *", True),
        ("length", "unsigned int", False),
    ]


@pytest.mark.parametrize(
    ("signature", "type_text", "const", "reference", "pointer"),
    [
        ("void f(const String& s)", "const String &", True, True, False),
        ("void f(const String &s)", "const String &", True, True, False),
        ("void f(String & s)", "String &", False, True, False),
        ("void f(const char *name)", "const char *", True, False, True),
        ("void f(char* p)", "char *", False, False, True),
        ("void f(const char * const label)", "const char * const", True, False, True),
        ("void f(uint8_t buffer[])", "uint8_t []", False, False, True),
        ("void f(std::vector<int> &values)", "std::vector<int> &", False, True, False),
    ],
)
def test_4_const_reference_and_pointer_formatting(signature, type_text, const, reference, pointer) -> None:
    parsed = parse_signature(signature)
    assert parsed is not None, signature
    (parameter,) = parsed.parameters
    assert parameter.type_text == type_text
    assert (parameter.const, parameter.reference, parameter.pointer) == (const, reference, pointer)


def test_a_multi_line_declarator_is_shown_on_one_line() -> None:
    parsed = parse_signature("static void\nsetMotorOutputs(bool run,\n                int speed)")
    assert parsed is not None
    assert parsed.text == "static void setMotorOutputs(bool run, int speed)"
    assert [p.name for p in parsed.parameters] == ["run", "speed"]


@pytest.mark.parametrize(
    "signature",
    [
        "void f(int x = 3)",               # default argument
        "void f(void (*callback)(int))",   # function-pointer parameter
        "void f(const char *fmt, ...)",    # variadic
        "void f(int)",                     # unnamed parameter
        "int value() const",               # trailing qualifier
        "void f(const x)",                 # no type named
        "not a declarator",
        "",
    ],
)
def test_a_shape_the_reader_does_not_describe_is_not_guessed_at(signature: str) -> None:
    assert parse_signature(signature) is None


def test_an_undescribable_signature_falls_back_to_its_raw_text() -> None:
    assert signature_extra_state("void f(int x = 3)") == {"signature": {"text": "void f(int x = 3)"}}


def test_no_signature_means_no_extra_state() -> None:
    assert signature_extra_state(None) is None
    assert signature_extra_state("   ") is None


# =============================================================================
# The representation carries it; only named-function containers do
# =============================================================================


def test_the_opened_security_section_carries_its_signature_description() -> None:
    header = top(opened(panel_one(), SECTION)["workspace"])
    assert header["type"] == "function_implementation"
    signature = header["extraState"]["signature"]
    assert signature["text"] == "static void applyCommand(const String &message)"
    assert signature["returnType"] == "void" and signature["name"] == "applyCommand"
    assert signature["parameters"] == [
        {"type": "const String &", "name": "message", "const": True, "reference": True, "pointer": False}
    ]


def test_setup_and_loop_carry_no_signature_description() -> None:
    for section in ("setup", "loop"):
        assert "extraState" not in top(opened(panel_one(), section)["workspace"])


def test_every_panel_one_function_is_described_and_none_is_guessed() -> None:
    program = program_for_source(panel_one())
    described = {
        s.section_id: signature_extra_state(s.signature)["signature"]
        for s in program.sections
        if s.signature is not None
    }
    assert described["callback_onMessage"]["name"] == "onMessage"
    for section_id, signature in described.items():
        # Every Panel 1 declarator is one the reader describes, and the
        # description is of THAT declarator, not a reconstruction of it.
        assert "parameters" in signature, section_id
        source_signature = program.section(section_id).signature
        assert signature["text"] == " ".join(source_signature.split())


# =============================================================================
# 6-7. Generation stays on the ORIGINAL signature, whatever extraState says
# =============================================================================


def test_6_unedited_round_trip_is_byte_identical_with_the_header_present() -> None:
    source = panel_one()
    rep = opened(source, SECTION)
    assert apply(source, SECTION, copy.deepcopy(rep["workspace"]), rep["preserved"]) == source_for_program(
        program_for_source(source)
    )


@pytest.mark.parametrize(
    "forged",
    [
        {"signature": {"text": "int applyCommand(char *evil, int extra)", "name": "pwned",
                       "returnType": "int", "specifiers": [],
                       "parameters": [{"type": "char *", "name": "evil"}]}},
        {"signature": {"text": "static void renamed()", "parameters": []}},
        {"signature": "system(evil);"},
        {"totally": ["unrelated", 1, None]},
        None,
    ],
    ids=["rewritten", "renamed-no-params", "not-an-object", "unrelated-keys", "removed"],
)
def test_7_forged_or_removed_signature_state_cannot_change_generated_cpp(forged) -> None:
    source = panel_one()
    rep = opened(source, SECTION)
    edited = copy.deepcopy(rep["workspace"])
    if forged is None:
        del top(edited)["extraState"]
    else:
        top(edited)["extraState"] = forged
    written = apply(source, SECTION, edited, rep["preserved"])
    assert written == source_for_program(program_for_source(source))
    assert "static void applyCommand(const String &message) {" in written
    for marker in ("evil", "pwned", "renamed", "int applyCommand"):
        assert marker not in written


def test_7_a_body_edit_keeps_the_original_signature_under_a_forged_header() -> None:
    source = panel_one()
    rep = opened(source, SECTION)
    edited = copy.deepcopy(rep["workspace"])
    top(edited)["extraState"] = {"signature": {"text": "void other(int n)", "parameters": []}}
    body = top(edited)["inputs"]["BODY"]
    top(edited)["inputs"]["BODY"] = {
        "block": {"type": "call_existing_function", "fields": {"NAME": "chirpBuzzer"}, "next": body}
    }
    region = program_for_source(apply(source, SECTION, edited, rep["preserved"])).section(SECTION)
    assert region.signature == "static void applyCommand(const String &message)"


# =============================================================================
# Real Blockly: rendered, read-only, saved, and still applied
# =============================================================================


def fields_of(inspected: dict, block_id: str) -> dict[str, list[dict]]:
    return {row["name"]: row["fields"] for row in inspected[block_id]["inputs"]}


@_needs_blockly
def test_real_blockly_renders_the_signature_and_parameter_read_only(blockly) -> None:
    rep = opened(panel_one(), SECTION)
    result = blockly(inspect={"s": rep["workspace"]})
    assert result["errors"] == {}, result["errors"]
    rows = fields_of(result["inspected"]["s"], SECTION)
    assert [f["value"] for f in rows["HEADER"]] == [
        "function", "static void applyCommand(const String &message)",
    ]
    assert [f["value"] for f in rows["PARAM_0"]] == ["parameter", "message", ": const String &"]
    names = [row["name"] for row in result["inspected"]["s"][SECTION]["inputs"]]
    assert names.index("PARAM_0") < names.index("BODY")  # header above the editable body
    for row in ("HEADER", "PARAM_0"):
        for field in rows[row]:
            assert field["editable"] is False and field["serializable"] is False, (row, field)


@_needs_blockly
def test_real_blockly_shows_no_parameters_and_falls_back_without_a_description(blockly) -> None:
    no_params = opened(panel_one(), "helper_chirpBuzzer")["workspace"]
    bare = copy.deepcopy(opened(panel_one(), SECTION)["workspace"])
    del top(bare)["extraState"]
    raw = copy.deepcopy(bare)
    top(raw)["extraState"] = {"signature": {"text": "void f(int x = 3)"}}
    result = blockly(inspect={"none": no_params, "bare": bare, "raw": raw})
    assert result["errors"] == {}, result["errors"]
    assert [f["value"] for f in fields_of(result["inspected"]["none"], "helper_chirpBuzzer")["PARAMS"]] == [
        "no parameters"
    ]
    bare_rows = fields_of(result["inspected"]["bare"], SECTION)
    assert [f["value"] for f in bare_rows["HEADER"]] == ["function body"]
    raw_rows = fields_of(result["inspected"]["raw"], SECTION)
    assert [f["value"] for f in raw_rows["HEADER"]] == ["function", "void f(int x = 3)"]
    assert "PARAMS" not in raw_rows and "PARAM_0" not in raw_rows


@_needs_blockly
def test_real_blockly_ignores_malformed_parameter_entries(blockly) -> None:
    ws = copy.deepcopy(opened(panel_one(), SECTION)["workspace"])
    top(ws)["extraState"] = {"signature": {"text": "x", "parameters": [None, 7, {"name": 1}, {"name": "ok", "type": "int"}]}}
    result = blockly(inspect={"s": ws})
    assert result["errors"] == {}, result["errors"]
    rows = fields_of(result["inspected"]["s"], SECTION)
    assert [f["value"] for f in rows["PARAM_0"]] == ["parameter", "ok", ": int"]
    assert "PARAM_1" not in rows


@_needs_blockly
def test_5_real_blockly_save_load_keeps_the_description(blockly) -> None:
    rep = opened(panel_one(), SECTION)
    saved = blockly(states={"s": rep["workspace"]})["saved"]["s"]
    assert top(saved)["extraState"] == top(rep["workspace"])["extraState"]
    again = blockly(states={"s": saved})["saved"]["s"]
    assert top(again)["extraState"] == top(rep["workspace"])["extraState"]


@_needs_blockly
def test_8_real_blockly_message_is_readable_and_the_edit_applies(blockly) -> None:
    """`variables_get(message)` - the existing variable block, no special one -
    reads the parameter; the saved workspace goes through the real
    `apply_section_blockly` and only the intended body line changes."""
    from tests.test_p4_0_real_blockly_remediation import SKETCH_NAME, panel_one_workspace

    live = panel_one_workspace()
    before = live.full_source(SKETCH_NAME)
    view = live.section_blockly(SKETCH_NAME, SECTION)
    ops = [
        {"op": "new", "ref": "log", "type": "call_existing_function", "fields": {"NAME": "logCommand"}},
        {"op": "new", "ref": "msg", "type": "variables_get", "fields": {"NAME": "message"}},
        {"op": "value", "parent": "log", "input": "ARG0", "child": "msg"},
        {"op": "statement", "parent": f"id:{SECTION}", "input": "BODY", "child": "log"},
    ]
    result = blockly(edits={"e": {"state": view["workspace"], "ops": ops}})
    assert result["errors"] == {}, result["errors"]
    saved = result["saved"]["e"]
    assert top(saved)["extraState"] == top(view["workspace"])["extraState"]
    live.apply_section_blockly(SKETCH_NAME, SECTION, saved, view["preserved"])
    after = live.full_source(SKETCH_NAME)
    header = "static void applyCommand(const String &message) {\n"
    assert header in after
    assert after.replace(header + "  logCommand(message);\n", header) == before
