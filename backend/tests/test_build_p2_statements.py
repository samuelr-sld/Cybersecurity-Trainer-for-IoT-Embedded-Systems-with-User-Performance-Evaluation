"""Build Mode Blockly Phase P2 - general statement constructs.

P2 teaches the semantic layer, the Blockly bridge and the generator seven GENERAL
statement shapes, none of them tied to a function name:

    String message;                      uninitialized declaration
    static unsigned long lastEdge = 0;   qualified declaration (qualifier + type kept)
    lastEdge = millis();  x = expr;      assignment
    message += value;                    compound assignment (operator kept)
    message.trim();  client.loop();      zero-argument method call
    message.reserve(length);             method call with arguments
    applyMotorState(true);               function call with arguments

and fixes the P1 limitation that unsupported source NESTED inside a supported
block could be lost. Every test here drives the real pipeline

    C++ -> B3 IR -> B4 Blockly (state JSON) -> B5 IR -> B6 C++

and asserts on the way through. A statement is understood COMPLETELY or not at
all: a recognised head with an expression P2 cannot state stays visible source.
"""

from __future__ import annotations

import ast
import copy
import pathlib
import re

import pytest

from app.blockly.catalog import default_block_catalog
from app.build import BuildWorkspace, board_info_from_fqbn
from app.build.blockly_bridge import PreservedSource, program_to_blockly
from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
from app.build.blockly_bridge.structural import CALL_ARGUMENT_INPUTS
from app.build.discovery import analyze_source
from app.build.document_project import build_project_from_document
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    SectionBlocklyError,
    program_with_section,
    section_from_state,
    section_representation,
)
from app.build.semantic import (
    AssignmentStatement,
    CallStatement,
    ConditionalStatement,
    LiteralValue,
    MethodCallStatement,
    OperationStatement,
    SemanticType,
    SymbolValue,
    UnsupportedStatement,
    VariableDeclaration,
)
from app.build.workspace import SecurityRegionOwnershipError

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE_INO = (
    BACKEND
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)
SOURCE = PANEL_ONE_INO.read_text(encoding="utf-8")


# --- helpers ----------------------------------------------------------------


def loop_source(*lines: str) -> str:
    """A file whose `loop()` holds these lines (each already indented 2 more)."""
    return "void loop() {\n" + "".join(f"  {line}\n" for line in lines) + "}\n"


def body_chain(node: dict | None) -> list[dict]:
    """A `next`-linked statement stack as a flat list of block states."""
    chain: list[dict] = []
    while node is not None:
        chain.append(node)
        node = (node.get("next") or {}).get("block")
    return chain


def container_chain(representation: dict) -> list[dict]:
    (container,) = representation["workspace"]["blocks"]["blocks"]
    inputs = container.get("inputs") or {}
    node = inputs.get("DO") or inputs.get("BODY")
    return body_chain(node["block"] if node else None)


