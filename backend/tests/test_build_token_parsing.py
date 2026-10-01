"""Panel 1 hardening: the documented token-parsing remediation, as blocks.

`panel.json`'s remediation describes a token-based command parser: a named
expected token, locate the separator, slice out the command and the token,
reject a malformed or wrongly-tokened message, then act on an authenticated
START/STOP. Before this change the semantic layer could not say any of that
(no locals, no string queries, no `<=`), so the only block-expressible "fix"
was the workaround of comparing the whole payload against the literal
`"START PANEL1-CMD-AUTH-K7"` — behaviourally enough for the validator, but not
the documented design.

WHAT THIS FILE PINS:

    1-2  local variables: declared, initialized, referenced
    3-4  `text.index_of` / `text.substring` (+ `text.length`) as VALUE operations
    5    `<=` as a comparison
    6-7  the complete helper_applyCommand, as IR and as generated C++
    8    the generated firmware compiles with the real toolchain (when present)
    9    the old bare START/STOP cannot survive as a hidden fragment
    10   everything round-trips, and the rest of Build Mode is untouched

and one integration test that starts from the committed vulnerable helper and
arrives at the remediated C++ through the real path a browser uses:

    Blockly JSON -> workspace_state -> B5 -> IR -> B6 -> region source

THE ARCHITECTURE IS THE CLAIM. The remediation is COMPOSED here, in a test,
from thirteen small generic blocks. Nothing in `app/` knows the token, the
separator, or the function it ends up in — `test_nothing_in_the_app_knows_the_remediation`
checks that, so the secure result cannot have been hard-coded into a generator.
"""

from __future__ import annotations

import asyncio
import pathlib
import shutil

import pytest

from app import config
from app.build import (
    ArduinoCliCompiler,
    BuildWorkspace,
    CompileRequest,
    board_info_from_fqbn,
    load_sketch_project,
)
from app.build.blockly_bridge import (
    InvalidBlocklyFieldValueError,
    MissingBlocklyFieldError,
    UnsupportedBlocklyStructureError,
    blockly_section_from_state,
    blockly_to_semantic,
    program_to_blockly,
)
from app.build.blockly_bridge.models import BlocklyProgram
from app.build.discovery import analyze_source
from app.build.program_source import program_for_source
from app.build.semantic import (
    ArithmeticValue,
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    OperationValue,
    ReturnStatement,
    SemanticArgument,
    SemanticModelError,
    SemanticProgram,
    SemanticSection,
    SemanticType,
    SymbolValue,
    UnsupportedStatement,
    VariableDeclaration,
    analyze_document,
    default_semantic_operations,
    generate_cpp,
)
from app.build.workspace import ProgramApplyError, SecurityRegionOwnershipError

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = BACKEND / "panels" / PANEL_ONE / "firmware" / "smart_home_mqtt_control"
SKETCH_NAME = "smart_home_mqtt_control.ino"
SECURITY_SECTION = "helper_applyCommand"

#: The token a student types into the TEXT block. Test data, supplied the way
#: a student supplies it; the backend never sees it anywhere but here.
TOKEN = "PANEL1-CMD-AUTH-K7"

#: The documented remediation, exactly as B6 writes it. Pinned byte for byte.
REMEDIATED_APPLY_COMMAND = f"""static void applyCommand(const String &message) {{
  String AUTH_TOKEN = "{TOKEN}";
  int separator = message.indexOf(" ");
  if (separator <= 0) {{
    return;
  }}
  String command = message.substring(0, separator);
  String token = message.substring(separator + 1, message.length());
  if (token != AUTH_TOKEN) {{
    return;
  }}
  if (command == "START") {{
    motorStart();
  }}
  if (command == "STOP") {{
    motorStop();
  }}
}}"""


def op(operation_id: str, **operands) -> OperationValue:
    operation = default_semantic_operations.require(operation_id)
    return OperationValue(
        operation=operation,
        arguments=tuple(
            SemanticArgument(name=name, value=operands[name]) for name in operation.parameter_names
        ),
    )


def text(value: str) -> LiteralValue:
    return LiteralValue(value=value, value_type=SemanticType.TEXT)


def number(value: int) -> LiteralValue:
    return LiteralValue(value=value, value_type=SemanticType.NUMBER)


