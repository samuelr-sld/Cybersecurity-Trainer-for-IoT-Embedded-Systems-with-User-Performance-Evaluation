"""Build Mode Blockly Phase P3 - control flow and operators.

P3 teaches the semantic layer, the Blockly bridge and the generator:

    for (INIT; CONDITION; STEP) { ... }      a C-style loop (each part optional)
    if / else if / else                      one linked chain of any length
    <  >  >=  <=                             ordering comparisons
    &&  ||  !                                logical connectives
    cond ? a : b                             a value chosen by a boolean
    i++;  i--;                               postfix steps

None of it is tied to a function name, and none of it widens into P4: casts,
indexing, `String(x)`, method calls as values, `-` and the rest of the
expression grammar stay visible, read-only source. The rule throughout is
"understand completely or preserve completely": a header or condition with ONE
part outside the grammar keeps the whole statement as source.

Every test drives the real pipeline
    C++ -> IR -> Blockly (state JSON) -> IR -> C++
and asserts on the way through.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from app.blockly.catalog import default_block_catalog
from app.build.blockly_bridge import PreservedSource, program_to_blockly
from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE, PRESERVED_TEXT_FIELD
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
    ComparisonValue,
    ConditionalStatement,
    ForStatement,
    LiteralValue,
    LogicalValue,
    MethodCallStatement,
    NotValue,
    SemanticModelError,
    SemanticType,
    SymbolValue,
    TernaryValue,
    UnsupportedStatement,
    UpdateStatement,
    VariableDeclaration,
)

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
    return "void loop() {\n" + "".join(f"  {line}\n" for line in lines) + "}\n"


def program_of(*lines: str):
    return program_for_source(loop_source(*lines))


def statements(*lines: str):
    return program_of(*lines).loop.statements


def chain_of(node: dict | None) -> list[dict]:
    out: list[dict] = []
    while node is not None:
        out.append(node)
        node = (node.get("next") or {}).get("block")
    return out


def top_blocks(program, section_id: str = "loop") -> list[dict]:
    (container,) = section_representation(program, section_id)["workspace"]["blocks"]["blocks"]
    inputs = container.get("inputs") or {}
    node = inputs.get("DO") or inputs.get("BODY")
    return chain_of(node["block"] if node else None)


def types_in(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("type"), str):
            found.append(node["type"])
        for value in node.values():
            found.extend(types_in(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(types_in(value))
    return found


def round_trip(source: str, section_id: str = "loop") -> str:
    program = program_for_source(source)
    rep = section_representation(program, section_id)
    section = section_from_state(section_id, rep["workspace"], rep["preserved"])
    return source_for_program(program_with_section(program, section))


def sym(name: str) -> SymbolValue:
    return SymbolValue(name)


def num(value: int) -> LiteralValue:
    return LiteralValue(value, SemanticType.NUMBER)


# --- 1. the IR: each construct, on its own ----------------------------------


def test_a_for_loop_keeps_its_init_condition_step_and_body() -> None:
    (loop,) = statements(
        "for (unsigned int i = 0; i < length; i++) {",
        "  count += 1;",
        "  tick();",
        "}",
    )
    assert isinstance(loop, ForStatement)
    assert isinstance(loop.init, VariableDeclaration)
    assert (loop.init.name, loop.init.type_name, loop.init.initializer) == (
        "i",
        "unsigned int",
        num(0),
    )
    assert loop.condition == ComparisonValue(sym("i"), "<", sym("length"))
    assert loop.step == UpdateStatement("i", "++", text="i++;")
    assert [type(item) for item in loop.body] == [AssignmentStatement, CallStatement]


def test_a_for_header_may_use_assignments_and_leave_parts_empty() -> None:
    (assigned,) = statements("for (i = 0; i <= 10; i += 2) {", "  tick();", "}")
    assert isinstance(assigned.init, AssignmentStatement) and assigned.init.operator == "="
    assert isinstance(assigned.step, AssignmentStatement) and assigned.step.operator == "+="
    (bare,) = statements("for (;;) {", "  tick();", "}")
    assert (bare.init, bare.condition, bare.step) == (None, None, None)
    (partial,) = statements("for (; running; ) {", "  tick();", "}")
    assert partial.init is None and partial.step is None and partial.condition == sym("running")


@pytest.mark.parametrize(
    "header",
    [
        "int i = 0, j = 1; i < 3; i++",  # two declarators
        "auto item : items",  # ranged for
        "int i = 0; i < 3; ++i",  # prefix step
        "int i = 0; i < table[i]; i++",  # a condition outside the grammar
        "static int i = 0; i < 3; i++",  # a qualified init
        "int i = 0; i < 3; i = table[i]",  # a step outside the grammar
        "int i = 0; i < 3",  # not three parts
    ],
)
def test_a_for_header_not_stated_in_full_keeps_the_whole_loop_as_source(header: str) -> None:
    source = loop_source(f"for ({header}) {{", "  tick();", "}")
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, UnsupportedStatement), parsed
    assert parsed.source_text.startswith(f"for ({header})")
    assert round_trip(source) == source


def test_a_for_without_braces_stays_source() -> None:
    source = loop_source("for (int i = 0; i < 3; i++)", "  tick();")
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, UnsupportedStatement)
    assert "tick();" in parsed.source_text and parsed.source_text.startswith("for (")
    assert round_trip(source) == source


def test_if_else_is_one_conditional_with_an_else_body() -> None:
    (chain,) = statements("if (ready) {", "  start();", "} else {", "  stop();", "}")
    assert isinstance(chain, ConditionalStatement)
    assert chain.else_if is None
    assert [item.function_name for item in chain.else_body] == ["stop"]


def test_else_if_is_a_linked_chain_of_any_length() -> None:
    (chain,) = statements(
        "if (a) {",
        "  one();",
        "} else if (b) {",
        "  two();",
        "} else if (c) {",
        "  three();",
        "} else {",
        "  four();",
        "}",
    )
    links = []
    link = chain
    while link is not None:
        links.append(link)
        link = link.else_if
    assert [l.condition for l in links] == [sym("a"), sym("b"), sym("c")]
    assert links[-1].else_body[0].function_name == "four"
    assert all(l.else_body is None for l in links[:-1])


def test_a_conditional_continues_with_else_if_or_else_never_both() -> None:
    inner = ConditionalStatement(condition=sym("b"), body=())
    with pytest.raises(SemanticModelError):
        ConditionalStatement(condition=sym("a"), body=(), else_if=inner, else_body=())


def test_a_brace_less_else_keeps_the_whole_statement_as_source() -> None:
    source = loop_source("if (a) {", "  one();", "} else two();")
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, UnsupportedStatement)
    assert round_trip(source) == source


def test_an_else_if_with_a_condition_outside_the_grammar_keeps_the_whole_chain() -> None:
    """No half-understood chain: not the `if` as a block and the rest as text."""
    source = loop_source(
        "if (a) {", "  one();", f"}} else if (WiFi.status() != WL_CONNECTED) {{", "  two();", "}"
    )
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, UnsupportedStatement)
    assert parsed.source_text.startswith("if (a)") and "else if" in parsed.source_text
    assert round_trip(source) == source


@pytest.mark.parametrize("operator", ["<", ">", ">=", "<="])
def test_ordering_comparisons(operator: str) -> None:
    (chain,) = statements(f"if (count {operator} LIMIT) {{", "  tick();", "}")
    assert chain.condition == ComparisonValue(sym("count"), operator, sym("LIMIT"))
    assert chain.condition.fits(SemanticType.BOOLEAN)


def test_ordering_compares_numbers_not_text() -> None:
    with pytest.raises(SemanticModelError):
        ComparisonValue(LiteralValue("a", SemanticType.TEXT), "<", num(1))


def test_logical_operators_follow_cpp_precedence() -> None:
    (chain,) = statements("if (a && b || c) {", "  tick();", "}")
    assert chain.condition == LogicalValue(LogicalValue(sym("a"), "&&", sym("b")), "||", sym("c"))
    (grouped,) = statements("if (a && (b || c)) {", "  tick();", "}")
    assert grouped.condition == LogicalValue(sym("a"), "&&", LogicalValue(sym("b"), "||", sym("c")))


def test_not_binds_tighter_than_a_comparison() -> None:
    (chain,) = statements("if (!a == b) {", "  tick();", "}")
    assert chain.condition == ComparisonValue(NotValue(sym("a")), "==", sym("b"))
    (grouped,) = statements("if (!(a == b)) {", "  tick();", "}")
    assert grouped.condition == NotValue(ComparisonValue(sym("a"), "==", sym("b")))


def test_a_ternary_chooses_between_two_values() -> None:
    (assign,) = statements("x = run ? HIGH : LOW;")
    assert assign.value == TernaryValue(sym("run"), sym("HIGH"), sym("LOW"))
    (nested,) = statements("x = a ? b : c ? d : e;")
    assert nested.value == TernaryValue(sym("a"), sym("b"), TernaryValue(sym("c"), sym("d"), sym("e")))


def test_a_ternary_fits_a_slot_only_when_both_branches_do() -> None:
    text = LiteralValue("a", SemanticType.TEXT)
    assert TernaryValue(sym("f"), text, text).fits(SemanticType.TEXT)
    assert not TernaryValue(sym("f"), text, text).fits(SemanticType.NUMBER)
    assert TernaryValue(sym("f"), sym("HIGH"), sym("LOW")).fits(SemanticType.NUMBER)
    with pytest.raises(SemanticModelError):
        TernaryValue(text, num(1), num(2))  # a text is not a condition


def test_postfix_steps_are_updates_and_prefix_steps_are_source() -> None:
    up, down = statements("i++;", "count--;")
    assert (up.target, up.operator, down.target, down.operator) == ("i", "++", "count", "--")
    (prefix,) = statements("++i;")
    assert isinstance(prefix, UnsupportedStatement)


def test_a_known_operation_call_with_a_richer_argument_is_a_complete_call() -> None:
    (call,) = statements("digitalWrite(MOTOR_IN1, run ? HIGH : LOW);")
    assert isinstance(call, CallStatement) and call.function_name == "digitalWrite"
    assert isinstance(call.arguments[1], TernaryValue)


# --- 2. the whole pipeline, one construct at a time -------------------------

#: (statement lines, IR class, top-level Blockly type, block types that must appear)
CASES = [
    (["for (unsigned int i = 0; i < length; i++) {", "  count += 1;", "}"], ForStatement, "for_loop",
     {"variables_declare", "logic_less", "variables_update", "variables_set"}),
    (["for (i = 0; i <= 10; i += 2) {", "  tick();", "}"], ForStatement, "for_loop",
     {"variables_set", "logic_less_equal"}),
    (["for (;;) {", "  tick();", "}"], ForStatement, "for_loop", set()),
    (["if (ready) {", "  start();", "} else {", "  stop();", "}"], ConditionalStatement, "logic_if",
     set()),
    (["if (a) {", "  one();", "} else if (b) {", "  two();", "}"], ConditionalStatement, "logic_if",
     set()),
    (["if (a) {", "  one();", "} else if (b) {", "  two();", "} else if (c) {", "  three();",
      "} else {", "  four();", "}"], ConditionalStatement, "logic_if", set()),
    (["if (count < LIMIT) {", "  tick();", "}"], ConditionalStatement, "logic_if", {"logic_less"}),
    (["if (count > LIMIT) {", "  tick();", "}"], ConditionalStatement, "logic_if", {"logic_greater"}),
    (["if (count >= LIMIT) {", "  tick();", "}"], ConditionalStatement, "logic_if",
     {"logic_greater_equal"}),
    (["if (a && b || c) {", "  tick();", "}"], ConditionalStatement, "logic_if",
     {"logic_and", "logic_or"}),
    (["if ((a || b) && c) {", "  tick();", "}"], ConditionalStatement, "logic_if",
     {"logic_and", "logic_or"}),
    (["if (!a && b) {", "  tick();", "}"], ConditionalStatement, "logic_if", {"logic_not", "logic_and"}),
    (["if (!(a && b)) {", "  tick();", "}"], ConditionalStatement, "logic_if", {"logic_not"}),
    (["if (running) {", "  tick();", "}"], ConditionalStatement, "logic_if", {"variables_get"}),
    (["x = run ? HIGH : LOW;"], AssignmentStatement, "variables_set", {"logic_ternary"}),
    (["x = a ? b : c ? d : e;"], AssignmentStatement, "variables_set", {"logic_ternary"}),
    (["digitalWrite(MOTOR_IN1, run ? HIGH : LOW);"], CallStatement, "call_existing_function",
     {"logic_ternary"}),
    (['client.publish(T, running ? "RUNNING" : "STOPPED", true);'], MethodCallStatement,
     "call_method", {"logic_ternary", "text_literal", "logic_true"}),
    (["i++;"], UpdateStatement, "variables_update", set()),
    (["count--;"], UpdateStatement, "variables_update", set()),
    (["if (digitalRead(START) == LOW && !running) {", "  go();", "}"], ConditionalStatement,
     "logic_if", {"call_function_value", "logic_and", "logic_not"}),
]


@pytest.mark.parametrize(("lines", "ir_class", "block_type", "expected"), CASES)
def test_semantic_parsing(lines, ir_class, block_type, expected) -> None:
    (parsed,) = statements(*lines)
    assert isinstance(parsed, ir_class), parsed
    assert parsed.supported is True


@pytest.mark.parametrize(("lines", "ir_class", "block_type", "expected"), CASES)
def test_blockly_conversion_and_state_serialization(lines, ir_class, block_type, expected) -> None:
    program = program_of(*lines)
    (item,) = program_to_blockly(program).section("loop").block.body
    assert not isinstance(item, PreservedSource), "an understood statement was preserved"
    assert item.block_type == block_type

    rep = section_representation(program, "loop")
    (block,) = top_blocks(program)
    assert block["type"] == block_type
    seen = set(types_in(rep["workspace"]))
    assert expected <= seen, expected - seen
    assert PRESERVED_BLOCK_TYPE not in seen
    assert rep["preserved"] == []


@pytest.mark.parametrize(("lines", "ir_class", "block_type", "expected"), CASES)
def test_cpp_generation_and_exact_no_edit_round_trip(lines, ir_class, block_type, expected) -> None:
    source = loop_source(*lines)
    assert source_for_program(program_for_source(source)) == source
    assert round_trip(source) == source


def test_a_chain_is_drawn_as_nested_if_blocks_in_the_else_if_input() -> None:
    program = program_of(
        "if (a) {", "  one();", "} else if (b) {", "  two();", "} else {", "  three();", "}"
    )
    (outer,) = top_blocks(program)
    assert set(outer["inputs"]) == {"CONDITION", "DO", "ELSE_IF"}
    (link,) = chain_of(outer["inputs"]["ELSE_IF"]["block"])
    assert link["type"] == "logic_if"
    assert set(link["inputs"]) == {"CONDITION", "DO", "ELSE"}
    assert chain_of(link["inputs"]["ELSE"]["block"])[0]["fields"]["NAME"] == "three"


def test_a_for_is_drawn_with_init_condition_step_and_do_inputs() -> None:
    program = program_of("for (int i = 0; i < 3; i++) {", "  tick();", "}")
    (loop,) = top_blocks(program)
    assert set(loop["inputs"]) == {"INIT", "CONDITION", "STEP", "DO"}
    assert loop["inputs"]["INIT"]["block"]["type"] == "variables_declare"
    assert loop["inputs"]["STEP"]["block"]["type"] == "variables_update"
    assert loop["inputs"]["CONDITION"]["block"]["type"] == "logic_less"


# --- 3. precedence is written from the tree ---------------------------------


@pytest.mark.parametrize(
    "condition",
    [
        "a && b || c",
        "a || b && c",
        "(a || b) && c",
        "a && (b || c)",
        "!a && !b",
        "!(a || b)",
        "!a == b",
        "count < LIMIT && !done",
        "(count < LIMIT) == done",
    ],
)
def test_conditions_regenerate_with_the_same_grouping(condition: str) -> None:
    source = loop_source(f"if ({condition}) {{", "  tick();", "}")
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, ConditionalStatement)
    regenerated = source_for_program(program_for_source(source))
    reread = program_for_source(regenerated).loop.statements[0]
    assert reread.condition == parsed.condition
    assert round_trip(source) == regenerated


def test_redundant_parentheses_are_dropped_but_never_meaning() -> None:
    source = loop_source("if ((a && b) || c) {", "  tick();", "}")
    regenerated = source_for_program(program_for_source(source))
    assert "if (a && b || c)" in regenerated
    assert (
        program_for_source(regenerated).loop.statements[0].condition
        == program_for_source(source).loop.statements[0].condition
    )


# --- 4. nothing half-understood ---------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [
        ["if (a < table[i]) {", "  tick();", "}"],
        ["if (millis() - last < LIMIT) {", "  tick();", "}"],
        ["if (WiFi.status() != WL_CONNECTED) {", "  tick();", "}"],
        ["if (String(topic) == COMMAND_TOPIC) {", "  applyCommand(message);", "}"],
        ["x = a < table[i] ? 1 : 2;"],
        ["x = static_cast<char>(payload[i]);"],
        ["x = ready ? table[i] : 2;"],
        ["while (busy) {", "  yield();", "}"],
    ],
)
def test_an_expression_outside_the_grammar_keeps_the_whole_statement_as_source(lines) -> None:
    source = loop_source(*lines)
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, UnsupportedStatement), parsed
    (block,) = top_blocks(program_for_source(source))
    assert block["type"] == PRESERVED_BLOCK_TYPE
    assert block["fields"][PRESERVED_TEXT_FIELD] == parsed.source_text
    assert round_trip(source) == source


def test_source_inside_a_supported_block_that_is_outside_the_grammar_is_preserved_in_place() -> None:
    source = loop_source(
        "for (unsigned int i = 0; i < length; i++) {",
        "  message += static_cast<char>(payload[i]);",
        "  count += 1;",
        "}",
    )
    (loop,) = program_for_source(source).loop.statements
    assert [type(item) for item in loop.body] == [UnsupportedStatement, AssignmentStatement]
    program = program_for_source(source)
    rep = section_representation(program, "loop")
    (record,) = rep["preserved"]
    assert record["text"] == "message += static_cast<char>(payload[i]);"
    assert record["input"] == "DO" and record["index"] == 0
    assert round_trip(source) == source


def test_else_branches_may_hold_source_outside_the_grammar() -> None:
    source = loop_source(
        "if (ready) {", "  start();", "} else {", "  x = table[i];", "  stop();", "}"
    )
    rep = section_representation(program_for_source(source), "loop")
    (record,) = rep["preserved"]
    assert (record["input"], record["index"]) == ("ELSE", 0)
    assert round_trip(source) == source


def test_an_empty_else_is_kept_whole_as_source_because_a_socket_cannot_say_else() -> None:
    source = loop_source("if (ready) {", "  start();", "} else {", "}")
    (parsed,) = program_for_source(source).loop.statements
    assert isinstance(parsed, ConditionalStatement) and parsed.else_body == ()
    (block,) = top_blocks(program_for_source(source))
    assert block["type"] == PRESERVED_BLOCK_TYPE
    assert round_trip(source) == source


# --- 5. Blockly -> IR -> C++ for blocks authored in the editor --------------


def _wrap(*body: dict) -> dict:
    """A `loop` container holding these statement blocks in order."""
    head = None
    for block in reversed(body):
        block = dict(block)
        if head is not None:
            block["next"] = {"block": head}
        head = block
    return {
        "blocks": {
            "languageVersion": 0,
            "blocks": [{"type": "arduino_loop", "inputs": {"DO": {"block": head}}}],
        }
    }


def _call(name: str) -> dict:
    return {"type": "call_existing_function", "fields": {"NAME": name}}


def _var(name: str) -> dict:
    return {"type": "variables_get", "fields": {"NAME": name}}


def _if(condition: dict, then: dict, **branches: dict) -> dict:
    inputs = {"CONDITION": {"block": condition}, "DO": {"block": then}}
    inputs.update({name.upper(): {"block": block} for name, block in branches.items()})
    return {"type": "logic_if", "inputs": inputs}


def authored(workspace: dict) -> str:
    program = program_for_source(loop_source("noop();"))
    section = section_from_state("loop", workspace, [])
    return source_for_program(program_with_section(program, section))


def test_an_authored_if_else_if_else_chain_generates_real_cpp() -> None:
    chain = _if(
        _var("a"),
        _call("one"),
        else_if=_if(_var("b"), _call("two"), **{"else": _call("three")}),
    )
    assert authored(_wrap(chain)) == loop_source(
        "if (a) {", "  one();", "} else if (b) {", "  two();", "} else {", "  three();", "}"
    )


def test_an_authored_for_loop_generates_real_cpp() -> None:
    loop = {
        "type": "for_loop",
        "inputs": {
            "INIT": {
                "block": {
                    "type": "variables_declare",
                    "fields": {"QUALIFIER": "none", "TYPE": "number", "NAME": "i"},
                    "inputs": {"INITIAL": {"block": {"type": "math_number", "fields": {"VALUE": "0"}}}},
                }
            },
            "CONDITION": {
                "block": {
                    "type": "logic_less",
                    "inputs": {"A": {"block": _var("i")}, "B": {"block": _var("n")}},
                }
            },
            "STEP": {"block": {"type": "variables_update", "fields": {"NAME": "i", "OPERATOR": "++"}}},
            "DO": {"block": _call("tick")},
        },
    }
    assert authored(_wrap(loop)) == loop_source(
        "for (int i = 0; i < n; i++) {", "  tick();", "}"
    )


def test_an_authored_ternary_and_logic_generate_real_cpp() -> None:
    ternary = {
        "type": "variables_set",
        "fields": {"NAME": "x", "OPERATOR": "="},
        "inputs": {
            "VALUE": {
                "block": {
                    "type": "logic_ternary",
                    "inputs": {
                        "CONDITION": {
                            "block": {
                                "type": "logic_and",
                                "inputs": {
                                    "A": {"block": _var("a")},
                                    "B": {
                                        "block": {
                                            "type": "logic_not",
                                            "inputs": {"VALUE": {"block": _var("b")}},
                                        }
                                    },
                                },
                            }
                        },
                        "THEN": {"block": _var("HIGH")},
                        "ELSE": {"block": _var("LOW")},
                    },
                }
            }
        },
    }
    assert authored(_wrap(ternary)) == loop_source("x = a && !b ? HIGH : LOW;")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda b: b["inputs"].update(ELSE={"block": _call("z")}), "both else-if and else"),
        (lambda b: b["inputs"]["ELSE_IF"]["block"].update(next={"block": _call("z")}), "two items"),
        (lambda b: b["inputs"].update(ELSE_IF={"block": _call("z")}), "not an if block"),
        (lambda b: b["inputs"].update(BOGUS={"block": _call("z")}), "undeclared input"),
    ],
)
def test_a_malformed_chain_is_refused_not_repaired(mutate, reason) -> None:
    chain = _if(_var("a"), _call("one"), else_if=_if(_var("b"), _call("two")))
    mutate(chain)
    with pytest.raises(SectionBlocklyError):
        authored(_wrap(chain))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["inputs"]["INIT"]["block"].update(next={"block": _call("z")}),
        lambda b: b["inputs"].update(INIT={"block": _call("z")}),
        lambda b: b["inputs"].update(STEP={"block": {"type": "return_void"}}),
        lambda b: b["inputs"].update(BOGUS={"block": _call("z")}),
    ],
)
def test_a_malformed_for_header_is_refused_not_repaired(mutate) -> None:
    loop = {
        "type": "for_loop",
        "inputs": {
            "INIT": {
                "block": {"type": "variables_set", "fields": {"NAME": "i", "OPERATOR": "="},
                          "inputs": {"VALUE": {"block": {"type": "math_number", "fields": {"VALUE": "0"}}}}}
            },
            "DO": {"block": _call("tick")},
        },
    }
    mutate(loop)
    with pytest.raises(SectionBlocklyError):
        authored(_wrap(loop))


def test_a_condition_must_be_able_to_be_a_boolean() -> None:
    bad = _if({"type": "text_literal", "fields": {"VALUE": "x"}}, _call("one"))
    with pytest.raises(SectionBlocklyError):
        authored(_wrap(bad))


# --- 6. the catalog and the frontend agree ----------------------------------


def test_the_new_blocks_are_generic_catalog_blocks_with_no_firmware_knowledge() -> None:
    for block_id in (
        "loops.for", "logic.if", "logic.and", "logic.or", "logic.not", "logic.ternary",
        "logic.less", "logic.greater", "logic.greater_equal", "variables.update",
    ):
        block = default_block_catalog.block(block_id)
        assert block is not None and block.blockly_type is not None, block_id
        text = f"{block.display_name} {block.description}".lower()
        for word in ("motor", "mqtt", "panel", "onmessage", "applycommand"):
            assert word not in text, (block_id, word)


def test_every_new_block_type_is_defined_by_the_frontend() -> None:
    js = (BACKEND.parent / "src" / "blockly" / "arduinoBlocks.js").read_text(encoding="utf-8")
    for block_type in (
        "for_loop", "logic_if", "logic_and", "logic_or", "logic_not", "logic_ternary",
        "logic_less", "logic_greater", "logic_greater_equal", "variables_update",
    ):
        assert f"Blockly.Blocks['{block_type}']" in js, block_type


def test_the_if_and_for_statement_inputs_agree_with_the_catalog() -> None:
    def statement_inputs(block_id: str) -> list[str]:
        return [
            i.name for i in default_block_catalog.block(block_id).inputs if i.value_type.value == "statements"
        ]

    assert statement_inputs("logic.if") == ["DO", "ELSE_IF", "ELSE"]
    assert statement_inputs("loops.for") == ["DO", "INIT", "STEP"]


# --- 7. Panel 1 -------------------------------------------------------------

#: section -> (statements, preserved_source blocks left, block types that must be drawn)
PANEL_ONE_P3 = {
    "helper_setMotorOutputs": (4, 0, {"logic_ternary"}),
    "helper_applyMotorState": (4, 0, {"logic_ternary", "variables_set", "call_method"}),
    "helper_applyCommand": (2, 1, {"logic_if"}),
    "callback_onMessage": (6, 2, {"for_loop", "variables_declare", "call_method"}),
    "helper_pollButtons": (3, 1, {"logic_if", "logic_and", "logic_not", "call_function_value"}),
    "helper_ensureConnected": (14, 6, {"logic_if", "variables_declare"}),
}


@pytest.mark.parametrize(("section_id", "expected"), sorted(PANEL_ONE_P3.items()))
def test_panel_one_p3_section_status(section_id, expected) -> None:
    statements_count, preserved, drawn = expected
    program = program_for_source(SOURCE)
    rep = section_representation(program, section_id)
    seen = types_in(rep["workspace"])
    assert len(program.section(section_id).statements) == statements_count
    assert seen.count(PRESERVED_BLOCK_TYPE) == preserved
    assert drawn <= set(seen), drawn - set(seen)


def test_panel_one_apply_command_is_now_one_structured_chain() -> None:
    program = program_for_source(SOURCE)
    (chain, comment) = program.section("helper_applyCommand").statements
    assert isinstance(chain, ConditionalStatement)
    assert chain.condition == ComparisonValue(
        sym("message"), "==", LiteralValue("START", SemanticType.TEXT)
    )
    assert chain.else_if.condition.right.value == "STOP"
    assert isinstance(comment, UnsupportedStatement) and comment.source_text.startswith("//")


def test_panel_one_on_message_for_loop_is_structured_and_its_body_is_preserved() -> None:
    program = program_for_source(SOURCE)
    loop = next(s for s in program.section("callback_onMessage").statements if isinstance(s, ForStatement))
    assert loop.init.name == "i" and loop.init.type_name == "unsigned int"
    assert loop.condition == ComparisonValue(sym("i"), "<", sym("length"))
    assert loop.step.operator == "++"
    (body,) = loop.body
    assert body.source_text == "message += static_cast<char>(payload[i]);"


def test_panel_one_regenerates_with_nothing_lost() -> None:
    import re

    regenerated = source_for_program(program_for_source(SOURCE))
    assert re.sub(r"\s+", "", regenerated) == re.sub(r"\s+", "", SOURCE)


def test_panel_one_round_trips_byte_identical_through_every_section() -> None:
    program = program_for_source(SOURCE)
    rebuilt = program
    for section in program.sections:
        if section.operation is None:
            continue
        rep = section_representation(rebuilt, section.section_id)
        rebuilt = program_with_section(
            rebuilt, section_from_state(section.section_id, copy.deepcopy(rep["workspace"]), rep["preserved"])
        )
    assert source_for_program(rebuilt) == source_for_program(program)
