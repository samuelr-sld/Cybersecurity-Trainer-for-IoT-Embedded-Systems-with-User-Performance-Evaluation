"""Phase B3 — the semantic representation layer (`app/build/semantic/`).

Covers the IR in isolation: its model's own validation, its operation
vocabulary, and `analyze_document` turning a B1 `BuildDocument` into a
`SemanticProgram`. Nothing here touches a `BuildSession`, `BuildWorkspace`,
`BuildProject`, `FileSegment`, a panel package, Blockly or hardware — the
layer is exercised against plain source strings, the real committed Panel 1
firmware read off disk, and its own model classes.

The two things this suite exists to pin down, beyond ordinary behaviour:

  * the IR is BLOCKLY-INDEPENDENT (static import checks below), while still
    reusing the block catalog's `semantic_operation` vocabulary verbatim —
    the conformance section proves the two agree without either importing
    the other, the same way `tests/test_panel_packages.py` proves a
    `WorkflowStep.command` names a real tool;
  * UNSUPPORTED C++ IS REPRESENTED, NOT LOST OR FAILED ON, and the original
    `CodeSection` source survives analysis untouched.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.build.discovery import BuildDocument, CodeSection, SectionKind, analyze_source
from app.build.semantic import (
    FUNCTIONS_IMPLEMENTATION,
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TIME_DELAY,
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    OperationForm,
    OperationStatement,
    SemanticAnalysisError,
    SemanticArgument,
    SemanticError,
    SemanticModelError,
    SemanticOperation,
    SemanticOperationRegistry,
    SemanticParameter,
    SemanticProgram,
    SemanticSection,
    SemanticType,
    SymbolValue,
    UnknownOperationError,
    UnsupportedReason,
    UnsupportedStatement,
    analyze_document,
    build_default_operations,
    default_semantic_operations,
)
from app.build.semantic.operations import QUALIFIED_OPERATION_PATTERN

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
SEMANTIC_DIR = APP_DIR / "build" / "semantic"
PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parents[1]
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)

BLINK_SOURCE = (
    "const int LED = 2;\n"
    "\n"
    "void setup() {\n"
    "  pinMode(LED, OUTPUT);\n"
    "}\n"
    "\n"
    "void loop() {\n"
    "  digitalWrite(LED, HIGH);\n"
    "  delay(1000);\n"
    "  digitalWrite(LED, LOW);\n"
    "  delay(1000);\n"
    "}\n"
)


def analyze(source: str) -> SemanticProgram:
    """Source text all the way through both layers — B1 then B3."""
    return analyze_document(analyze_source(source))


# =============================================================================
# 1. Semantic model validation
# =============================================================================


def test_operation_rejects_an_unqualified_id():
    with pytest.raises(SemanticModelError):
        SemanticOperation(operation_id="pinmode", form=OperationForm.STATEMENT)


def test_operation_rejects_duplicate_parameter_names():
    with pytest.raises(SemanticModelError):
        SemanticOperation(
            operation_id="gpio.example",
            form=OperationForm.STATEMENT,
            parameters=(
                SemanticParameter("PIN", SemanticType.PIN),
                SemanticParameter("PIN", SemanticType.NUMBER),
            ),
        )


def test_a_container_operation_cannot_declare_parameters():
    with pytest.raises(SemanticModelError):
        SemanticOperation(
            operation_id="program.example",
            form=OperationForm.CONTAINER,
            parameters=(SemanticParameter("DO", SemanticType.NUMBER),),
        )


def test_parameter_rejects_a_lowercase_name():
    with pytest.raises(SemanticModelError):
        SemanticParameter("pin", SemanticType.PIN)


def test_literal_value_rejects_a_value_that_contradicts_its_type():
    with pytest.raises(SemanticModelError):
        LiteralValue(value="OUTPUT", value_type=SemanticType.NUMBER)
    with pytest.raises(SemanticModelError):
        LiteralValue(value=1, value_type=SemanticType.BOOLEAN)


def test_a_boolean_literal_is_not_accepted_as_a_number():
    # `isinstance(True, int)` is True in Python; the model must not inherit
    # that conflation from the language.
    with pytest.raises(SemanticModelError):
        LiteralValue(value=True, value_type=SemanticType.NUMBER)


def test_symbol_value_rejects_an_empty_name():
    with pytest.raises(SemanticModelError):
        SymbolValue(name="  ")


def test_statement_rejects_arguments_that_do_not_match_its_parameters():
    operation = default_semantic_operations.require(TIME_DELAY)
    with pytest.raises(SemanticModelError):
        OperationStatement(
            operation=operation,
            arguments=(SemanticArgument("PIN", LiteralValue(2, SemanticType.NUMBER)),),
            text="delay(2);",
        )


def test_statement_rejects_a_missing_argument():
    operation = default_semantic_operations.require(GPIO_PIN_MODE)
    with pytest.raises(SemanticModelError):
        OperationStatement(
            operation=operation,
            arguments=(SemanticArgument("PIN", SymbolValue("LED")),),
            text="pinMode(LED);",
        )


def test_statement_rejects_a_literal_of_the_wrong_type_for_its_parameter():
    operation = default_semantic_operations.require(TIME_DELAY)
    with pytest.raises(SemanticModelError):
        OperationStatement(
            operation=operation,
            arguments=(SemanticArgument("MS", LiteralValue("soon", SemanticType.TEXT)),),
            text='delay("soon");',
        )


def test_a_container_operation_cannot_be_a_statement_in_a_body():
    operation = default_semantic_operations.require(PROGRAM_SETUP)
    with pytest.raises(SemanticModelError):
        OperationStatement(operation=operation, arguments=(), text="setup();")


def test_a_statement_operation_cannot_be_a_section_container():
    operation = default_semantic_operations.require(TIME_DELAY)
    with pytest.raises(SemanticModelError):
        SemanticSection(section_id="setup", operation=operation, statements=())


def test_a_section_without_a_container_operation_cannot_hold_understood_statements():
    operation = default_semantic_operations.require(TIME_DELAY)
    statement = OperationStatement(
        operation=operation,
        arguments=(SemanticArgument("MS", LiteralValue(10, SemanticType.NUMBER)),),
        text="delay(10);",
    )
    with pytest.raises(SemanticModelError):
        SemanticSection(section_id="helper_x", operation=None, statements=(statement,))


def test_program_rejects_duplicate_section_ids():
    section = SemanticSection(section_id="setup", operation=None, statements=())
    with pytest.raises(SemanticModelError):
        SemanticProgram(sections=(section, section))


def test_unsupported_statement_requires_text_and_a_known_reason():
    with pytest.raises(SemanticModelError):
        UnsupportedStatement(text="   ", reason=UnsupportedReason.NOT_A_CALL)
    with pytest.raises(SemanticModelError):
        UnsupportedStatement(text="x();", reason="nope")


def test_all_semantic_errors_share_one_base_class():
    for error in (SemanticModelError, UnknownOperationError, SemanticAnalysisError):
        assert issubclass(error, SemanticError)
        assert issubclass(error, ValueError)


# =============================================================================
# 2. Stable operation identity
# =============================================================================


def test_the_default_registry_declares_exactly_the_supported_subset():
    assert set(default_semantic_operations.operation_ids) == {
        PROGRAM_SETUP,
        PROGRAM_LOOP,
        FUNCTIONS_IMPLEMENTATION,
        GPIO_PIN_MODE,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    }


def test_operation_ids_are_the_stable_namespaced_names():
    assert PROGRAM_SETUP == "program.setup"
    assert PROGRAM_LOOP == "program.loop"
    assert GPIO_PIN_MODE == "gpio.pin_mode"
    assert GPIO_DIGITAL_WRITE == "gpio.digital_write"
    assert TIME_DELAY == "time.delay"


def test_an_operation_id_is_never_a_cpp_fragment():
    # The C++ spelling lives only in the analyzer's call table; no operation
    # id may be the source-language name it happens to be written as.
    cpp_names = {"pinMode", "digitalWrite", "delay", "setup", "loop"}
    assert not (set(default_semantic_operations.operation_ids) & cpp_names)


def test_two_registry_builds_are_equal_in_identity_and_order():
    first = build_default_operations()
    second = build_default_operations()
    assert first.operation_ids == second.operation_ids
    assert first.operations == second.operations


def test_registry_rejects_a_duplicate_operation():
    operation = SemanticOperation(operation_id="time.delay", form=OperationForm.STATEMENT)
    with pytest.raises(SemanticModelError):
        SemanticOperationRegistry((operation, operation))


def test_an_unknown_operation_is_a_clean_miss_not_a_guess():
    assert default_semantic_operations.operation("mqtt.publish") is None
    assert "mqtt.publish" not in default_semantic_operations
    with pytest.raises(UnknownOperationError):
        default_semantic_operations.require("mqtt.publish")


def test_container_and_statement_forms_are_separated():
    containers = default_semantic_operations.with_form(OperationForm.CONTAINER)
    statements = default_semantic_operations.with_form(OperationForm.STATEMENT)
    assert {op.operation_id for op in containers} == {
        PROGRAM_SETUP,
        PROGRAM_LOOP,
        FUNCTIONS_IMPLEMENTATION,
    }
    assert {op.operation_id for op in statements} == {
        GPIO_PIN_MODE,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    }


def test_a_statement_carries_its_operations_stable_id():
    program = analyze(BLINK_SOURCE)
    statement = program.setup.operation_statements[0]
    assert statement.operation_id == GPIO_PIN_MODE
    assert statement.operation is default_semantic_operations.require(GPIO_PIN_MODE)


# =============================================================================
# 3. Ordered statement representation
# =============================================================================


def test_statements_keep_their_source_order():
    program = analyze(BLINK_SOURCE)
    assert [s.operation_id for s in program.loop.operation_statements] == [
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    ]


def test_order_is_preserved_across_supported_and_unsupported_statements():
    source = (
        "void setup() {\n"
        "  pinMode(2, OUTPUT);\n"
        "  Serial.begin(115200);\n"
        "  delay(10);\n"
        "  WiFi.begin(SSID);\n"
        "  digitalWrite(2, HIGH);\n"
        "}\n"
    )
    program = analyze(source)
    assert [
        s.operation_id if s.supported else s.reason.value for s in program.setup.statements
    ] == [
        GPIO_PIN_MODE,
        "not_a_call",
        TIME_DELAY,
        "not_a_call",
        GPIO_DIGITAL_WRITE,
    ]


def test_sections_keep_their_document_order_and_ids():
    # The ids and the order are B1's, reproduced exactly — B1 folds the blank
    # run preceding a function into that function's own section, so there is
    # no `global_2` between setup and loop, only the trailing newline after
    # loop's closing brace. The IR mirrors whatever B1 discovered rather than
    # re-deciding section boundaries.
    program = analyze(BLINK_SOURCE)
    assert [section.section_id for section in program.sections] == [
        "global",
        "setup",
        "loop",
        "global_2",
    ]


def test_argument_order_follows_the_operations_declared_parameters():
    program = analyze("void setup() {\n  pinMode(4, INPUT_PULLUP);\n}\n")
    statement = program.setup.operation_statements[0]
    assert [argument.name for argument in statement.arguments] == ["PIN", "MODE"]


# =============================================================================
# 4/5. setup and loop section representation
# =============================================================================


def test_setup_becomes_a_program_setup_container_section():
    program = analyze(BLINK_SOURCE)
    setup = program.setup
    assert setup is not None
    assert setup.section_id == "setup"
    assert setup.operation_id == PROGRAM_SETUP
    assert setup.operation.form is OperationForm.CONTAINER
    assert setup.supported is True


def test_loop_becomes_a_program_loop_container_section():
    program = analyze(BLINK_SOURCE)
    loop = program.loop
    assert loop is not None
    assert loop.section_id == "loop"
    assert loop.operation_id == PROGRAM_LOOP
    assert loop.operation.form is OperationForm.CONTAINER


def test_a_container_section_holds_its_body_as_statements_not_as_an_argument():
    program = analyze(BLINK_SOURCE)
    assert program.setup.operation.parameters == ()
    assert len(program.setup.statements) == 1


def test_an_empty_body_is_a_supported_section_with_no_statements():
    program = analyze("void setup() {\n}\n\nvoid loop() {\n}\n")
    assert program.setup.supported is True
    assert program.setup.statements == ()
    assert program.loop.statements == ()


def test_a_source_with_no_setup_or_loop_has_no_supported_section():
    program = analyze("static int counter = 0;\n")
    assert program.setup is None
    assert program.loop is None
    assert program.supported_sections == ()


def test_operations_used_reports_the_whole_program_deterministically():
    assert analyze(BLINK_SOURCE).operations_used == (
        GPIO_DIGITAL_WRITE,
        GPIO_PIN_MODE,
        PROGRAM_LOOP,
        PROGRAM_SETUP,
        TIME_DELAY,
    )


# =============================================================================
# 6. gpio.pin_mode representation
# =============================================================================


def test_pin_mode_with_a_literal_pin_becomes_a_typed_operation():
    program = analyze("void setup() {\n  pinMode(27, OUTPUT);\n}\n")
    statement = program.setup.operation_statements[0]
    assert statement.operation_id == GPIO_PIN_MODE
    assert statement.value("PIN") == LiteralValue(27, SemanticType.NUMBER)
    assert statement.value("MODE") == SymbolValue("OUTPUT")


def test_pin_mode_with_a_named_pin_constant_becomes_a_symbol():
    # The real Panel 1 firmware never writes a bare pin number; a
    # literal-only value model could not represent one line of it.
    program = analyze("void setup() {\n  pinMode(START_BUTTON, INPUT_PULLUP);\n}\n")
    statement = program.setup.operation_statements[0]
    assert statement.value("PIN") == SymbolValue("START_BUTTON")
    assert statement.value("MODE") == SymbolValue("INPUT_PULLUP")


def test_a_number_literal_is_accepted_in_a_pin_slot_but_not_the_reverse():
    assert LiteralValue(2, SemanticType.NUMBER).fits(SemanticType.PIN)
    assert not LiteralValue(2, SemanticType.PIN).fits(SemanticType.NUMBER)


def test_a_symbol_fits_every_parameter_type():
    symbol = SymbolValue("BUZZER_CHIRP_MS")
    assert all(symbol.fits(value_type) for value_type in SemanticType)


# =============================================================================
# 7. gpio.digital_write representation
# =============================================================================


def test_digital_write_records_pin_and_value():
    program = analyze("void loop() {\n  digitalWrite(GREEN_LED, HIGH);\n}\n")
    statement = program.loop.operation_statements[0]
    assert statement.operation_id == GPIO_DIGITAL_WRITE
    assert statement.value("PIN") == SymbolValue("GREEN_LED")
    assert statement.value("VALUE") == SymbolValue("HIGH")


def test_digital_write_accepts_a_boolean_literal_value():
    program = analyze("void loop() {\n  digitalWrite(2, true);\n}\n")
    statement = program.loop.operation_statements[0]
    assert statement.value("VALUE") == LiteralValue(True, SemanticType.BOOLEAN)


def test_digital_write_with_a_ternary_value_is_not_half_represented():
    # `setMotorOutputs` in the real Panel 1 firmware writes exactly this.
    program = analyze("void loop() {\n  digitalWrite(MOTOR_IN1, run ? HIGH : LOW);\n}\n")
    assert program.loop.operation_statements == ()
    (statement,) = program.loop.unsupported_statements
    assert statement.reason is UnsupportedReason.UNSUPPORTED_ARGUMENT
    assert statement.source_text == "digitalWrite(MOTOR_IN1, run ? HIGH : LOW);"


# =============================================================================
# 8. time.delay representation
# =============================================================================


def test_delay_with_a_literal_duration():
    program = analyze("void loop() {\n  delay(1000);\n}\n")
    statement = program.loop.operation_statements[0]
    assert statement.operation_id == TIME_DELAY
    assert statement.value("MS") == LiteralValue(1000, SemanticType.NUMBER)


def test_delay_with_a_named_constant_duration():
    program = analyze("void loop() {\n  delay(BUZZER_CHIRP_MS);\n}\n")
    assert program.loop.operation_statements[0].value("MS") == SymbolValue("BUZZER_CHIRP_MS")


def test_delay_with_the_wrong_argument_count_is_carried_verbatim():
    program = analyze("void loop() {\n  delay(10, 20);\n}\n")
    (statement,) = program.loop.unsupported_statements
    assert statement.reason is UnsupportedReason.ARGUMENT_COUNT


# =============================================================================
# 9. Unsupported construct behaviour
# =============================================================================


@pytest.mark.parametrize(
    ("statement_source", "reason"),
    [
        ("Serial.begin(115200);", UnsupportedReason.NOT_A_CALL),
        ("client.setCallback(onMessage);", UnsupportedReason.NOT_A_CALL),
        ("int counter = 0;", UnsupportedReason.NOT_A_CALL),
        ("motorRunning = true;", UnsupportedReason.NOT_A_CALL),
        ("if (ready) return;", UnsupportedReason.NOT_A_CALL),
        ("pinMode(2, OUTPUT)", UnsupportedReason.NOT_A_CALL),
        ("analogWrite(2, 128);", UnsupportedReason.UNKNOWN_CALL),
        ("digitalWrite(2);", UnsupportedReason.ARGUMENT_COUNT),
        ("delay(millis() + 5);", UnsupportedReason.UNSUPPORTED_ARGUMENT),
        ("pinMode(2 + 1, OUTPUT);", UnsupportedReason.UNSUPPORTED_ARGUMENT),
    ],
)
def test_unsupported_statements_keep_their_text_and_say_why(statement_source, reason):
    program = analyze(f"void loop() {{\n  {statement_source}\n}}\n")
    (statement,) = program.loop.statements
    assert isinstance(statement, UnsupportedStatement)
    assert statement.reason is reason
    assert statement.source_text == statement_source
    assert statement.supported is False


def test_a_zero_argument_call_to_an_unregistered_function_is_a_call_statement():
    # The no-device/Blockly-integration correction: a call to a function this
    # firmware defines itself (not a platform operation) is representable —
    # but only with no arguments; see `CallStatement`.
    program = analyze("void loop() {\n  ensureConnected();\n}\n")
    (statement,) = program.loop.statements
    assert isinstance(statement, CallStatement)
    assert statement.function_name == "ensureConnected"
    assert statement.source_text == "ensureConnected();"
    assert statement.supported is True


def test_a_call_to_an_unregistered_function_with_arguments_stays_unsupported():
    # Deliberately NOT widened: `CallStatement` is zero-argument only.
    program = analyze("void loop() {\n  setBrightness(128);\n}\n")
    (statement,) = program.loop.statements
    assert isinstance(statement, UnsupportedStatement)
    assert statement.reason is UnsupportedReason.UNKNOWN_CALL


def test_an_unsupported_construct_never_raises():
    # The whole point: a body of nothing but unrepresentable C++ analyzes
    # cleanly, it just means nothing.
    program = analyze(
        "void loop() {\n"
        "  String message = client.read();\n"
        "  message.trim();\n"
        "  publishState(message.c_str(), true);\n"
        "}\n"
    )
    assert program.loop.operation_statements == ()
    assert len(program.loop.unsupported_statements) == 3


def test_a_control_flow_block_is_carried_as_one_statement_not_fragments():
    source = (
        "void setup() {\n"
        "  while (WiFi.status() != WL_CONNECTED) {\n"
        "    delay(500);\n"
        "  }\n"
        "}\n"
    )
    program = analyze(source)
    (statement,) = program.setup.statements
    assert statement.reason is UnsupportedReason.NOT_A_CALL
    assert statement.source_text.startswith("while (WiFi.status()")
    assert statement.source_text.endswith("}")
    # A `delay` nested inside unsupported control flow is NOT lifted out as
    # an understood statement — it is part of a construct with no meaning.
    assert program.setup.operation_statements == ()


def test_a_for_loop_header_does_not_split_into_three_statements():
    program = analyze("void loop() {\n  for (int i = 0; i < 3; i++) { delay(1); }\n}\n")
    assert len(program.loop.statements) == 1


def test_an_initializer_list_is_one_statement_including_its_semicolon():
    program = analyze("void setup() {\n  int table[] = {1, 2, 3};\n}\n")
    (statement,) = program.setup.statements
    assert statement.source_text == "int table[] = {1, 2, 3};"


def test_a_semicolon_inside_a_string_literal_does_not_end_a_statement():
    program = analyze('void loop() {\n  Serial.println("a; b");\n}\n')
    (statement,) = program.loop.statements
    assert statement.source_text == 'Serial.println("a; b");'


def test_a_comment_between_statements_is_preserved_not_dropped():
    # CORRECTED: `code_mask` blanks every comment to whitespace, and
    # `_statement_spans` walks straight past whitespace — so without
    # `_body_statements` explicitly noticing the gap, a comment sitting
    # between two statements (or trailing after the last one) would open no
    # span, carry nothing, and simply vanish from a regenerated file. That
    # silent loss is what this test now pins the ABSENCE of.
    source = (
        "void setup() {\n"
        "  // configure the indicator\n"
        "  pinMode(2, OUTPUT);  // drive it\n"
        "  /* and wait */\n"
        "  delay(5);\n"
        "}\n"
    )
    program = analyze(source)
    assert [s.operation_id for s in program.setup.operation_statements] == [
        GPIO_PIN_MODE,
        TIME_DELAY,
    ]
    # Two recognized statements plus two comment gaps: one before pinMode,
    # one (merging the inline "// drive it" and the block comment) before
    # delay.
    assert len(program.setup.statements) == 4
    comments = [s for s in program.setup.statements if isinstance(s, UnsupportedStatement)]
    assert len(comments) == 2
    assert comments[0].source_text == "// configure the indicator"
    assert "// drive it" in comments[1].source_text
    assert "/* and wait */" in comments[1].source_text
    for comment in comments:
        assert comment.reason is UnsupportedReason.NOT_A_CALL


def test_a_call_that_is_supported_only_because_the_registry_declares_it():
    # Narrow the table and the same source stops being understood — the
    # recognizer is registry-driven, not hardcoded to three call names.
    narrow = SemanticOperationRegistry(
        (default_semantic_operations.require(PROGRAM_LOOP),)
    )
    program = analyze_document(analyze_source("void loop() {\n  delay(5);\n}\n"), operations=narrow)
    (statement,) = program.loop.statements
    assert statement.reason is UnsupportedReason.UNKNOWN_CALL


def test_a_section_kind_without_a_container_operation_is_whole_and_uninterpreted():
    narrow = SemanticOperationRegistry(
        (default_semantic_operations.require(TIME_DELAY),)
    )
    program = analyze_document(analyze_source("void loop() {\n  delay(5);\n}\n"), operations=narrow)
    section = program.section("loop")
    assert section.supported is False
    (statement,) = section.statements
    assert statement.reason is UnsupportedReason.UNSUPPORTED_SECTION
    assert "delay(5);" in statement.source_text


# =============================================================================
# 10. CodeSection -> semantic representation
# =============================================================================


def test_every_code_section_produces_exactly_one_semantic_section_in_order():
    document = analyze_source(PANEL_ONE_INO.read_text(encoding="utf-8"))
    program = analyze_document(document)
    assert [section.section_id for section in program.sections] == [
        section.section_id for section in document.sections
    ]


def test_a_semantic_section_references_its_code_section_by_id_only():
    program = analyze(BLINK_SOURCE)
    setup = program.setup
    for field in ("start_offset", "end_offset", "kind", "text"):
        assert not hasattr(setup, field), f"the IR must not carry a CodeSection's {field}"
    # `signature` DOES exist (the no-device/Blockly-integration correction) —
    # but it is None for `program.setup`/`program.loop`, whose declarator is a
    # fixed `emissions.py` row, never a `CodeSection`'s own text carried
    # through. Only a `functions.implementation` container (see below) sets it.
    assert setup.signature is None


def test_helper_functions_and_callbacks_are_representable_containers():
    # CORRECTED: HELPER_FUNCTION and CALLBACK sections used to have no
    # container operation at all. They do now (`functions.implementation`,
    # see `_SECTION_OPERATIONS` in analyzer.py) — only a run of
    # GLOBAL_DECLARATIONS text still has none, because it names no single
    # construct a container could represent.
    source = (
        "#include <WiFi.h>\n"
        "\n"
        "static void helper() {\n"
        "  delay(1);\n"
        "}\n"
        "\n"
        "static void onMessage(char *topic) {\n"
        "  helper();\n"
        "}\n"
        "\n"
        "void setup() {\n"
        "  client.setCallback(onMessage);\n"
        "}\n"
    )
    document = analyze_source(source)
    program = analyze_document(document)
    for section in document.sections:
        semantic = program.section(section.section_id)
        if section.kind is SectionKind.GLOBAL_DECLARATIONS:
            assert semantic.supported is False
        else:
            assert semantic.supported is True

    helper_section = next(s for s in document.sections if s.kind is SectionKind.HELPER_FUNCTION)
    helper_semantic = program.section(helper_section.section_id)
    assert helper_semantic.operation_id == FUNCTIONS_IMPLEMENTATION
    assert helper_semantic.signature == "static void helper()"
    (delay_statement,) = helper_semantic.statements
    assert isinstance(delay_statement, OperationStatement)
    assert delay_statement.operation_id == TIME_DELAY

    callback_section = next(s for s in document.sections if s.kind is SectionKind.CALLBACK)
    callback_semantic = program.section(callback_section.section_id)
    assert callback_semantic.operation_id == FUNCTIONS_IMPLEMENTATION
    (call_statement,) = callback_semantic.statements
    assert isinstance(call_statement, CallStatement)
    assert call_statement.function_name == "helper"

    # `setup()`'s own `client.setCallback(onMessage);` is a method call on an
    # object — still not a construct this layer recognizes.
    (setup_statement,) = program.section("setup").statements
    assert isinstance(setup_statement, UnsupportedStatement)


def test_a_helper_functions_body_is_now_interpreted():
    # CORRECTED: `delay(1)` inside a helper is real, supported C++, and the
    # helper now HAS a container operation (`functions.implementation`), so
    # its body is split into statements and recognized exactly like
    # `program.setup`'s — the whole point of the correction.
    program = analyze("static void helper() {\n  delay(1);\n}\n")
    section = program.section("helper_helper")
    assert section.supported is True
    assert section.signature == "static void helper()"
    (statement,) = section.statements
    assert isinstance(statement, OperationStatement)
    assert statement.operation_id == TIME_DELAY


def test_a_whitespace_only_section_produces_no_statement_at_all():
    program = analyze(BLINK_SOURCE)
    assert program.section("global_2").statements == ()


def test_analysis_does_not_modify_the_document_or_its_sections():
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    document = analyze_source(source)
    before = tuple(document.sections)
    analyze_document(document)
    assert document.source == source
    assert document.sections == before
    assert document.render() == source


def test_the_original_source_survives_in_the_code_section_not_in_the_ir():
    source = "void setup() {\n  digitalWrite(LED, run ? HIGH : LOW);\n}\n"
    document = analyze_source(source)
    program = analyze_document(document)
    # The IR could not represent that statement at all; the source is still
    # there, in full, in the layer that owns it.
    assert program.setup.operation_statements == ()
    assert document.section("setup").text == source.rstrip("\n")
    assert document.render() == source


def test_analyze_document_rejects_something_that_is_not_a_build_document():
    with pytest.raises(TypeError):
        analyze_document("void setup() {}")


def test_a_function_section_with_no_body_braces_is_a_genuine_error():
    # Unreachable from the real analyzer; pinned so a hand-built or future
    # document that breaks the structural contract fails loudly.
    broken = BuildDocument(
        source="void setup();\n",
        sections=(
            CodeSection(
                section_id="setup",
                kind=SectionKind.SETUP,
                name="setup",
                text="void setup();\n",
                start_offset=0,
                end_offset=14,
                signature="void setup()",
            ),
        ),
    )
    with pytest.raises(SemanticAnalysisError):
        analyze_document(broken)


# =============================================================================
# 11/12. Dependency boundaries — no Blockly, no session, no panel
# =============================================================================

_SEMANTIC_MODULES = (
    SEMANTIC_DIR / "__init__.py",
    SEMANTIC_DIR / "errors.py",
    SEMANTIC_DIR / "operations.py",
    SEMANTIC_DIR / "models.py",
    SEMANTIC_DIR / "analyzer.py",
    # B6's two modules live in this package and are held to the same
    # boundaries: a generator that could reach a filesystem, a session or a
    # toolchain would be the beginning of a compiler, which B7 owns.
    SEMANTIC_DIR / "emissions.py",
    SEMANTIC_DIR / "generator.py",
)

#: Everything the semantic layer must not import. Blockly heads the list:
#: B4 makes Blockly an ADAPTER over this IR, which only works while the
#: dependency points that way. The build-session/workspace/project and panel
#: layers follow for the reason B1's own boundary test gives — a
#: representation layer that knows about permissions, sessions or a specific
#: panel is no longer reusable by the next one.
_FORBIDDEN_IMPORT_PREFIXES = (
    "app.blockly",
    "app.build.models",
    "app.build.workspace",
    "app.build.service",
    "app.build.compiler",
    "app.build.flasher",
    "app.build.process",
    "app.build.blink",
    "app.build.environmental",
    "app.build.document_project",
    "app.build.sketch_source",
    "app.build_sessions",
    "app.build_websocket",
    "app.build_project_selection",
    "app.panels",
    "app.hardware",
    "app.scenarios",
    "app.commands",
    "app.events",
    "app.sessions",
    "app.metrics",
    "app.websocket",
    "app.main",
    "app.config",
)

#: Panel-specific and Blockly-specific literals that must never appear in the
#: semantic layer: it is a generic representation of Arduino C++, not a
#: description of one panel's firmware or of one editor's block types.
_FORBIDDEN_LITERALS = {
    "smart-home-mqtt-control",
    "20:9b:a9:88:0b:e4",
    "cybertrainer/smart-home/motor/control",
    "192.168.50.1",
    "onMessage",
    "applyCommand",
    "setMotorOutputs",
    "mosquitto",
    "MQTT",
    "arduino_setup",
    "arduino_loop",
    "digitalwrite",
    "pinmode",
    "blockly",
    "Blockly",
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_semantic_layer_imports_no_forbidden_app_layer():
    for path in _SEMANTIC_MODULES:
        offenders = sorted(
            name
            for name in _imports(path)
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
            if name == prefix or name.startswith(prefix + ".")
        )
        assert offenders == [], f"{path.name} imports forbidden layer(s): {offenders}"


def test_the_semantic_layer_imports_only_discovery_from_app():
    for path in _SEMANTIC_MODULES:
        app_imports = {name for name in _imports(path) if name.startswith("app.")}
        offenders = sorted(
            name
            for name in app_imports
            if not (
                name == "app.build.discovery"
                or name.startswith("app.build.discovery.")
                or name.startswith("app.build.semantic")
            )
        )
        assert offenders == [], f"{path.name} reaches outside discovery: {offenders}"


def test_the_semantic_layer_touches_no_filesystem_or_process():
    for path in _SEMANTIC_MODULES:
        banned = ("subprocess", "os", "pathlib", "shutil", "tempfile", "socket", "importlib")
        offenders = sorted(
            name
            for name in _imports(path)
            for bad in banned
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_semantic_layer_uses_no_dynamic_execution():
    banned_names = {"eval", "exec", "compile", "__import__"}
    banned_attrs = {"system", "popen", "Popen", "run", "spawnv", "spawn"}
    for path in _SEMANTIC_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in banned_names, f"{path.name} calls {func.id}"
            if isinstance(func, ast.Attribute):
                assert func.attr not in banned_attrs, f"{path.name} calls .{func.attr}"


def test_the_semantic_layer_names_no_panel_or_blockly_literal():
    for path in _SEMANTIC_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _FORBIDDEN_LITERALS
            }
        )
        assert offenders == [], f"{path.name} names forbidden literal(s): {offenders}"


def test_blockly_does_not_import_the_semantic_layer_either():
    # The reuse is by shared identifier, not by dependency, in BOTH
    # directions — see `app/build/semantic/operations.py`.
    blockly_dir = APP_DIR / "blockly"
    for path in sorted(blockly_dir.rglob("*.py")):
        offenders = sorted(
            name for name in _imports(path) if name.startswith("app.build")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


# =============================================================================
# Block-catalog vocabulary conformance (reuse without a dependency)
# =============================================================================


def test_the_operation_id_pattern_matches_the_catalogs():
    from app.blockly.models import QUALIFIED_ID_PATTERN

    assert QUALIFIED_OPERATION_PATTERN == QUALIFIED_ID_PATTERN


def test_every_semantic_type_matches_a_catalog_value_type():
    from app.blockly.models import ValueType

    catalog_values = {value_type.value for value_type in ValueType}
    for semantic_type in SemanticType:
        assert semantic_type.value in catalog_values, semantic_type


def test_every_ir_operation_is_named_by_an_implemented_catalog_block():
    from app.blockly.catalog import default_block_catalog
    from app.blockly.models import ImplementationStatus

    implemented = {
        block.semantic_operation
        for block in default_block_catalog.with_status(ImplementationStatus.IMPLEMENTED)
    }
    for operation_id in default_semantic_operations.operation_ids:
        assert operation_id in implemented, (
            f"{operation_id} has no IMPLEMENTED block behind it — the IR must not "
            "claim a capability the platform does not have"
        )


def test_every_ir_operation_agrees_with_a_catalog_block_on_its_parameters():
    from app.blockly.catalog import default_block_catalog
    from app.blockly.models import ValueType

    for operation in default_semantic_operations.operations:
        candidates = [
            block
            for block in default_block_catalog.blocks
            if block.semantic_operation == operation.operation_id
        ]
        assert candidates, operation.operation_id
        # A container's body is structural in the IR (it is the section's
        # statements), so the catalog's STATEMENTS-typed inputs are excluded
        # from the comparison — see `operations.py`.
        expected = tuple(
            (parameter.name, parameter.value_type.value) for parameter in operation.parameters
        )
        shapes = [
            tuple(
                (item.name, item.value_type.value)
                for item in block.inputs
                if item.value_type is not ValueType.STATEMENTS
            )
            for block in candidates
        ]
        assert expected in shapes, (
            f"{operation.operation_id}: IR parameters {expected} match no catalog block "
            f"among {shapes}"
        )


def test_the_ir_declares_no_operation_the_catalog_does_not_name():
    from app.blockly.catalog import default_block_catalog

    known = {block.semantic_operation for block in default_block_catalog.blocks}
    unknown = sorted(set(default_semantic_operations.operation_ids) - known)
    assert unknown == [], f"IR operations absent from the block catalog: {unknown}"


# =============================================================================
# 13. Determinism, and the real Panel 1 firmware as an integration fixture
# =============================================================================


def test_analysis_is_deterministic_for_the_real_panel_one_firmware():
    document = analyze_source(PANEL_ONE_INO.read_text(encoding="utf-8"))
    assert analyze_document(document) == analyze_document(document)


def test_analysis_is_deterministic_across_two_independent_runs():
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    assert analyze(source) == analyze(source)


def test_panel_one_setup_recovers_its_real_pin_configuration():
    program = analyze(PANEL_ONE_INO.read_text(encoding="utf-8"))
    pins = [
        (s.value("PIN").name, s.value("MODE").name)
        for s in program.setup.operation_statements
        if s.operation_id == GPIO_PIN_MODE
    ]
    assert pins == [
        ("START_BUTTON", "INPUT_PULLUP"),
        ("STOP_BUTTON", "INPUT_PULLUP"),
        ("MOTOR_IN1", "OUTPUT"),
        ("MOTOR_IN2", "OUTPUT"),
        ("GREEN_LED", "OUTPUT"),
        ("RED_LED", "OUTPUT"),
        ("BUZZER", "OUTPUT"),
    ]


def test_panel_one_setup_mixes_supported_and_unsupported_statements():
    program = analyze(PANEL_ONE_INO.read_text(encoding="utf-8"))
    setup = program.setup

    # The seven pin_mode() calls remain the only statements the semantic
    # layer claims as recognized operations.
    assert len(setup.operation_statements) == 7
    assert {s.operation.operation_id for s in setup.operation_statements} == {
        "gpio.pin_mode"
    }

    # Everything else -- the serial diagnostics and the real Wi-Fi/MQTT
    # connect sequence this firmware performs -- is carried verbatim as
    # unsupported rather than dropped, approximated, or misclassified as
    # understood. Checked by content rather than by a raw count, since that
    # count is free to change as the connect/diagnostic sequence does without
    # affecting what this test actually cares about: the supported/
    # unsupported split itself.
    unsupported_texts = {s.source_text for s in setup.unsupported_statements}
    for expected in (
        "Serial.begin(115200);",
        "setMotorOutputs(false);",
        "WiFi.begin(WIFI_SSID, WIFI_PASSWORD);",
        "client.setServer(MQTT_BROKER, MQTT_PORT);",
        "client.setCallback(onMessage);",
    ):
        assert expected in unsupported_texts

    # No statement is claimed twice or silently dropped between the two
    # buckets.
    assert len(setup.statements) == len(setup.operation_statements) + len(
        setup.unsupported_statements
    )


def test_panel_one_loop_mixes_call_statements_and_one_unsupported_method():
    # CORRECTED: `ensureConnected()` and `pollButtons()` are zero-argument
    # calls to functions this firmware defines itself — both are
    # `CallStatement`s now. `client.loop();` is a method call on an object and
    # stays unsupported; no `OperationStatement` is claimed either way.
    program = analyze(PANEL_ONE_INO.read_text(encoding="utf-8"))
    loop = program.loop
    assert loop.supported is True
    assert loop.operation_statements == ()
    calls = [s for s in loop.statements if isinstance(s, CallStatement)]
    assert [s.function_name for s in calls] == ["ensureConnected", "pollButtons"]
    assert [s.source_text for s in loop.unsupported_statements] == ["client.loop();"]


def test_panel_one_mqtt_implementation_is_never_semantically_claimed():
    # CORRECTED: the callback and the vulnerable handler are now
    # representable CONTAINERS (`functions.implementation` — the no-device/
    # Blockly-integration correction), but their MQTT-specific logic is still
    # never claimed as understood: no MQTT operation exists anywhere, and
    # `onMessage()`'s own statements — string/object method calls the
    # analyzer does not recognize — all stay carried-verbatim.
    program = analyze(PANEL_ONE_INO.read_text(encoding="utf-8"))
    on_message = program.section("callback_onMessage")
    assert on_message.supported is True
    assert on_message.operation_statements == ()
    assert all(isinstance(s, UnsupportedStatement) for s in on_message.statements)

    # applyCommand() IS partially understood now — its `if (message == "X")`
    # gate is exactly the construct the correction targets — but the MQTT
    # broker/topic logic surrounding it is not, and no such operation exists.
    apply_command = program.section("helper_applyCommand")
    assert apply_command.supported is True
    conditionals = [s for s in apply_command.statements if isinstance(s, ConditionalStatement)]
    assert len(conditionals) == 1
    assert isinstance(conditionals[0].condition, ComparisonValue)

    assert "mqtt.publish" not in program.operations_used
    assert "mqtt.subscribe" not in program.operations_used