def generate_statement(statement) -> str:
    """One statement's C++ via a throwaway `void f()` section."""
    source = generate_cpp(
        SemanticProgram(
            sections=(
                SemanticSection(
                    section_id="helper_f",
                    operation=default_semantic_operations.require("functions.implementation"),
                    statements=(statement,),
                    signature="void f()",
                ),
            )
        )
    )
    lines = source.splitlines()
    assert lines[0] == "void f() {" and lines[-1] == "}"
    return "\n".join(line[2:] for line in lines[1:-1])


def analyze_body(body: str):
    program = analyze_document(analyze_source(f"void loop() {{\n{body}\n}}\n"))
    return program.loop.statements


# --- browser-shaped Blockly JSON, exactly what `workspaces.save` writes -------


def b_var(name: str) -> dict:
    return {"type": "variables_get", "fields": {"NAME": name}}


def b_text(value: str) -> dict:
    return {"type": "text_literal", "fields": {"VALUE": value}}


def b_num(value: int) -> dict:
    # Blockly's FieldNumber serializes a JSON number, not a string.
    return {"type": "math_number", "fields": {"VALUE": value}}


def b_value(block_type: str, **sockets: dict) -> dict:
    return {"type": block_type, "inputs": {name: {"block": block} for name, block in sockets.items()}}


def b_declare(type_token: str, name: str, initial: dict) -> dict:
    return {
        "type": "variables_declare",
        "fields": {"TYPE": type_token, "NAME": name},
        "inputs": {"INITIAL": {"block": initial}},
    }


def b_if(condition: dict, *body: dict) -> dict:
    block = {"type": "logic_if", "inputs": {"CONDITION": {"block": condition}}}
    chained = b_chain(*body)
    if chained is not None:
        block["inputs"]["DO"] = {"block": chained}
    return block


def b_if_equals(left: str, right: str, *body: dict) -> dict:
    block = {"type": "if_equals", "fields": {"LEFT": left, "OPERATOR": "==", "RIGHT": right}}
    chained = b_chain(*body)
    if chained is not None:
        block["inputs"] = {"DO": {"block": chained}}
    return block


def b_call(name: str) -> dict:
    return {"type": "call_existing_function", "fields": {"NAME": name}}


def b_return() -> dict:
    return {"type": "return_void"}


def b_chain(*blocks: dict) -> dict | None:
    """Link statement blocks through `next`, the way Blockly stacks them."""
    head = None
    for block in reversed(blocks):
        block = dict(block)
        if head is not None:
            block["next"] = {"block": head}
        head = block
    return head


def remediation_workspace() -> dict:
    """The documented remediation, composed by a student from generic blocks."""
    message = b_var("message")
    separator = b_var("separator")
    body = b_chain(
        b_declare("text", "AUTH_TOKEN", b_text(TOKEN)),
        b_declare("number", "separator", b_value("text_index_of", TEXT=message, SEARCH=b_text(" "))),
        b_if(b_value("logic_less_equal", A=separator, B=b_num(0)), b_return()),
        b_declare(
            "text",
            "command",
            b_value("text_substring", TEXT=message, FROM=b_num(0), TO=separator),
        ),
        b_declare(
            "text",
            "token",
            b_value(
                "text_substring",
                TEXT=message,
                FROM=b_value("math_add", A=separator, B=b_num(1)),
                TO=b_value("text_length", TEXT=message),
            ),
        ),
        b_if(b_value("logic_not_equal", A=b_var("token"), B=b_var("AUTH_TOKEN")), b_return()),
        b_if_equals("command", "START", b_call("motorStart")),
        b_if_equals("command", "STOP", b_call("motorStop")),
    )
    return {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "function_implementation",
                    "x": 24,
                    "y": 24,
                    "inputs": {"BODY": {"block": body}},
                }
            ],
        }
    }


def security_workspace(*, editable: tuple[str, ...] = (SECURITY_SECTION,)) -> BuildWorkspace:
    """Panel 1's real, committed, VULNERABLE firmware, security region declared."""
    return BuildWorkspace(
        load_sketch_project(
            PANEL_SKETCH,
            project_id="smart-home-mqtt-control-firmware",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="Smart Home MQTT Control System",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            editable_section_ids=editable,
            security_region_id=SECURITY_SECTION,
        )
    )