def all_types(node) -> list[str]:
    """Every block type anywhere in a state fragment, depth first."""
    found: list[str] = []
    if isinstance(node, dict):
        if "type" in node and isinstance(node["type"], str):
            found.append(node["type"])
        for value in node.values():
            found.extend(all_types(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(all_types(value))
    return found


def fields(block: dict) -> dict:
    return block.get("fields") or {}


def round_trip(source: str, section_id: str = "loop") -> str:
    """Program -> Blockly state -> program -> C++, with no edit in between."""
    program = program_for_source(source)
    representation = section_representation(program, section_id)
    section = section_from_state(
        section_id, representation["workspace"], representation["preserved"]
    )
    return source_for_program(program_with_section(program, section))


# --- the seven constructs, one focused case each ----------------------------

#: (statement source, IR class, Blockly type, fields the block must carry)
CASES = [
    # 1-3. declarations
    ("String message;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "none", "TYPE": "text", "NAME": "message"}),
    ("int value;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "none", "TYPE": "number", "NAME": "value"}),
    ("bool running;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "none", "TYPE": "boolean", "NAME": "running"}),
    # 4-5. qualified declarations keep their qualifier AND their type spelling
    ("static unsigned long lastEdge = 0;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "static", "TYPE": "unsigned_long", "NAME": "lastEdge"}),
    ("static bool state = false;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "static", "TYPE": "boolean", "NAME": "state"}),
    ("const int X = 5;", VariableDeclaration, "variables_declare",
     {"QUALIFIER": "const", "TYPE": "number", "NAME": "X"}),
    ("static const unsigned long WIFI_RETRY_MS = 10000;", VariableDeclaration,
     "variables_declare",
     {"QUALIFIER": "static_const", "TYPE": "unsigned_long", "NAME": "WIFI_RETRY_MS"}),
    # 6-7. assignment and compound assignment
    ("x = y + 1;", AssignmentStatement, "variables_set", {"NAME": "x", "OPERATOR": "="}),
    ("lastEdge = millis();", AssignmentStatement, "variables_set",
     {"NAME": "lastEdge", "OPERATOR": "="}),
    ("motorRunning = run;", AssignmentStatement, "variables_set",
     {"NAME": "motorRunning", "OPERATOR": "="}),
    ("message += value;", AssignmentStatement, "variables_set",
     {"NAME": "message", "OPERATOR": "+="}),
    # 8-11. method calls
    ("message.trim();", MethodCallStatement, "call_method",
     {"RECEIVER": "message", "METHOD": "trim"}),
    ("message.toUpperCase();", MethodCallStatement, "call_method",
     {"RECEIVER": "message", "METHOD": "toUpperCase"}),
    ("client.loop();", MethodCallStatement, "call_method",
     {"RECEIVER": "client", "METHOD": "loop"}),
    ("message.reserve(length);", MethodCallStatement, "call_method",
     {"RECEIVER": "message", "METHOD": "reserve"}),
    ("client.publish(topic, state);", MethodCallStatement, "call_method",
     {"RECEIVER": "client", "METHOD": "publish"}),
    ("client.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD);", MethodCallStatement,
     "call_method", {"RECEIVER": "client", "METHOD": "connect"}),
    ('Serial.println("WiFi connection lost");', MethodCallStatement, "call_method",
     {"RECEIVER": "Serial", "METHOD": "println"}),
    # 12-14. function calls with arguments
    ("applyMotorState(true);", CallStatement, "call_existing_function",
     {"NAME": "applyMotorState"}),
    ("applyMotorState(false);", CallStatement, "call_existing_function",
     {"NAME": "applyMotorState"}),
    ("delay(BUZZER_CHIRP_MS);", OperationStatement, "delay", {"MS": "BUZZER_CHIRP_MS"}),
]


@pytest.mark.parametrize(("statement", "ir_class", "block_type", "block_fields"), CASES)
def test_semantic_parsing(statement, ir_class, block_type, block_fields) -> None:
    program = program_for_source(loop_source(statement))
    (parsed,) = program.loop.statements
    assert isinstance(parsed, ir_class), parsed
    assert parsed.supported is True
    assert parsed.source_text == statement


@pytest.mark.parametrize(("statement", "ir_class", "block_type", "block_fields"), CASES)
def test_blockly_conversion_and_state_serialization(
    statement, ir_class, block_type, block_fields
) -> None:
    program = program_for_source(loop_source(statement))
    (item,) = program_to_blockly(program).section("loop").block.body
    assert not isinstance(item, PreservedSource), "understood statement was preserved"
    assert item.block_type == block_type

    representation = section_representation(program, "loop")
    (block,) = container_chain(representation)
    assert block["type"] == block_type
    assert {name: fields(block)[name] for name in block_fields} == block_fields
    assert PRESERVED_BLOCK_TYPE not in all_types(representation["workspace"])
    assert representation["preserved"] == []


@pytest.mark.parametrize(("statement", "ir_class", "block_type", "block_fields"), CASES)
def test_cpp_generation_and_exact_no_edit_round_trip(
    statement, ir_class, block_type, block_fields
) -> None:
    source = loop_source(statement)
    program = program_for_source(source)
    # B6 writes the statement from its MEANING, not from remembered text.
    assert f"  {statement}\n" in source_for_program(program)
    assert source_for_program(program) == source
    assert round_trip(source) == source


# --- what the IR keeps ------------------------------------------------------


def test_a_declaration_keeps_its_qualifier_and_type_spelling() -> None:
    (declaration,) = program_for_source(
        loop_source("static unsigned long lastEdge = 0;")
    ).loop.statements
    assert declaration.qualifiers == ("static",)
    assert declaration.type_name == "unsigned long"
    assert declaration.value_type is SemanticType.NUMBER
    assert declaration.initializer == LiteralValue(0, SemanticType.NUMBER)
    assert declaration.cpp_type == "unsigned long"


def test_the_default_type_spelling_is_not_stored_twice() -> None:
    (declaration,) = program_for_source(loop_source("int value;")).loop.statements
    assert declaration.type_name is None and declaration.initializer is None


def test_qualifiers_written_out_of_canonical_order_stay_source() -> None:
    (statement,) = program_for_source(loop_source("const static int X = 5;")).loop.statements
    assert isinstance(statement, UnsupportedStatement)
    assert statement.source_text == "const static int X = 5;"


def test_the_assignment_operator_is_kept_as_written() -> None:
    (plain, compound) = program_for_source(
        loop_source("message = value;", "message += value;")
    ).loop.statements
    assert (plain.operator, compound.operator) == ("=", "+=")
    assert plain != compound


def test_a_method_call_keeps_receiver_method_and_ordered_arguments() -> None:
    (call,) = program_for_source(
        loop_source("client.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD);")
    ).loop.statements
    assert (call.receiver, call.method_name) == ("client", "connect")
    assert call.arguments == (
        SymbolValue("clientId"),
        SymbolValue("MQTT_USERNAME"),
        SymbolValue("MQTT_PASSWORD"),
    )


def test_a_function_call_is_not_a_method_call() -> None:
    function_call, method_call = program_for_source(
        loop_source("reserve(length);", "message.reserve(length);")
    ).loop.statements
    assert isinstance(function_call, CallStatement)
    assert isinstance(method_call, MethodCallStatement)


def test_arguments_fill_the_call_block_sockets_in_order() -> None:
    representation = section_representation(
        program_for_source(loop_source("client.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD);")),
        "loop",
    )
    (block,) = container_chain(representation)
    inputs = block["inputs"]
    assert [inputs[name]["block"]["fields"]["NAME"] for name in ("ARG0", "ARG1", "ARG2")] == [
        "clientId",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
    ]
    assert "ARG3" not in inputs


def test_editing_a_call_argument_in_blockly_changes_the_generated_cpp() -> None:
    program = program_for_source(loop_source("applyMotorState(true);"))
    representation = section_representation(program, "loop")
    edited = copy.deepcopy(representation["workspace"])
    (container,) = edited["blocks"]["blocks"]
    call = container["inputs"]["DO"]["block"]
    assert call["inputs"]["ARG0"]["block"]["type"] == "logic_true"
    call["inputs"]["ARG0"]["block"] = {"type": "logic_false"}
    section = section_from_state("loop", edited, representation["preserved"])
    assert source_for_program(program_with_section(program, section)) == loop_source(
        "applyMotorState(false);"
    )


def test_a_block_authored_in_blockly_generates_real_cpp() -> None:
    """`[call method] message . reserve ( length )` -> `message.reserve(length);`"""
    program = program_for_source(loop_source("client.loop();"))
    edited = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "arduino_loop",
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "call_method",
                                "fields": {"RECEIVER": "message", "METHOD": "reserve"},
                                "inputs": {
                                    "ARG0": {
                                        "block": {
                                            "type": "variables_get",
                                            "fields": {"NAME": "length"},
                                        }
                                    }
                                },
                                "next": {
                                    "block": {
                                        "type": "variables_set",
                                        "fields": {"NAME": "message", "OPERATOR": "+="},
                                        "inputs": {
                                            "VALUE": {
                                                "block": {
                                                    "type": "variables_get",
                                                    "fields": {"NAME": "part"},
                                                }
                                            }
                                        },
                                    }
                                },
                            }
                        }
                    },
                }
            ],
        }
    }
    section = section_from_state("loop", edited, [])
    assert source_for_program(program_with_section(program, section)) == loop_source(
        "message.reserve(length);", "message += part;"
    )


