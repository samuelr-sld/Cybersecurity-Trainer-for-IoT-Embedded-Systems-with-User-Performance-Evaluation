"""Phase B6 — the semantic IR -> Arduino C++ generator (`semantic/generator.py`).

The direction that closes the loop B1/B3/B4/B5 opened:

    .ino -> B1 -> B3 -> B4 -> Blockly -> B5 -> B6 -> .ino

The five things this suite exists to prove, beyond ordinary rendering:

  * UNSUPPORTED SOURCE SURVIVES, CHARACTER FOR CHARACTER. Panel 1's Wi-Fi
    setup, its MQTT client and credentials, its callback, its helpers and its
    globals are none of them representable in five operations, and every one
    of them comes back out of the generator exactly as it went in — not
    reformatted, not commented out, not translated, not dropped;
  * GENERATION NEVER DEPENDS ON PROVENANCE. A statement authored in the
    editor carries no source text at all and generates the same C++ as the
    identical statement read from firmware. `source_text` is never written;
  * ORDER IS THE DOCUMENT'S. Sections, and the statements inside them, come
    out in the order the IR holds them, supported and preserved interleaved
    exactly as they were;
  * NOTHING IS EVER SILENTLY OMITTED. An operation the registry does not
    declare, an operand that is not there, a value with no C++ form: every one
    of them raises. There is no skip path;
  * IT IS A PURE STRING FUNCTION. No filesystem, no process, no arduino-cli,
    no Blockly, no session — B7 owns compilation, and nothing here reaches
    toward it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

#: The default Build Mode project's program, imported rather than copied so
#: this fixture cannot drift from the firmware a student actually opens — and
#: so the exact-reconstruction test below is a statement about real project
#: source, not about a string written to pass.
from app.build.blink import _MAIN_INO_PROGRAM_DEFAULT as BLINK_PROGRAM
from app.build.blockly_bridge import blockly_to_semantic, program_to_blockly
from app.build.discovery import analyze_source, code_mask
from app.build.semantic import (
    FUNCTIONS_IMPLEMENTATION,
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    INDENT,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TIME_DELAY,
    CallEmission,
    CppEmissionTable,
    CppGenerationError,
    FunctionEmission,
    InvalidContainerError,
    InvalidSemanticValueError,
    LiteralValue,
    MissingOperationArgumentError,
    OperationForm,
    OperationStatement,
    SemanticArgument,
    SemanticError,
    SemanticOperation,
    SemanticOperationRegistry,
    SemanticParameter,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticType,
    SemanticValue,
    SymbolValue,
    UnsupportedOperationError,
    UnsupportedReason,
    UnsupportedStatement,
    analyze_document,
    build_default_emissions,
    default_cpp_emissions,
    default_semantic_operations,
    generate_cpp,
)
from app.build.semantic.errors import SemanticModelError

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
SEMANTIC_DIR = BACKEND_DIR / "app" / "build" / "semantic"
GENERATOR_MODULE = SEMANTIC_DIR / "generator.py"
EMISSIONS_MODULE = SEMANTIC_DIR / "emissions.py"
_B6_MODULES = (GENERATOR_MODULE, EMISSIONS_MODULE)

PANEL_ONE_INO = (
    BACKEND_DIR
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)

def semantic(source: str) -> SemanticProgram:
    """Source text through B1 and B3."""
    return analyze_document(analyze_source(source))


def generated(source: str) -> str:
    """Source text through B1, B3 and B6."""
    return generate_cpp(semantic(source))


def panel_one_source() -> str:
    return PANEL_ONE_INO.read_text(encoding="utf-8")


def operation(operation_id: str) -> SemanticOperation:
    return default_semantic_operations.require(operation_id)


def statement(operation_id: str, *, text: str | None = None, **values: SemanticValue):
    """One `OperationStatement`, with or without the source it came from."""
    return OperationStatement(
        operation=operation(operation_id),
        arguments=tuple(
            SemanticArgument(name=name, value=value) for name, value in values.items()
        ),
        text=text,
    )


def container(operation_id: str, *statements: SemanticStatement) -> SemanticSection:
    section_id = {PROGRAM_SETUP: "setup", PROGRAM_LOOP: "loop"}[operation_id]
    return SemanticSection(
        section_id=section_id, operation=operation(operation_id), statements=statements
    )


def preserved(text: str, section_id: str = "global") -> SemanticSection:
    return SemanticSection(
        section_id=section_id,
        operation=None,
        statements=(
            UnsupportedStatement(text=text, reason=UnsupportedReason.UNSUPPORTED_SECTION),
        ),
    )


def number(value: int | float) -> LiteralValue:
    return LiteralValue(value=value, value_type=SemanticType.NUMBER)


# =============================================================================
# 1. The five operations, written as real Arduino C++
# =============================================================================


def test_program_setup_becomes_an_arduino_setup_function():
    written = generate_cpp(SemanticProgram(sections=(container(PROGRAM_SETUP),)))
    assert written == "void setup() {\n}\n"


def test_program_loop_becomes_an_arduino_loop_function():
    written = generate_cpp(SemanticProgram(sections=(container(PROGRAM_LOOP),)))
    assert written == "void loop() {\n}\n"


def test_pin_mode_becomes_a_real_pin_mode_call():
    written = generate_cpp(
        SemanticProgram(
            sections=(
                container(
                    PROGRAM_SETUP,
                    statement(
                        GPIO_PIN_MODE,
                        PIN=SymbolValue("START_BUTTON"),
                        MODE=SymbolValue("INPUT_PULLUP"),
                    ),
                ),
            )
        )
    )
    assert written == "void setup() {\n  pinMode(START_BUTTON, INPUT_PULLUP);\n}\n"


def test_digital_write_becomes_a_real_digital_write_call():
    written = generate_cpp(
        SemanticProgram(
            sections=(
                container(
                    PROGRAM_LOOP,
                    statement(
                        GPIO_DIGITAL_WRITE,
                        PIN=SymbolValue("GREEN_LED"),
                        VALUE=SymbolValue("HIGH"),
                    ),
                ),
            )
        )
    )
    assert written == "void loop() {\n  digitalWrite(GREEN_LED, HIGH);\n}\n"


def test_delay_becomes_a_real_delay_call():
    written = generate_cpp(
        SemanticProgram(sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(500))),))
    )
    assert written == "void loop() {\n  delay(500);\n}\n"


def test_a_body_is_indented_one_conventional_level():
    written = generated("void loop() {\n  delay(5);\n}\n")
    assert f"\n{INDENT}delay(5);\n" in written


# =============================================================================
# 2. Values — literals keep their type, symbols keep their name
# =============================================================================


def test_an_integer_literal_is_written_as_itself():
    assert "delay(1000);" in generate_cpp(
        SemanticProgram(sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(1000))),))
    )


def test_a_negative_and_a_decimal_literal_keep_their_written_form():
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_LOOP,
                statement(TIME_DELAY, MS=number(-5)),
                statement(TIME_DELAY, MS=number(0.5)),
            ),
        )
    )
    written = generate_cpp(program)
    assert "delay(-5);" in written
    assert "delay(0.5);" in written


def test_a_boolean_literal_is_written_true_or_false_and_never_as_a_number():
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_LOOP,
                statement(
                    GPIO_DIGITAL_WRITE,
                    PIN=number(2),
                    VALUE=LiteralValue(value=True, value_type=SemanticType.BOOLEAN),
                ),
                statement(
                    GPIO_DIGITAL_WRITE,
                    PIN=number(2),
                    VALUE=LiteralValue(value=False, value_type=SemanticType.BOOLEAN),
                ),
            ),
        )
    )
    written = generate_cpp(program)
    assert "digitalWrite(2, true);" in written
    assert "digitalWrite(2, false);" in written
    assert "digitalWrite(2, 1);" not in written


def test_a_symbol_is_written_as_its_name_and_is_never_resolved():
    # The IR does not resolve names and neither does the generator: the panel's
    # START_BUTTON is 32 in its firmware, and writing 32 here would put a number
    # in the student's source that the student never wrote.
    written = generate_cpp(
        SemanticProgram(
            sections=(
                container(
                    PROGRAM_SETUP,
                    statement(
                        GPIO_PIN_MODE,
                        PIN=SymbolValue("START_BUTTON"),
                        MODE=SymbolValue("OUTPUT"),
                    ),
                ),
            )
        )
    )
    assert "pinMode(START_BUTTON, OUTPUT);" in written
    assert "32" not in written


def test_a_text_literal_is_quoted_and_escaped_back_into_c_plus_plus():
    written = generate_cpp(
        SemanticProgram(
            sections=(
                container(
                    PROGRAM_SETUP,
                    statement(
                        GPIO_PIN_MODE,
                        PIN=number(2),
                        MODE=LiteralValue(value='a"b\\c\nd', value_type=SemanticType.TEXT),
                    ),
                ),
            )
        )
    )
    assert 'pinMode(2, "a\\"b\\\\c\\nd");' in written


def test_a_text_literal_round_trips_through_the_analyzer_unchanged():
    # The generator's escaping is the inverse of the analyzer's unescaping, and
    # this is the test that keeps the two from drifting apart.
    original = LiteralValue(value='say "hi"\tnow', value_type=SemanticType.TEXT)
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_SETUP,
                statement(GPIO_PIN_MODE, PIN=number(2), MODE=original),
            ),
        )
    )
    reread = semantic(generate_cpp(program)).setup.operation_statements[0]
    assert reread.value("MODE") == original


def test_a_non_finite_number_is_refused_rather_than_written():
    program = SemanticProgram(
        sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(float("inf")))),)
    )
    with pytest.raises(InvalidSemanticValueError):
        generate_cpp(program)


def test_a_control_character_in_text_is_refused_rather_than_guessed_at():
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_SETUP,
                statement(
                    GPIO_PIN_MODE,
                    PIN=number(2),
                    MODE=LiteralValue(value="bell\x07", value_type=SemanticType.TEXT),
                ),
            ),
        )
    )
    with pytest.raises(InvalidSemanticValueError):
        generate_cpp(program)


def test_a_value_form_the_ir_does_not_declare_has_no_c_plus_plus_form():
    class Invented(SemanticValue):
        __slots__ = ()

        def fits(self, value_type):
            return True

        @property
        def source_text(self):
            return "whatever"

    invented = OperationStatement(
        operation=operation(TIME_DELAY),
        arguments=(SemanticArgument(name="MS", value=Invented()),),
    )
    with pytest.raises(InvalidSemanticValueError):
        generate_cpp(SemanticProgram(sections=(container(PROGRAM_LOOP, invented),)))


# =============================================================================
# 3. Order — statements, containers and sections stay where they were
# =============================================================================


def test_statements_are_written_in_their_semantic_order():
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_LOOP,
                statement(GPIO_DIGITAL_WRITE, PIN=number(2), VALUE=SymbolValue("HIGH")),
                statement(TIME_DELAY, MS=number(1)),
                statement(GPIO_DIGITAL_WRITE, PIN=number(2), VALUE=SymbolValue("LOW")),
                statement(TIME_DELAY, MS=number(2)),
            ),
        )
    )
    assert generate_cpp(program) == (
        "void loop() {\n"
        "  digitalWrite(2, HIGH);\n"
        "  delay(1);\n"
        "  digitalWrite(2, LOW);\n"
        "  delay(2);\n"
        "}\n"
    )


def test_containers_are_written_in_their_program_order_even_when_loop_comes_first():
    # Nothing hoists setup above loop, and nothing assumes the two are adjacent.
    program = SemanticProgram(
        sections=(
            container(PROGRAM_LOOP),
            preserved("static void helper() {}", section_id="helper_helper"),
            container(PROGRAM_SETUP),
        )
    )
    assert generate_cpp(program) == (
        "void loop() {\n"
        "}\n"
        "\n"
        "static void helper() {}\n"
        "\n"
        "void setup() {\n"
        "}\n"
    )


def test_supported_and_unsupported_statements_interleave_exactly_as_held():
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_SETUP,
                statement(GPIO_PIN_MODE, PIN=SymbolValue("LED"), MODE=SymbolValue("OUTPUT")),
                UnsupportedStatement(
                    text="Serial.begin(115200);", reason=UnsupportedReason.NOT_A_CALL
                ),
                statement(TIME_DELAY, MS=number(10)),
            ),
        )
    )
    assert generate_cpp(program) == (
        "void setup() {\n"
        "  pinMode(LED, OUTPUT);\n"
        "  Serial.begin(115200);\n"
        "  delay(10);\n"
        "}\n"
    )


def test_a_section_holding_nothing_writes_nothing_rather_than_a_blank_it_never_had():
    # B3 represents the whitespace run between two functions as a section with
    # no statements. It means nothing, so it writes nothing.
    program = SemanticProgram(
        sections=(
            container(PROGRAM_SETUP),
            SemanticSection(section_id="global_2", operation=None, statements=()),
            container(PROGRAM_LOOP),
        )
    )
    assert generate_cpp(program) == "void setup() {\n}\n\nvoid loop() {\n}\n"


def test_a_program_that_says_nothing_writes_nothing():
    assert generate_cpp(SemanticProgram(sections=())) == ""


# =============================================================================
# 4. Unsupported source is emitted verbatim, never interpreted
# =============================================================================


def test_unsupported_source_is_emitted_exactly_as_it_was_carried():
    fragment = "client.publish(STATE_TOPIC, motorRunning ? \"RUNNING\" : \"STOPPED\", true);"
    program = SemanticProgram(sections=(preserved(fragment),))
    assert generate_cpp(program) == fragment + "\n"


def test_unsupported_source_is_not_normalized_reformatted_or_commented_out():
    fragment = "int   table[]  =  {1,2,   3};   // a comment nobody touches"
    written = generate_cpp(SemanticProgram(sections=(preserved(fragment),)))
    assert fragment in written
    assert not written.startswith("//")
    assert "/*" not in written


def test_a_multi_line_unsupported_fragment_keeps_its_own_inner_lines():
    # Only the leading indentation B3 stripped from the FIRST line is restored;
    # the fragment's own lines are source this generator must not touch.
    fragment = "while (WiFi.status() != WL_CONNECTED) {\n    delay(500);\n  }"
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_SETUP,
                UnsupportedStatement(text=fragment, reason=UnsupportedReason.NOT_A_CALL),
            ),
        )
    )
    assert generate_cpp(program) == (
        "void setup() {\n"
        "  while (WiFi.status() != WL_CONNECTED) {\n"
        "    delay(500);\n"
        "  }\n"
        "}\n"
    )


def test_top_level_unsupported_source_is_written_at_column_zero():
    fragment = "#include <PubSubClient.h>\nstatic const char *MQTT_BROKER = \"192.168.50.1\";"
    written = generate_cpp(SemanticProgram(sections=(preserved(fragment),)))
    assert written == fragment + "\n"


def test_an_unknown_call_is_preserved_rather_than_translated():
    written = generated("void loop() {\n  ensureConnected();\n}\n")
    assert "ensureConnected();" in written


# =============================================================================
# 5. Newly authored operations — no source text, and none needed
# =============================================================================


def test_an_authored_statement_with_no_source_text_generates_valid_c_plus_plus():
    authored = statement(TIME_DELAY, MS=number(250))
    assert authored.source_text is None
    assert authored.has_source is False
    written = generate_cpp(SemanticProgram(sections=(container(PROGRAM_LOOP, authored),)))
    assert written == "void loop() {\n  delay(250);\n}\n"


def test_an_authored_statement_generates_exactly_what_its_source_backed_twin_does():
    values = {"PIN": SymbolValue("GREEN_LED"), "VALUE": SymbolValue("HIGH")}
    authored = container(PROGRAM_LOOP, statement(GPIO_DIGITAL_WRITE, **values))
    from_source = container(
        PROGRAM_LOOP,
        statement(GPIO_DIGITAL_WRITE, text="digitalWrite(GREEN_LED,HIGH) ;", **values),
    )
    assert generate_cpp(SemanticProgram(sections=(authored,))) == generate_cpp(
        SemanticProgram(sections=(from_source,))
    )


def test_recorded_source_is_provenance_and_is_never_written_out():
    # A statement whose recorded text disagrees with its meaning proves the
    # generator writes the MEANING. Nothing falls back to the string.
    misleading = statement(
        TIME_DELAY, text="delay(99999); // what the file used to say", MS=number(7)
    )
    written = generate_cpp(SemanticProgram(sections=(container(PROGRAM_LOOP, misleading),)))
    assert "delay(7);" in written
    assert "99999" not in written


def test_the_ir_still_refuses_source_text_that_is_present_but_empty():
    # Optional does not mean lax: absent is a statement nobody read from a file,
    # while blank is a claim about source that says nothing.
    with pytest.raises(SemanticModelError):
        statement(TIME_DELAY, text="   ", MS=number(1))


def test_an_editor_authored_block_reaches_c_plus_plus_through_the_whole_bridge():
    # The B5/B6 seam, end to end: a block with no source becomes a statement
    # with no source, which the generator writes from its meaning alone.
    from app.build.blockly_bridge import BlocklyBlock, BlocklyField, BlocklyProgram, BlocklySection
    from app.build.blockly_bridge import block_definition_for
    from app.blockly.catalog import default_block_catalog

    delay_block = block_definition_for(TIME_DELAY, default_block_catalog)
    setup_block = block_definition_for(PROGRAM_SETUP, default_block_catalog)
    body_input = next(
        item.name for item in setup_block.inputs if item.value_type.value == "statements"
    )
    workspace = BlocklyProgram(
        sections=(
            BlocklySection(
                section_id="setup",
                block=BlocklyBlock(
                    operation_id=PROGRAM_SETUP,
                    block_id=setup_block.block_id,
                    block_type=setup_block.blockly_type,
                    body_input=body_input,
                    body=(
                        BlocklyBlock(
                            operation_id=TIME_DELAY,
                            block_id=delay_block.block_id,
                            block_type=delay_block.blockly_type,
                            fields=(BlocklyField(name="MS", value="750"),),
                        ),
                    ),
                ),
            ),
        )
    )
    program = blockly_to_semantic(workspace)
    assert program.setup.operation_statements[0].has_source is False
    assert generate_cpp(program) == "void setup() {\n  delay(750);\n}\n"


# =============================================================================
# 6. Determinism and purity
# =============================================================================


def test_generation_is_deterministic_for_the_real_panel_one_firmware():
    program = semantic(panel_one_source())
    assert generate_cpp(program) == generate_cpp(program)


def test_two_independent_runs_produce_byte_identical_source():
    source = panel_one_source()
    assert generated(source) == generated(source)


def test_generating_does_not_touch_the_program_it_was_given():
    program = semantic(panel_one_source())
    before = program
    generate_cpp(program)
    assert program == before
    assert program.sections is before.sections


def test_generating_twice_over_its_own_output_reaches_a_fixpoint():
    # The second pass changes nothing: whatever layout B6 imposes, it imposes
    # once. A generator that kept re-indenting its own output would fail here.
    once = generated(panel_one_source())
    assert generated(once) == once


# =============================================================================
# 7. Nothing is silently omitted — every refusal is loud
# =============================================================================


def _registry_without(operation_id: str) -> SemanticOperationRegistry:
    return SemanticOperationRegistry(
        tuple(
            item
            for item in default_semantic_operations.operations
            if item.operation_id != operation_id
        )
    )


def test_an_operation_the_registry_does_not_declare_is_never_written():
    program = SemanticProgram(
        sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(1))),)
    )
    with pytest.raises(UnsupportedOperationError, match="no such operation"):
        generate_cpp(program, operations=_registry_without(TIME_DELAY))


def test_an_operation_with_no_declared_emission_is_never_spelled():
    program = SemanticProgram(
        sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(1))),)
    )
    partial = CppEmissionTable(
        {
            item: default_cpp_emissions.require(item)
            for item in default_cpp_emissions.operation_ids
            if item != TIME_DELAY
        }
    )
    with pytest.raises(UnsupportedOperationError, match=r"no C\+\+ emission"):
        generate_cpp(program, emissions=partial)


def test_a_declared_parameter_with_no_operand_is_reported_rather_than_skipped():
    # The statement was built against the default table; the registry the
    # generator is handed declares an operand it was never built with.
    widened = SemanticOperationRegistry(
        tuple(
            SemanticOperation(
                operation_id=TIME_DELAY,
                form=OperationForm.STATEMENT,
                parameters=(
                    SemanticParameter("MS", SemanticType.NUMBER),
                    SemanticParameter("UNIT", SemanticType.TEXT),
                ),
            )
            if item.operation_id == TIME_DELAY
            else item
            for item in default_semantic_operations.operations
        )
    )
    program = SemanticProgram(
        sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(1))),)
    )
    with pytest.raises(MissingOperationArgumentError, match="UNIT"):
        generate_cpp(program, operations=widened)


def test_an_operand_no_declared_parameter_names_is_never_dropped_from_the_call():
    narrowed = SemanticOperationRegistry(
        tuple(
            SemanticOperation(
                operation_id=GPIO_PIN_MODE,
                form=OperationForm.STATEMENT,
                parameters=(SemanticParameter("PIN", SemanticType.PIN),),
            )
            if item.operation_id == GPIO_PIN_MODE
            else item
            for item in default_semantic_operations.operations
        )
    )
    program = SemanticProgram(
        sections=(
            container(
                PROGRAM_SETUP,
                statement(GPIO_PIN_MODE, PIN=SymbolValue("LED"), MODE=SymbolValue("OUTPUT")),
            ),
        )
    )
    with pytest.raises(UnsupportedOperationError, match="MODE"):
        generate_cpp(program, operations=narrowed)


def test_a_container_the_registry_calls_a_statement_is_reported():
    confused = SemanticOperationRegistry(
        tuple(
            SemanticOperation(operation_id=PROGRAM_SETUP, form=OperationForm.STATEMENT)
            if item.operation_id == PROGRAM_SETUP
            else item
            for item in default_semantic_operations.operations
        )
    )
    with pytest.raises(InvalidContainerError):
        generate_cpp(SemanticProgram(sections=(container(PROGRAM_SETUP),)), operations=confused)


def test_a_container_spelled_as_a_call_is_reported():
    swapped = CppEmissionTable(
        {
            **{item: default_cpp_emissions.require(item) for item in
               default_cpp_emissions.operation_ids},
            PROGRAM_SETUP: CallEmission(call_name="setup"),
        }
    )
    with pytest.raises(InvalidContainerError, match="function definition"):
        generate_cpp(SemanticProgram(sections=(container(PROGRAM_SETUP),)), emissions=swapped)


def test_a_statement_spelled_as_a_function_definition_is_reported():
    swapped = CppEmissionTable(
        {
            **{item: default_cpp_emissions.require(item) for item in
               default_cpp_emissions.operation_ids},
            TIME_DELAY: FunctionEmission(return_type="void", name="delay"),
        }
    )
    program = SemanticProgram(
        sections=(container(PROGRAM_LOOP, statement(TIME_DELAY, MS=number(1))),)
    )
    with pytest.raises(InvalidContainerError, match="written as a call"):
        generate_cpp(program, emissions=swapped)


def test_a_statement_form_the_ir_does_not_declare_has_no_generation_rule():
    class Invented(SemanticStatement):
        __slots__ = ()

        @property
        def source_text(self):
            return "invented();"

        @property
        def supported(self):
            return False

    section = SemanticSection(
        section_id="setup", operation=operation(PROGRAM_SETUP), statements=(Invented(),)
    )
    with pytest.raises(InvalidContainerError):
        generate_cpp(SemanticProgram(sections=(section,)))


@pytest.mark.parametrize("malformed", [None, "void setup() {}", 42, ()])
def test_something_that_is_not_a_program_is_refused(malformed):
    with pytest.raises(TypeError):
        generate_cpp(malformed)


def test_every_generation_failure_shares_the_semantic_base_class():
    for error in (
        UnsupportedOperationError,
        InvalidContainerError,
        MissingOperationArgumentError,
        InvalidSemanticValueError,
    ):
        assert issubclass(error, CppGenerationError)
        assert issubclass(error, SemanticError)
        assert issubclass(error, ValueError)


# =============================================================================
# 8. The emission table — closed, validated, and agreeing with the analyzer
# =============================================================================


def test_every_declared_operation_has_exactly_one_emission():
    # `functions.implementation` is the one declared exception: its C++
    # declarator is preserved verbatim per-section
    # (`SemanticSection.signature`), never a fixed spelling this table could
    # hold — `generator.py::_section` skips the emissions lookup entirely
    # whenever a signature is present.
    operations_needing_emissions = set(default_semantic_operations.operation_ids) - {
        FUNCTIONS_IMPLEMENTATION
    }
    assert sorted(default_cpp_emissions.operation_ids) == sorted(operations_needing_emissions)


def test_the_emission_table_declares_nothing_the_ir_does_not():
    for operation_id in default_cpp_emissions.operation_ids:
        assert operation_id in default_semantic_operations


def test_every_emission_matches_its_operations_form():
    for operation_id in default_cpp_emissions.operation_ids:
        declared = default_semantic_operations.require(operation_id)
        emission = default_cpp_emissions.require(operation_id)
        expected = (
            FunctionEmission if declared.form is OperationForm.CONTAINER else CallEmission
        )
        assert isinstance(emission, expected), operation_id


def test_the_generator_spells_every_call_the_way_the_analyzer_reads_it():
    # The two C++-aware tables in this package are inverses, and this is what
    # keeps them from drifting: neither module imports the other, exactly as
    # the IR and the block catalog agree by test rather than by dependency.
    from app.build.semantic.analyzer import _CALL_OPERATIONS

    for call_name, operation_id in _CALL_OPERATIONS.items():
        emission = default_cpp_emissions.require(operation_id)
        assert isinstance(emission, CallEmission)
        assert emission.call_name == call_name


def test_a_missing_emission_is_a_clean_miss_rather_than_a_guess():
    assert default_cpp_emissions.emission("gpio.analog_write") is None
    with pytest.raises(UnsupportedOperationError):
        default_cpp_emissions.require("gpio.analog_write")


def test_the_emission_table_refuses_a_malformed_row():
    with pytest.raises(SemanticModelError):
        CppEmissionTable({"not an operation id": CallEmission("delay")})
    with pytest.raises(SemanticModelError):
        CppEmissionTable({TIME_DELAY: "delay"})


@pytest.mark.parametrize("name", ["", "delay(", "client.loop", "2delay", "de lay", "delay;"])
def test_an_emission_refuses_anything_that_is_not_a_plain_identifier(name):
    with pytest.raises(SemanticModelError):
        CallEmission(call_name=name)


def test_a_function_emission_writes_its_declarator():
    assert FunctionEmission(return_type="void", name="setup").signature == "void setup()"


def test_the_default_table_is_rebuilt_identically():
    fresh = build_default_emissions()
    assert fresh.operation_ids == default_cpp_emissions.operation_ids
    for operation_id in fresh.operation_ids:
        assert fresh.require(operation_id) == default_cpp_emissions.require(operation_id)


# =============================================================================
# 9. Round trips — the whole pipeline, on real project source
# =============================================================================


def test_the_default_build_mode_program_is_reconstructed_byte_for_byte():
    assert generated(BLINK_PROGRAM) == BLINK_PROGRAM


def test_the_default_build_mode_program_survives_the_full_blockly_round_trip():
    program = semantic(BLINK_PROGRAM)
    through_blockly = blockly_to_semantic(program_to_blockly(program))
    assert generate_cpp(through_blockly) == BLINK_PROGRAM


def test_regenerated_source_re_analyzes_to_an_equal_semantic_program():
    for source in (BLINK_PROGRAM, panel_one_source()):
        program = semantic(source)
        assert semantic(generate_cpp(program)) == program


def test_panel_one_survives_semantic_to_blockly_to_semantic_to_c_plus_plus():
    program = semantic(panel_one_source())
    through_blockly = blockly_to_semantic(program_to_blockly(program))
    assert generate_cpp(through_blockly) == generate_cpp(program)


# =============================================================================
# 10. The real Panel 1 firmware as a regression fixture
# =============================================================================


def test_panel_one_keeps_its_supported_statements_as_real_calls():
    written = generated(panel_one_source())
    for pin, mode in (
        ("START_BUTTON", "INPUT_PULLUP"),
        ("STOP_BUTTON", "INPUT_PULLUP"),
        ("MOTOR_IN1", "OUTPUT"),
        ("MOTOR_IN2", "OUTPUT"),
        ("GREEN_LED", "OUTPUT"),
        ("RED_LED", "OUTPUT"),
        ("BUZZER", "OUTPUT"),
    ):
        assert f"pinMode({pin}, {mode});" in written


def test_panel_one_keeps_every_line_of_its_wifi_and_mqtt_configuration():
    written = generated(panel_one_source())
    for fragment in (
        "#include <WiFi.h>",
        "#include <PubSubClient.h>",
        'static const char *WIFI_SSID = "CyberTrainer";',
        'static const char *MQTT_BROKER = "192.168.50.1";',
        "static const uint16_t MQTT_PORT = 1883;",
        'static const char *MQTT_USERNAME = "panel1-device";',
        'static const char *COMMAND_TOPIC = "cybertrainer/smart-home/motor/control";',
        "WiFiClient espClient;",
        "PubSubClient client(espClient);",
        "WiFi.begin(WIFI_SSID, WIFI_PASSWORD);",
        "client.setServer(MQTT_BROKER, MQTT_PORT);",
        "client.setCallback(onMessage);",
    ):
        assert fragment in written, fragment


def test_panel_one_keeps_its_callback_and_its_vulnerable_handler_verbatim():
    written = generated(panel_one_source())
    source = panel_one_source()
    for section_id in ("callback_onMessage", "helper_applyCommand", "helper_setMotorOutputs"):
        fragment = semantic(source).section(section_id).statements[0].source_text
        assert fragment in written, section_id


def test_panel_one_keeps_the_comments_that_teach_the_vulnerability():
    written = generated(panel_one_source())
    for fragment in (
        "THE WEAKNESS IS MISSING AUTHORIZATION FOR CRITICAL MQTT COMMANDS.",
        "THE VULNERABLE HANDLER.",
        "LIVE LAB CREDENTIALS, COMMITTED ON PURPOSE.",
    ):
        assert fragment in written, fragment


def test_panel_one_loses_no_non_whitespace_character_of_its_source():
    # The strongest statement this suite can make without a compiler: every
    # meaningful character of the original firmware is still there, in order.
    source = panel_one_source()
    written = generated(source)
    assert "".join(source.split()) == "".join(written.split())


def test_panel_one_keeps_its_setup_and_loop_bodies_in_order():
    program = semantic(panel_one_source())
    written = generated(panel_one_source())
    for section in (program.setup, program.loop):
        cursor = 0
        for item in section.statements:
            first_line = item.source_text.splitlines()[0]
            found = written.find(first_line, cursor)
            assert found != -1, first_line
            cursor = found


# =============================================================================
# 11. Compile-safety, without a compiler — B7 owns arduino-cli
# =============================================================================


def test_generated_panel_one_source_is_structurally_balanced_c_plus_plus():
    # Braces, parens and brackets are counted on the discovery layer's own code
    # mask, so a brace inside a string or a comment cannot register. No process
    # is spawned: real compilation is B7's.
    masked = code_mask(generated(panel_one_source()))
    for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
        depth = 0
        for character in masked:
            if character == opener:
                depth += 1
            elif character == closer:
                depth -= 1
            assert depth >= 0, closer
        assert depth == 0, opener


def test_generated_source_still_declares_a_setup_and_a_loop_arduino_can_find():
    document = analyze_source(generated(panel_one_source()))
    assert document.setup is not None
    assert document.loop is not None
    assert document.setup.signature == "void setup()"
    assert document.loop.signature == "void loop()"


def test_generated_source_ends_in_a_newline_like_a_real_file():
    assert generated(panel_one_source()).endswith("\n")
    assert not generated(panel_one_source()).endswith("\n\n")


# =============================================================================
# 12. Dependency boundary — B6 adds no dependency of any kind
# =============================================================================


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_generator_imports_nothing_outside_its_own_package():
    for path in _B6_MODULES:
        offenders = sorted(
            name
            for name in _imports(path)
            if name.startswith("app.") and not name.startswith("app.build.semantic")
        )
        assert offenders == [], f"{path.name} reaches outside the IR: {offenders}"


def test_the_generator_touches_no_filesystem_process_or_toolchain():
    banned = (
        "subprocess",
        "os",
        "pathlib",
        "shutil",
        "tempfile",
        "socket",
        "importlib",
        "asyncio",
        "json",
    )
    for path in _B6_MODULES:
        offenders = sorted(
            name
            for name in _imports(path)
            for bad in banned
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_generator_uses_no_dynamic_execution():
    banned_names = {"eval", "exec", "compile", "__import__", "open"}
    banned_attrs = {"system", "popen", "Popen", "run", "spawnv", "spawn"}
    for path in _B6_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id not in banned_names, f"{path.name} calls {node.func.id}"
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned_attrs, f"{path.name} calls .{node.func.attr}"


def test_the_generator_names_no_panel_and_no_blockly_type():
    forbidden = {
        "smart-home-mqtt-control",
        "cybertrainer/smart-home/motor/control",
        "192.168.50.1",
        "onMessage",
        "applyCommand",
        "setMotorOutputs",
        "MQTT",
        "arduino_setup",
        "arduino_loop",
        "digitalwrite",
        "pinmode",
        "blockly",
        "Blockly",
        "arduino-cli",
    }
    for path in _B6_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in forbidden
            }
        )
        assert offenders == [], f"{path.name} names forbidden literal(s): {offenders}"


def test_b6_declares_no_compile_flash_or_validation_entry_point():
    # B7 owns compile/flash/validation. B6 is a string function, and nothing
    # here is a seam toward a toolchain.
    for path in _B6_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                for forbidden in ("flash", "upload", "validate", "arduino"):
                    assert forbidden not in lowered, node.name
            assert not isinstance(node, ast.AsyncFunctionDef), "the generator is synchronous"