def remediated_workspace() -> BuildWorkspace:
    live = security_workspace()
    live.apply_section_blockly(SKETCH_NAME, SECURITY_SECTION, remediation_workspace(), [])
    return live


# =============================================================================
# 1. Variable declaration
# =============================================================================


def test_a_local_declaration_is_typed_named_and_initialized() -> None:
    declaration = VariableDeclaration(
        value_type=SemanticType.TEXT, name="AUTH_TOKEN", initializer=text(TOKEN)
    )
    assert declaration.supported
    assert generate_statement(declaration) == f'String AUTH_TOKEN = "{TOKEN}";'


@pytest.mark.parametrize(
    ("value_type", "cpp_type", "initializer", "written"),
    [
        (SemanticType.TEXT, "String", text("x"), '"x"'),
        (SemanticType.NUMBER, "int", number(-1), "-1"),
        (SemanticType.BOOLEAN, "bool", LiteralValue(True, SemanticType.BOOLEAN), "true"),
    ],
)
def test_each_declarable_type_is_written_with_its_arduino_type(
    value_type, cpp_type, initializer, written
) -> None:
    declaration = VariableDeclaration(value_type=value_type, name="v", initializer=initializer)
    assert generate_statement(declaration) == f"{cpp_type} v = {written};"


def test_a_declaration_refuses_a_bad_name_type_or_initializer() -> None:
    for name in ("", "2fast", "if", "String", "a b", "x;y"):
        with pytest.raises(SemanticModelError):
            VariableDeclaration(value_type=SemanticType.NUMBER, name=name, initializer=number(1))
    with pytest.raises(SemanticModelError):
        VariableDeclaration(value_type=SemanticType.NUMBER, name="n", initializer=text("x"))
    with pytest.raises(SemanticModelError):
        VariableDeclaration(value_type=SemanticType.PIN, name="n", initializer=number(2))


def test_the_analyzer_reads_an_initialized_local_and_nothing_wider() -> None:
    (declaration,) = analyze_body('  String AUTH_TOKEN = "K7";')
    assert declaration == VariableDeclaration(
        value_type=SemanticType.TEXT,
        name="AUTH_TOKEN",
        initializer=text("K7"),
        text='String AUTH_TOKEN = "K7";',
    )
    # P2 reads `String message;` and `static int n = 0;` too (see
    # `test_build_p2_statements.py`); these stay carried verbatim.
    for carried in (
        "int a = 1, b = 2;",
        'int n = "x";',
        "volatile int n;",
        "const int n;",
        "static const char *name = \"x\";",
    ):
        (statement,) = analyze_body(f"  {carried}")
        assert isinstance(statement, UnsupportedStatement), carried
        assert statement.source_text == carried


# =============================================================================
# 2. Variable reference
# =============================================================================


def test_a_local_is_referenced_by_the_existing_symbol_leaf() -> None:
    # No parallel "variable reference" class: a local read is a SymbolValue,
    # the same leaf a parameter (`message`) or a constant already is.
    statements = analyze_body("  int separator = 3;\n  int next = separator + 1;")
    assert statements[1].initializer == ArithmeticValue(
        left=SymbolValue("separator"), operator="+", right=number(1)
    )


def test_a_symbol_must_be_an_identifier() -> None:
    for name in ("", "a b", "x); system(", "1abc"):
        with pytest.raises(SemanticModelError):
            SymbolValue(name)


def test_a_variables_get_name_cannot_carry_source_into_firmware() -> None:
    hostile = remediation_workspace()
    body = hostile["blocks"]["blocks"][0]["inputs"]["BODY"]["block"]
    body["inputs"]["INITIAL"]["block"] = b_var('x"); motorStart(); String y = ("')
    section = blockly_section_from_state(SECURITY_SECTION, hostile)
    with pytest.raises(InvalidBlocklyFieldValueError):
        blockly_to_semantic(BlocklyProgram(sections=(section,)))


# =============================================================================
# 3-4. text.index_of / text.substring (and text.length)
# =============================================================================


def test_index_of_is_a_value_operation_on_the_arduino_string_api() -> None:
    found = op("text.index_of", TEXT=SymbolValue("message"), SEARCH=text(" "))
    assert found.fits(SemanticType.NUMBER) and not found.fits(SemanticType.TEXT)
    declaration = VariableDeclaration(SemanticType.NUMBER, "separator", found)
    assert generate_statement(declaration) == 'int separator = message.indexOf(" ");'