def test_a_call_argument_gap_is_refused_not_renumbered() -> None:
    edited = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "arduino_loop",
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "call_existing_function",
                                "fields": {"NAME": "f"},
                                "inputs": {"ARG1": {"block": {"type": "logic_true"}}},
                            }
                        }
                    },
                }
            ],
        }
    }
    program = program_for_source(loop_source("f(true);"))
    with pytest.raises(SectionBlocklyError):
        program_with_section(program, section_from_state("loop", edited, []))


# --- NO SILENT LOSS ---------------------------------------------------------

OUT_OF_SCOPE = [
    # Constructs outside the expression grammar stay visible, read-only source -
    # never partly converted.
    "while (busy) {\n    yield();\n  }",
    "message += static_cast<char>(payload[i]);",
    "value = table[i];",
    "table[i] = 3;",
    "value = WiFi.status();",
    "n = String(topic).length();",
    "if (WiFi.status() != WL_CONNECTED) {\n    reconnect();\n  }",
    "if (millis() - lastEdge < DEBOUNCE) {\n    return;\n  }",
    "volatile int counter;",
    "int a = 1, b = 2;",
]


@pytest.mark.parametrize("statement", OUT_OF_SCOPE)
def test_constructs_outside_the_grammar_stay_visible_and_exact(statement: str) -> None:
    source = loop_source(*statement.split("\n  "))
    program = program_for_source(source)
    (parsed,) = program.loop.statements
    assert isinstance(parsed, UnsupportedStatement), parsed
    assert parsed.source_text == statement

    representation = section_representation(program, "loop")
    (block,) = container_chain(representation)
    assert block["type"] == PRESERVED_BLOCK_TYPE
    assert block["fields"][PRESERVED_TEXT_FIELD] == statement
    assert round_trip(source) == source