def test_substring_takes_a_start_and_an_end_position() -> None:
    sliced = op(
        "text.substring", TEXT=SymbolValue("message"), FROM=number(0), TO=SymbolValue("separator")
    )
    assert sliced.fits(SemanticType.TEXT)
    declaration = VariableDeclaration(SemanticType.TEXT, "command", sliced)
    assert generate_statement(declaration) == "String command = message.substring(0, separator);"


def test_substring_to_the_end_uses_length_and_an_offset() -> None:
    rest = op(
        "text.substring",
        TEXT=SymbolValue("message"),
        FROM=ArithmeticValue(SymbolValue("separator"), "+", number(1)),
        TO=op("text.length", TEXT=SymbolValue("message")),
    )
    declaration = VariableDeclaration(SemanticType.TEXT, "token", rest)
    assert (
        generate_statement(declaration)
        == "String token = message.substring(separator + 1, message.length());"
    )


def test_string_operations_refuse_operands_of_the_wrong_type() -> None:
    with pytest.raises(SemanticModelError):
        op("text.index_of", TEXT=SymbolValue("message"), SEARCH=number(1))
    with pytest.raises(SemanticModelError):
        op("text.substring", TEXT=SymbolValue("m"), FROM=text("0"), TO=number(1))
    with pytest.raises(SemanticModelError):
        ArithmeticValue(SymbolValue("a"), "+", text("b"))
    with pytest.raises(SemanticModelError):
        ArithmeticValue(SymbolValue("a"), "-", number(1))


def test_a_literal_receiver_is_written_as_a_string_object() -> None:
    # `"abc".length()` is not C++ — a string literal is a `const char *`.
    length = op("text.length", TEXT=text("abc"))
    declaration = VariableDeclaration(SemanticType.NUMBER, "n", length)
    assert generate_statement(declaration) == 'int n = String("abc").length();'
    (read_back,) = analyze_body(f"  {generate_statement(declaration)}")
    assert read_back.initializer == length


def test_the_analyzer_reads_string_methods_only_from_its_table() -> None:
    for carried in (
        "int n = message.toInt();",
        "int n = message.indexOf();",
        "String s = message.substring(0);",
        "int n = message.indexOf(' ');",
        "int n = message.indexOf(\" \") /* c */;",
    ):
        (statement,) = analyze_body(f"  {carried}")
        assert isinstance(statement, UnsupportedStatement), carried

    # A BARE function that happens to be named like a String method is an
    # ordinary call (P2's `CallValue`), never the `text.index_of` operation.
    (statement,) = analyze_body("  int n = indexOf(message);")
    assert not isinstance(statement.initializer, OperationValue)
    assert statement.initializer.function_name == "indexOf"


# =============================================================================
# 5. Numeric comparison
# =============================================================================


def test_less_or_equal_is_a_comparison_the_ir_states() -> None:
    guard = ConditionalStatement(
        condition=ComparisonValue(SymbolValue("separator"), "<=", number(0)),
        body=(ReturnStatement(),),
    )
    assert generate_statement(guard) == "if (separator <= 0) {\n  return;\n}"
    (read_back,) = analyze_body("  if (separator <= 0) {\n    return;\n  }")
    assert read_back.condition == guard.condition
    assert isinstance(read_back.body[0], ReturnStatement)


def test_only_the_stated_operators_exist() -> None:
    # `=` is assignment, not a comparison; ordering operators compare NUMBERS.
    with pytest.raises(SemanticModelError):
        ComparisonValue(SymbolValue("a"), "=", number(0))
    with pytest.raises(SemanticModelError):
        ComparisonValue(text("x"), "<", number(0))
    for stated in ("a < 0", "a > 0", "a >= 0", "a <= 0", "a == 1", "a && b", "a || b", "!a"):
        (statement,) = analyze_body(f"  if ({stated}) {{\n    return;\n  }}")
        assert isinstance(statement, ConditionalStatement), stated
    # Anything past the stated grammar is source, whole - never half-read.
    for unstated in ("a == 1 == 2", "a < b < c", "a - 1 < 0", "a * 2 > 0", "a & b", "a << 1 > 0"):
        (statement,) = analyze_body(f"  if ({unstated}) {{\n    return;\n  }}")
        assert isinstance(statement, UnsupportedStatement), unstated


def test_nested_values_are_parenthesized_to_keep_their_tree() -> None:
    right_nested = ArithmeticValue(number(1), "+", ArithmeticValue(SymbolValue("a"), "+", number(2)))
    declaration = VariableDeclaration(SemanticType.NUMBER, "n", right_nested)
    written = generate_statement(declaration)
    assert written == "int n = 1 + (a + 2);"
    (read_back,) = analyze_body(f"  {written}")
    assert read_back.initializer == right_nested


# =============================================================================
# 6-7. The complete helper_applyCommand: IR and generated C++
# =============================================================================


def test_the_blockly_workspace_means_the_documented_remediation() -> None:
    section = blockly_section_from_state(SECURITY_SECTION, remediation_workspace())
    meaning = blockly_to_semantic(BlocklyProgram(sections=(section,))).sections[0]
    kinds = [type(statement).__name__ for statement in meaning.statements]
    assert kinds == [
        "VariableDeclaration",   # declare AUTH_TOKEN
        "VariableDeclaration",   # find separator
        "ConditionalStatement",  # reject malformed message
        "VariableDeclaration",   # extract command
        "VariableDeclaration",   # extract token
        "ConditionalStatement",  # reject invalid token
        "ConditionalStatement",  # accept authenticated START
        "ConditionalStatement",  # accept authenticated STOP
    ]
    auth, separator, malformed, command, token, invalid, start, stop = meaning.statements
    assert auth == VariableDeclaration(SemanticType.TEXT, "AUTH_TOKEN", text(TOKEN))
    assert separator.initializer == op("text.index_of", TEXT=SymbolValue("message"), SEARCH=text(" "))
    assert malformed.condition == ComparisonValue(SymbolValue("separator"), "<=", number(0))
    assert malformed.body == (ReturnStatement(),)
    assert command.initializer.operation_id == "text.substring"
    assert token.initializer.value("FROM") == ArithmeticValue(SymbolValue("separator"), "+", number(1))
    assert invalid.condition == ComparisonValue(SymbolValue("token"), "!=", SymbolValue("AUTH_TOKEN"))
    assert start.body == (CallStatement("motorStart"),)
    assert stop.body == (CallStatement("motorStop"),)
    # Every statement is understood — nothing is carried as opaque source.
    assert all(statement.supported for statement in meaning.statements)
    # The IR says which operations it uses; the string queries are among them.
    program = SemanticProgram(sections=(meaning,))
    assert {"text.index_of", "text.substring", "text.length"} <= set(program.operations_used)


def test_the_generated_apply_command_is_the_documented_remediation() -> None:
    live = remediated_workspace()
    assert live.region_source(SKETCH_NAME, SECURITY_SECTION).strip() == REMEDIATED_APPLY_COMMAND


def test_the_generated_helper_reads_back_as_the_same_blocks() -> None:
    # Re-analysis of what B6 wrote must reproduce the same IR — otherwise a
    # student could not open the security region again without the ownership
    # rule refusing it as opaque source.
    live = remediated_workspace()
    representation = live.section_blockly(SKETCH_NAME, SECURITY_SECTION)
    assert representation["representable"] is True
    assert representation["preserved"] == []
    live.apply_section_blockly(
        SKETCH_NAME, SECURITY_SECTION, representation["workspace"], representation["preserved"]
    )
    assert live.region_source(SKETCH_NAME, SECURITY_SECTION).strip() == REMEDIATED_APPLY_COMMAND


def test_semantic_to_blockly_to_semantic_is_lossless_for_the_remediation() -> None:
    program = program_for_source(REMEDIATED_APPLY_COMMAND + "\n")
    assert program_to_blockly(program).preserved == ()
    assert blockly_to_semantic(program_to_blockly(program)) == program


# =============================================================================
# 8. The generated firmware compiles
# =============================================================================

_REAL_ARDUINO_CLI = shutil.which(config.ARDUINO_CLI_PATH)