def test_a_recognised_head_with_an_unreadable_expression_is_kept_whole() -> None:
    """`+=` is recognised, the expression is not: the WHOLE statement is carried."""
    (parsed,) = program_for_source(
        loop_source("message += static_cast<char>(payload[i]);")
    ).loop.statements
    assert isinstance(parsed, UnsupportedStatement)
    assert parsed.source_text == "message += static_cast<char>(payload[i]);"


def test_a_call_with_more_arguments_than_sockets_is_kept_visible_not_truncated() -> None:
    source = loop_source("sum(1, 2, 3, 4, 5);")
    program = program_for_source(source)
    (parsed,) = program.loop.statements
    assert isinstance(parsed, CallStatement) and len(parsed.arguments) == 5
    assert len(CALL_ARGUMENT_INPUTS) == 4

    representation = section_representation(program, "loop")
    (block,) = container_chain(representation)
    assert block["type"] == PRESERVED_BLOCK_TYPE
    assert block["fields"][PRESERVED_TEXT_FIELD] == "sum(1, 2, 3, 4, 5);"
    assert representation["preserved"][0]["understoodByTheIr"] is True
    assert round_trip(source) == source


def test_an_empty_text_argument_is_kept_visible_rather_than_dropped() -> None:
    source = loop_source('Serial.print("");')
    (block,) = container_chain(section_representation(program_for_source(source), "loop"))
    assert block["type"] == PRESERVED_BLOCK_TYPE
    assert round_trip(source) == source


# --- nested preservation ----------------------------------------------------

NESTED = loop_source(
    'if (mode == "ON") {',
    "  motorStart();",
    "  ready = table[i];",
    "  motorStop();",
    "}",
)


def nested_chain(representation: dict) -> list[dict]:
    (outer,) = container_chain(representation)
    assert outer["type"] == "if_equals"
    return body_chain(outer["inputs"]["DO"]["block"])