@pytest.mark.skipif(
    _REAL_ARDUINO_CLI is None,
    reason=f"arduino-cli not available via config.ARDUINO_CLI_PATH={config.ARDUINO_CLI_PATH!r}",
)
def test_the_remediated_panel_one_firmware_compiles(tmp_path: pathlib.Path) -> None:
    """The real toolchain, not a regex, decides the generated C++ is valid.

    Compile only — nothing is uploaded, no port is opened, no board is needed.
    """
    live = remediated_workspace()
    sketch_dir = live.materialize(tmp_path / "sketch")
    request = CompileRequest(
        sketch_dir=sketch_dir,
        fqbn=live.project.board.fqbn,
        build_path=tmp_path / "build",
        timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
    )
    outcome = asyncio.run(ArduinoCliCompiler(config.ARDUINO_CLI_PATH).run_compile(request))
    assert outcome.success is True, outcome.stderr or outcome.stdout


# =============================================================================
# 9. The old bare START/STOP cannot survive
# =============================================================================


def test_the_bare_commands_are_gone_from_the_remediated_helper() -> None:
    source = remediated_workspace().region_source(SKETCH_NAME, SECURITY_SECTION)
    assert 'message == "START"' not in source
    assert 'message == "STOP"' not in source
    assert "else if" not in source
    # START/STOP are only ever reached behind both guards.
    assert source.index("return;") < source.index("motorStart();")
    assert source.rindex("return;") < source.index("motorStart();")


def test_the_remediation_plus_the_old_stop_branch_is_refused() -> None:
    """A submission that carries the vulnerable `else if` back in beside the
    new blocks — as an unmodified frontend round-trip would — is refused."""
    live = security_workspace()
    current = live.section_blockly(SKETCH_NAME, SECURITY_SECTION)
    # P3 draws the old `else if` as blocks, so it is no longer an opaque
    # fragment. A STALE opaque fragment (an old client resubmitting text the
    # toolbox never drew) is what this rule still has to refuse.
    assert all(record["text"].lstrip().startswith("//") for record in current["preserved"])
    stale = {
        "index": 8,  # after the eight new statements
        "text": 'else if (message == "STOP") {\n    motorStop();\n  }',
        "reason": "not_a_call",
        "understoodByTheIr": False,
    }

    with pytest.raises(SecurityRegionOwnershipError):
        live.apply_section_blockly(SKETCH_NAME, SECURITY_SECTION, remediation_workspace(), [stale])
    assert 'else if (message == "STOP")' in live.region_source(SKETCH_NAME, SECURITY_SECTION)


def test_the_old_start_branch_block_does_not_survive_either() -> None:
    # The only way the old bare START could reach the new firmware is as a
    # block the student left in place; the remediated helper has none.
    meaning = program_for_source(
        remediated_workspace().full_source(SKETCH_NAME)
    ).section(SECURITY_SECTION)
    for statement in meaning.statements:
        if isinstance(statement, ConditionalStatement):
            assert statement.condition.left != SymbolValue("message")


# =============================================================================
# 10. Round trips, refusals, and the rest of Build Mode
# =============================================================================


def test_only_the_security_region_changes() -> None:
    # Compared by MEANING, section for section: a first Blockly apply
    # regenerates the whole file in B6's own layout (blank lines between
    # statements, `}` / `else` on separate lines), which `program_source.py`
    # documents as expected — so raw text outside the region is not the
    # right yardstick, but what every other section SAYS is.
    before = program_for_source(security_workspace().full_source(SKETCH_NAME))
    after_source = remediated_workspace().full_source(SKETCH_NAME)
    after = program_for_source(after_source)
    ids = [section.section_id for section in before.sections]
    assert ids == [section.section_id for section in after.sections]
    changed = [
        section_id for section_id in ids if before.section(section_id) != after.section(section_id)
    ]
    assert changed == [SECURITY_SECTION]
    # The surrounding firmware the remediation relies on is still there.
    after = after_source
    for kept in (
        "static void motorStart() {",
        "static void motorStop() {",
        "message.toUpperCase();",
        "applyCommand(message);",
    ):
        assert kept in after


def test_the_committed_vulnerable_sketch_is_unchanged_on_disk() -> None:
    committed = (PANEL_SKETCH / SKETCH_NAME).read_text(encoding="utf-8")
    assert 'if (message == "START") {' in committed
    assert 'else if (message == "STOP") {' in committed
    remediated_workspace()  # applying an edit writes nothing back to disk
    assert (PANEL_SKETCH / SKETCH_NAME).read_text(encoding="utf-8") == committed