def test_unsupported_source_nested_in_a_supported_block_is_drawn_in_place() -> None:
    program = program_for_source(NESTED)
    (conditional,) = program.loop.statements
    assert isinstance(conditional, ConditionalStatement)
    assert [type(item) for item in conditional.body] == [
        CallStatement,
        UnsupportedStatement,
        CallStatement,
    ]

    chain = nested_chain(section_representation(program, "loop"))
    assert [block["type"] for block in chain] == [
        "call_existing_function",
        PRESERVED_BLOCK_TYPE,
        "call_existing_function",
    ]
    assert chain[1]["fields"][PRESERVED_TEXT_FIELD] == "ready = table[i];"


def test_nested_source_survives_parse_blockly_state_generate_byte_identical() -> None:
    assert round_trip(NESTED) == NESTED


def test_editing_a_sibling_cannot_delete_the_nested_source() -> None:
    program = program_for_source(NESTED)
    representation = section_representation(program, "loop")
    edited = copy.deepcopy(representation["workspace"])
    (container,) = edited["blocks"]["blocks"]
    outer = container["inputs"]["DO"]["block"]
    first = outer["inputs"]["DO"]["block"]
    assert first["fields"]["NAME"] == "motorStart"
    first["fields"]["NAME"] = "motorGo"

    section = section_from_state("loop", edited, representation["preserved"])
    text = source_for_program(program_with_section(program, section))
    assert text == NESTED.replace("motorStart", "motorGo")
    assert text.index("motorGo") < text.index("ready = table[i];") < text.index("motorStop")


def test_nested_source_two_levels_deep_is_kept() -> None:
    source = loop_source(
        'if (mode == "ON") {',
        '  if (gear == "HIGH") {',
        "    ready = table[i];",
        "    motorStart();",
        "  }",
        "}",
    )
    program = program_for_source(source)
    types = all_types(section_representation(program, "loop")["workspace"])
    assert types.count(PRESERVED_BLOCK_TYPE) == 1
    assert round_trip(source) == source


def test_nested_source_is_recorded_against_the_block_that_holds_it() -> None:
    """A nested fragment has a record of its own: which block, which input, where."""
    representation = section_representation(program_for_source(NESTED), "loop")
    (record,) = representation["preserved"]
    (outer,) = container_chain(representation)
    assert record["parentId"] == outer["id"] == "loop.0"
    assert record["input"] == "DO" and record["index"] == 1
    assert record["id"] == "loop.0.1"


def test_nested_fragments_do_not_disturb_section_level_ones() -> None:
    source = loop_source(
        "value = table[i];",
        'if (mode == "ON") {',
        "  ready = table[i];",
        "  motorStart();",
        "}",
        "other = table[j];",
    )
    program = program_for_source(source)
    representation = section_representation(program, "loop")
    assert [
        record["index"] for record in representation["preserved"] if record["parentId"] == "loop"
    ] == [0, 2]
    assert [
        record["index"] for record in representation["preserved"] if record["parentId"] != "loop"
    ] == [0]
    assert round_trip(source) == source


def _security_workspace() -> BuildWorkspace:
    source = "void setup() {\n}\n\n" + NESTED
    project = build_project_from_document(
        analyze_source(source),
        path="main.ino",
        project_id="p",
        scenario_id="s",
        module_id="m",
        firmware_name="f",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=("loop",),
        security_region_id="loop",
    )
    return BuildWorkspace(project)


def test_nested_opaque_source_cannot_hide_in_the_security_region() -> None:
    """Full ownership covers a statement one `if` deep, not just the top level."""
    live = _security_workspace()
    representation = live.section_blockly("main.ino", "loop")
    # Nothing at SECTION level to trip the rule - the fragment is one `if` deep.
    assert all(record["parentId"] != "loop" for record in representation["preserved"])
    with pytest.raises(SecurityRegionOwnershipError, match="ready = table"):
        live.apply_section_blockly(
            "main.ino", "loop", representation["workspace"], representation["preserved"]
        )


# --- Panel 1 regression -----------------------------------------------------


def _count(node, block_type: str) -> int:
    return all_types(node).count(block_type)