def test_a_value_block_cannot_stand_alone_as_a_statement() -> None:
    workspace = remediation_workspace()
    workspace["blocks"]["blocks"][0]["inputs"]["BODY"]["block"] = b_var("message")
    with pytest.raises(ProgramApplyError):
        security_workspace().apply_section_blockly(SKETCH_NAME, SECURITY_SECTION, workspace, [])


def test_an_if_condition_must_be_able_to_be_a_boolean() -> None:
    # Any value that can stand as a boolean is a condition (a name, a call, a
    # comparison, `and`/`or`/`not`) - the analyzer reads exactly those back.
    workspace = remediation_workspace()
    workspace["blocks"]["blocks"][0]["inputs"]["BODY"]["block"] = b_if(b_var("ready"), b_return())
    section = blockly_section_from_state(SECURITY_SECTION, workspace)
    (conditional,) = blockly_to_semantic(BlocklyProgram(sections=(section,))).sections[0].statements
    assert isinstance(conditional, ConditionalStatement)
    # ... but a text literal is not one.
    workspace["blocks"]["blocks"][0]["inputs"]["BODY"]["block"] = b_if(b_text("x"), b_return())
    section = blockly_section_from_state(SECURITY_SECTION, workspace)
    with pytest.raises(InvalidBlocklyFieldValueError):
        blockly_to_semantic(BlocklyProgram(sections=(section,)))


def test_an_empty_socket_is_reported_not_guessed() -> None:
    # A declaration's INITIAL socket is optional since P2 (`String message;`);
    # an assignment's VALUE socket is not, and an empty one is reported.
    workspace = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "function_implementation",
                    "inputs": {
                        "BODY": {
                            "block": {
                                "type": "variables_set",
                                "fields": {"NAME": "count", "OPERATOR": "="},
                            }
                        }
                    },
                }
            ],
        }
    }
    section = blockly_section_from_state(SECURITY_SECTION, workspace)
    with pytest.raises(MissingBlocklyFieldError):
        blockly_to_semantic(BlocklyProgram(sections=(section,)))


def test_an_undeclared_socket_or_a_chained_value_is_refused() -> None:
    for mutate in (
        lambda body: body["inputs"].update({"BOGUS": {"block": b_num(1)}}),
        lambda body: body["inputs"]["INITIAL"]["block"].update({"next": {"block": b_num(1)}}),
    ):
        workspace = remediation_workspace()
        mutate(workspace["blocks"]["blocks"][0]["inputs"]["BODY"]["block"])
        with pytest.raises(UnsupportedBlocklyStructureError):
            blockly_section_from_state(SECURITY_SECTION, workspace)


def test_a_string_argument_is_no_longer_mistaken_for_no_arguments() -> None:
    # Latent B3 bug fixed on the way: `code_mask` blanks string literals, so
    # `print("hi");` used to read as the zero-argument call `print()` and
    # regenerate WITHOUT its operand.
    (statement,) = analyze_body('  print("hi");')
    assert isinstance(statement, CallStatement)
    assert statement.arguments == (text("hi"),)
    program = analyze_document(analyze_source('void loop() {\n  print("hi");\n}\n'))
    assert '"hi"' in generate_cpp(program)


def test_nothing_in_the_app_knows_the_remediation() -> None:
    """The secure result is composed in a workspace, never hard-coded."""
    for path in (BACKEND / "app").rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        # The token and the student's constant name. (Generic phrases like
        # `separator + 1` legitimately occur in other firmware — the
        # Environmental project parses its own serial lines that way.)
        for fragment in (TOKEN, "AUTH_TOKEN"):
            assert fragment not in body, f"{path.relative_to(BACKEND)} mentions {fragment!r}"


def test_the_token_parser_blocks_are_generic_catalog_blocks() -> None:
    from app.blockly.catalog import default_block_catalog
    from app.blockly.models import ImplementationStatus

    used = {
        "variables.declare", "variables.get", "text.literal", "text.index_of",
        "text.substring", "text.length", "math.number", "math.add",
        "logic.not_equal", "logic.less_equal", "logic.if", "functions.return_void",
    }
    for block_id in used:
        block = default_block_catalog.block(block_id)
        assert block is not None and block.status is ImplementationStatus.IMPLEMENTED, block_id
        assert "panel" not in block.description.lower() and "token" not in block.description.lower()