#: section id -> (statements, preserved_source blocks still drawn). What is left
#: over is exactly what P2 does not model; nothing here is claimed "fully editable"
#: while a preserved_source block remains.
PANEL_ONE_STATUS = {
    "setup": (21, 2),
    "loop": (3, 0),
    "helper_chirpBuzzer": (3, 0),
    "helper_setMotorOutputs": (4, 0),
    "helper_applyMotorState": (4, 0),
    "helper_motorStart": (1, 0),
    "helper_motorStop": (1, 0),
    "helper_applyCommand": (2, 1),
    "callback_onMessage": (6, 2),
    "helper_pollButtons": (3, 1),
    "helper_ensureConnected": (14, 6),
}


@pytest.mark.parametrize(("section_id", "expected"), sorted(PANEL_ONE_STATUS.items()))
def test_panel_one_section_status(section_id: str, expected: tuple[int, int]) -> None:
    program = program_for_source(SOURCE)
    representation = section_representation(program, section_id)
    statements, preserved = expected
    assert len(program.section(section_id).statements) == statements
    assert _count(representation["workspace"], PRESERVED_BLOCK_TYPE) == preserved


def test_panel_one_regenerates_with_nothing_lost() -> None:
    """Every statement - understood or preserved - is still there, in order."""
    program = program_for_source(SOURCE)
    regenerated = source_for_program(program)
    assert re.sub(r"\s+", "", regenerated) == re.sub(r"\s+", "", SOURCE)


def test_panel_one_round_trips_byte_identical_section_by_section() -> None:
    program = program_for_source(SOURCE)
    rebuilt = program
    for section in program.sections:
        if section.operation is None:
            continue
        representation = section_representation(rebuilt, section.section_id)
        rebuilt = program_with_section(
            rebuilt,
            section_from_state(
                section.section_id, representation["workspace"], representation["preserved"]
            ),
        )
    assert source_for_program(rebuilt) == source_for_program(program)


def test_the_helpers_that_only_forward_a_call_are_fully_drawn() -> None:
    """`motorStart`/`motorStop` were entirely preserved before P2."""
    program = program_for_source(SOURCE)
    for section_id, argument in (("helper_motorStart", "true"), ("helper_motorStop", "false")):
        (block,) = container_chain(section_representation(program, section_id))
        assert block["type"] == "call_existing_function"
        assert fields(block) == {"NAME": "applyMotorState"}
        assert block["inputs"]["ARG0"]["block"]["type"] == f"logic_{argument}"


# --- genericity -------------------------------------------------------------


def _code_tokens(path: pathlib.Path) -> set[str]:
    """Identifiers and string literals in CODE - docstrings and comments excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.add(node.value)
    return found


def test_no_recognizer_or_block_knows_a_firmware_function_name() -> None:
    names = {"onMessage", "applyMotorState", "applyCommand", "motorStart", "motorStop",
             "pollButtons", "ensureConnected", "chirpBuzzer", "setMotorOutputs"}
    for package in ("semantic", "blockly_bridge"):
        for path in (BACKEND / "app" / "build" / package).rglob("*.py"):
            leaked = names & {token for token in _code_tokens(path)}
            assert not leaked, f"{path.name} names a firmware function: {sorted(leaked)}"


def test_the_same_recognizer_handles_any_compatible_function() -> None:
    source = "void beep(int times) {\n  ring.start(times);\n  count += times;\n  log(times);\n}\n"
    section = program_for_source(source).section("helper_beep")
    assert [type(s) for s in section.statements] == [
        MethodCallStatement,
        AssignmentStatement,
        CallStatement,
    ]


def test_call_socket_names_agree_between_the_bridge_and_the_catalog() -> None:
    for block_id in ("functions.call_existing", "functions.call_method", "functions.call_value"):
        definition = default_block_catalog.block(block_id)
        sockets = tuple(
            item.name for item in definition.inputs if item.name.startswith("ARG")
        )
        assert sockets == CALL_ARGUMENT_INPUTS, block_id


def test_the_frontend_defines_the_same_number_of_argument_sockets() -> None:
    js = (BACKEND.parent / "src" / "blockly" / "arduinoBlocks.js").read_text(encoding="utf-8")
    assert f"const CALL_ARGUMENT_COUNT = {len(CALL_ARGUMENT_INPUTS)}" in js
