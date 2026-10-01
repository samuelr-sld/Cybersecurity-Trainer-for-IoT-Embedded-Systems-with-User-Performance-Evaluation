"""Phase B5 — the Blockly -> semantic IR adapter (`blockly_bridge/reverse.py`).

The direction B4 did not go. Everything here converts a workspace back into
the IR and nothing here produces C++ — `reverse.py` has no generator, no
source reconstruction and no compile path, which the static checks at the
bottom (and the ones B4's own suite already runs over every module of the
package) pin down.

The four things this suite exists to prove, beyond ordinary behaviour:

  * THE ROUND TRIP IS EXACT. `semantic -> Blockly -> semantic` returns an
    EQUAL `SemanticProgram` for the blink fixture and for the real committed
    Panel 1 firmware — same sections, same ids, same order, same operations,
    same values, same source text, same preserved fragments;
  * THE LOOKUP IS THE CATALOG'S, READ BACKWARDS. Rename a Blockly type or a
    body input in a private catalog and the reverse adapter follows it; no
    `"pinmode" -> "gpio.pin_mode"` mapping exists in the package;
  * NOTHING IS SILENTLY DROPPED OR REPAIRED. Preserved source comes back at
    its position with its exact text; a malformed block is reported rather
    than skipped, guessed at or half-built;
  * A FIELD'S TEXT IS READ AS WRITTEN. `START_BUTTON` stays a symbol and is
    never resolved to a pin number, `1000` stays a literal, and a token no
    field can hold is refused instead of coerced.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.blockly.categories import CATEGORIES
from app.blockly.catalog import BlockCatalog, default_block_catalog
from app.blockly.definitions.factory import BODY, CategoryFactory
from app.blockly.models import BlockDefinition, BlockKind, ImplementationStatus
from app.build.blockly_bridge import (
    BlocklyBlock,
    BlocklyBridgeError,
    BlocklyField,
    BlocklyProgram,
    BlocklySection,
    BridgeReason,
    FieldBinding,
    FieldKind,
    InvalidBlocklyFieldValueError,
    InvalidBlocklyWorkspaceError,
    MissingBlocklyFieldError,
    PreservedSource,
    UnknownBlocklyBlockError,
    UnrepresentableOperationError,
    UnsupportedBlocklyStructureError,
    block_definition_for,
    block_definition_for_type,
    blockly_to_semantic,
    program_to_blockly,
)
from app.build.discovery import analyze_source
from app.build.semantic import (
    FUNCTIONS_IMPLEMENTATION,
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TIME_DELAY,
    ConditionalStatement,
    ForStatement,
    LiteralValue,
    OperationStatement,
    SemanticProgram,
    SemanticType,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
    analyze_document,
)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
BRIDGE_DIR = BACKEND_DIR / "app" / "build" / "blockly_bridge"
REVERSE_MODULE = BRIDGE_DIR / "reverse.py"
PANEL_ONE_INO = (
    BACKEND_DIR
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


def semantic(source: str) -> SemanticProgram:
    """Source text through B1 and B3."""
    return analyze_document(analyze_source(source))


def round_trip(source: str) -> SemanticProgram:
    """Source text through B1, B3, B4 and back through B5."""
    return blockly_to_semantic(program_to_blockly(semantic(source)))


def panel_one_source() -> str:
    return PANEL_ONE_INO.read_text(encoding="utf-8")


def blockly_type_of(operation_id: str) -> str:
    """The Blockly type the real catalog draws an operation as."""
    return block_definition_for(operation_id, default_block_catalog).blockly_type


def body_input_of(operation_id: str) -> str:
    """The statement-input name the real catalog declares for a container."""
    definition = block_definition_for(operation_id, default_block_catalog)
    return next(item.name for item in definition.inputs if item.value_type.value == "statements")


def statement_block(
    operation_id: str, source_text: str, **fields: str
) -> BlocklyBlock:
    """A hand-built statement block, as an editor or a stored document has it."""
    definition = block_definition_for(operation_id, default_block_catalog)
    return BlocklyBlock(
        operation_id=operation_id,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=source_text,
        fields=tuple(BlocklyField(name=name, value=value) for name, value in fields.items()),
    )


def container_block(operation_id: str, *body, body_input: str | None = None) -> BlocklyBlock:
    definition = block_definition_for(operation_id, default_block_catalog)
    return BlocklyBlock(
        operation_id=operation_id,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        body_input=body_input or body_input_of(operation_id),
        body=tuple(body),
    )


def one_section_program(block: BlocklyBlock, section_id: str = "setup") -> BlocklyProgram:
    return BlocklyProgram(sections=(BlocklySection(section_id=section_id, block=block),))


def operations_in(program: SemanticProgram, section_id: str) -> list[str | None]:
    """Each statement of a section as its operation id, or None if unsupported."""
    section = program.section(section_id)
    return [
        statement.operation_id if isinstance(statement, OperationStatement) else None
        for statement in section.statements
    ]


# =============================================================================
# 17. The reverse lookup is the catalog's, not a table of this package's
# =============================================================================


@pytest.mark.parametrize(
    "blockly_type,operation_id",
    [
        ("arduino_setup", PROGRAM_SETUP),
        ("arduino_loop", PROGRAM_LOOP),
        ("pinmode", GPIO_PIN_MODE),
        ("digitalwrite", GPIO_DIGITAL_WRITE),
        ("delay", TIME_DELAY),
    ],
)
def test_every_blockly_type_resolves_back_through_the_catalog(blockly_type, operation_id):
    definition = block_definition_for_type(blockly_type, default_block_catalog)
    assert definition.blockly_type == blockly_type
    assert definition.semantic_operation == operation_id


def test_the_reverse_lookup_is_the_exact_inverse_of_the_forward_one():
    for operation_id in (
        PROGRAM_SETUP,
        PROGRAM_LOOP,
        GPIO_PIN_MODE,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    ):
        forward = block_definition_for(operation_id, default_block_catalog)
        assert block_definition_for_type(forward.blockly_type, default_block_catalog) is forward


def test_a_renamed_blockly_type_is_followed_rather_than_assumed():
    # The catalog owns the operation <-> Blockly type relation. Point one entry
    # at a different type and BOTH directions must move with it.
    factory = CategoryFactory("program")
    blocks = tuple(
        block for block in default_block_catalog.blocks if block.block_id != PROGRAM_SETUP
    ) + (
        factory.container(
            PROGRAM_SETUP,
            "setup",
            "Runs once at start-up.",
            (("DO", BODY),),
            implemented_as="start_hat",
        ),
    )
    catalog = BlockCatalog(CATEGORIES, blocks)
    program = program_to_blockly(semantic(BLINK_SOURCE), catalog=catalog)
    assert program.section("setup").block.block_type == "start_hat"
    assert blockly_to_semantic(program, catalog=catalog) == semantic(BLINK_SOURCE)
    # And the default catalog, which knows no such type, says so.
    with pytest.raises(UnknownBlocklyBlockError, match="start_hat"):
        blockly_to_semantic(program)


def test_a_block_the_catalog_has_not_implemented_is_not_read_back():
    # A BLOCKLY_DEFINED block can be dragged out but has no generator and no
    # IR operation behind it; reading a statement out of one would claim a
    # meaning nothing has defined.
    delay = block_definition_for(TIME_DELAY, default_block_catalog)
    unimplemented = BlockDefinition(
        block_id=delay.block_id,
        category_id=delay.category_id,
        display_name=delay.display_name,
        description=delay.description,
        kind=BlockKind.STATEMENT,
        semantic_operation=delay.semantic_operation,
        inputs=delay.inputs,
        status=ImplementationStatus.BLOCKLY_DEFINED,
        blockly_type=delay.blockly_type,
    )
    catalog = BlockCatalog(
        CATEGORIES,
        tuple(
            block for block in default_block_catalog.blocks if block.block_id != delay.block_id
        )
        + (unimplemented,),
    )
    program = one_section_program(
        container_block(PROGRAM_SETUP, statement_block(TIME_DELAY, "delay(5);", MS="5"))
    )
    with pytest.raises(UnknownBlocklyBlockError, match="blockly_defined"):
        blockly_to_semantic(program, catalog=catalog)


def test_a_catalog_block_the_ir_does_not_declare_is_a_loud_disagreement():
    # Drift between two tables, not a malformed workspace: the block is drawn
    # and implemented, but the IR declares no such operation.
    delay = block_definition_for(TIME_DELAY, default_block_catalog)
    drifted = BlockDefinition(
        block_id=delay.block_id,
        category_id=delay.category_id,
        display_name=delay.display_name,
        description=delay.description,
        kind=BlockKind.STATEMENT,
        semantic_operation="time.sleep_forever",
        inputs=delay.inputs,
        status=ImplementationStatus.IMPLEMENTED,
        blockly_type=delay.blockly_type,
        generator_id=delay.generator_id,
    )
    catalog = BlockCatalog(
        CATEGORIES,
        tuple(
            block for block in default_block_catalog.blocks if block.block_id != delay.block_id
        )
        + (drifted,),
    )
    program = one_section_program(
        container_block(PROGRAM_SETUP, statement_block(TIME_DELAY, "delay(5);", MS="5"))
    )
    with pytest.raises(UnrepresentableOperationError, match="time.sleep_forever"):
        blockly_to_semantic(program, catalog=catalog)


def test_the_reverse_adapter_states_no_blockly_type_of_its_own():
    # B4's suite makes this assertion over every module in the package; it is
    # restated here because it is the whole premise of B5's lookup.
    types = {"arduino_setup", "arduino_loop", "pinmode", "digitalwrite", "delay"}
    tree = ast.parse(REVERSE_MODULE.read_text(encoding="utf-8"))
    offenders = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in types
        }
    )
    assert offenders == []


def test_the_reverse_adapter_names_no_semantic_operation_of_its_own():
    operations = {PROGRAM_SETUP, PROGRAM_LOOP, GPIO_PIN_MODE, GPIO_DIGITAL_WRITE, TIME_DELAY}
    tree = ast.parse(REVERSE_MODULE.read_text(encoding="utf-8"))
    offenders = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in operations
        }
    )
    assert offenders == []


# =============================================================================
# 1/2/10. Containers and their bodies
# =============================================================================


def test_setup_comes_back_as_its_container_operation():
    section = round_trip(BLINK_SOURCE).section("setup")
    assert section.operation_id == PROGRAM_SETUP
    assert section.supported is True


def test_loop_comes_back_as_its_own_container_operation():
    assert round_trip(BLINK_SOURCE).section("loop").operation_id == PROGRAM_LOOP


def test_a_containers_body_becomes_its_sections_statements():
    assert operations_in(round_trip(BLINK_SOURCE), "loop") == [
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    ]


def test_an_empty_container_body_becomes_a_section_with_no_statements():
    program = round_trip("void setup() {\n}\n\nvoid loop() {\n}\n")
    assert program.section("setup").statements == ()
    assert program.section("loop").statements == ()


def test_the_body_input_name_is_read_from_the_catalog_not_from_a_constant():
    # The B4 suite proves the forward direction follows a renamed body input.
    # B5 must follow the same rename rather than looking for a fixed name.
    factory = CategoryFactory("program")
    blocks = tuple(
        block for block in default_block_catalog.blocks if block.block_id != PROGRAM_SETUP
    ) + (
        factory.container(
            PROGRAM_SETUP,
            "setup",
            "Runs once at start-up.",
            (("STACK", BODY),),
            implemented_as="arduino_setup",
        ),
    )
    catalog = BlockCatalog(CATEGORIES, blocks)
    program = program_to_blockly(semantic(BLINK_SOURCE), catalog=catalog)
    assert program.section("setup").block.body_input == "STACK"
    assert blockly_to_semantic(program, catalog=catalog) == semantic(BLINK_SOURCE)


def test_a_body_hanging_from_an_input_the_catalog_does_not_declare_is_reported():
    block = container_block(
        PROGRAM_SETUP,
        statement_block(TIME_DELAY, "delay(5);", MS="5"),
        body_input="STACK",
    )
    with pytest.raises(UnsupportedBlocklyStructureError, match="STACK"):
        blockly_to_semantic(one_section_program(block))


# =============================================================================
# 3/4/5/6/7. The statement operations and their values
# =============================================================================


def test_pin_mode_comes_back_with_both_operands_bound_by_name():
    statement = round_trip(BLINK_SOURCE).section("setup").statements[0]
    assert statement.operation_id == GPIO_PIN_MODE
    assert statement.value("PIN") == SymbolValue("LED")
    assert statement.value("MODE") == SymbolValue("OUTPUT")


def test_digital_write_comes_back_with_both_operands_bound_by_name():
    statement = round_trip(BLINK_SOURCE).section("loop").statements[0]
    assert statement.operation_id == GPIO_DIGITAL_WRITE
    assert statement.value("PIN") == SymbolValue("LED")
    assert statement.value("VALUE") == SymbolValue("HIGH")


def test_delay_comes_back_with_its_single_operand():
    statement = round_trip(BLINK_SOURCE).section("loop").statements[1]
    assert statement.operation_id == TIME_DELAY
    assert statement.value("MS") == LiteralValue(1000, SemanticType.NUMBER)


def test_a_literal_stays_the_literal_it_was_written_as():
    statement = round_trip("void setup() {\n  pinMode(2, OUTPUT);\n}\n").section(
        "setup"
    ).statements[0]
    value = statement.value("PIN")
    assert isinstance(value, LiteralValue)
    assert value == LiteralValue(2, SemanticType.NUMBER)


def test_a_symbol_stays_a_symbol_and_is_never_resolved_to_a_number():
    statement = round_trip("void setup() {\n  pinMode(START_PIN, OUTPUT);\n}\n").section(
        "setup"
    ).statements[0]
    value = statement.value("PIN")
    assert isinstance(value, SymbolValue)
    assert value.name == "START_PIN"


def test_a_symbol_and_a_literal_that_read_alike_stay_different_values():
    # `2` and a constant NAMED 2 would both read "2" in the field; only the
    # first is a literal, and the token's own form is what says so.
    binding = FieldBinding("PIN", FieldKind.TEXT)
    assert binding.parse("2") == LiteralValue(2, SemanticType.NUMBER)
    assert binding.parse("TWO") == SymbolValue("TWO")


def test_parsing_a_field_is_the_exact_inverse_of_rendering_it():
    cases = [
        (FieldBinding("PIN", FieldKind.TEXT), "START_BUTTON"),
        (FieldBinding("PIN", FieldKind.TEXT), "7"),
        (FieldBinding("MS", FieldKind.NUMBER), "1000"),
        (FieldBinding("MS", FieldKind.NUMBER), "-3"),
        (FieldBinding("MS", FieldKind.NUMBER), "2.5"),
        (FieldBinding("VALUE", FieldKind.DROPDOWN, ("HIGH", "LOW")), "HIGH"),
    ]
    for binding, text in cases:
        value = binding.parse(text)
        assert value is not None, text
        assert binding.render(value) == text


def test_a_numeric_field_refuses_a_name_the_way_it_refuses_to_render_one():
    binding = FieldBinding("MS", FieldKind.NUMBER)
    assert binding.parse("BUZZER_CHIRP_MS") is None
    assert binding.parse("true") is None


def test_a_dropdown_refuses_a_token_outside_its_options():
    binding = FieldBinding("VALUE", FieldKind.DROPDOWN, ("HIGH", "LOW"))
    assert binding.parse("PRESSED") is None
    assert binding.parse("LOW") == SymbolValue("LOW")


def test_a_field_holding_an_expression_is_refused_rather_than_guessed_at():
    for text in ("a + b", "ready ? HIGH : LOW", "digitalRead(2)", "  ", '"text"'):
        assert FieldBinding("PIN", FieldKind.TEXT).parse(text) is None


# =============================================================================
# 8/9. Field order and statement order
# =============================================================================


def test_fields_are_matched_by_name_so_their_order_cannot_swap_operands():
    definition = block_definition_for(GPIO_PIN_MODE, default_block_catalog)
    reversed_fields = BlocklyBlock(
        operation_id=GPIO_PIN_MODE,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text="pinMode(LED, OUTPUT);",
        fields=(BlocklyField("MODE", "OUTPUT"), BlocklyField("PIN", "LED")),
    )
    workspace = one_section_program(container_block(PROGRAM_SETUP, reversed_fields))
    program = blockly_to_semantic(workspace)
    statement = program.section("setup").statements[0]
    # The arguments come out in the OPERATION's declared order regardless.
    assert [argument.name for argument in statement.arguments] == ["PIN", "MODE"]
    assert statement.value("PIN") == SymbolValue("LED")
    assert statement.value("MODE") == SymbolValue("OUTPUT")


def test_statement_order_is_the_bodys_order():
    source = (
        "void setup() {\n"
        "  pinMode(LED, OUTPUT);\n"
        "  digitalWrite(LED, HIGH);\n"
        "  delay(5);\n"
        "}\n"
    )
    assert operations_in(round_trip(source), "setup") == [
        GPIO_PIN_MODE,
        GPIO_DIGITAL_WRITE,
        TIME_DELAY,
    ]


def test_order_survives_across_supported_and_preserved_items():
    source = (
        "void setup() {\n"
        "  pinMode(1, OUTPUT);\n"
        "  Serial.begin(115200);\n"
        "  pinMode(2, INPUT);\n"
        "}\n"
    )
    program = round_trip(source)
    assert operations_in(program, "setup") == [GPIO_PIN_MODE, None, GPIO_PIN_MODE]
    assert program.section("setup").statements[1].source_text == "Serial.begin(115200);"


def test_layout_coordinates_carry_no_meaning():
    # The workspace state positions top-level blocks deterministically; the
    # order of the SECTIONS is what says what comes first, and the reverse
    # adapter never reads an x or a y.
    program = program_to_blockly(semantic(BLINK_SOURCE))
    state = program.to_workspace_state()["blocks"]["blocks"]
    assert [block["y"] for block in state] == sorted(block["y"] for block in state)
    restored = [section.section_id for section in blockly_to_semantic(program).sections]
    assert restored == [section.section_id for section in semantic(BLINK_SOURCE).sections]
    assert restored.index("setup") < restored.index("loop")


# =============================================================================
# 11. Section ids
# =============================================================================


def test_section_ids_and_order_are_carried_through_untouched():
    source = panel_one_source()
    assert [section.section_id for section in round_trip(source).sections] == [
        section.section_id for section in semantic(source).sections
    ]


def test_a_section_id_is_never_regenerated_or_inferred_from_a_name():
    program = BlocklyProgram(
        sections=(
            BlocklySection(
                section_id="helper_applyMotorCommand",
                block=None,
                preserved=(
                    PreservedSource(
                        "void applyMotorCommand() {}",
                        UnsupportedReason.UNSUPPORTED_SECTION,
                    ),
                ),
            ),
        )
    )
    assert blockly_to_semantic(program).sections[0].section_id == "helper_applyMotorCommand"


# =============================================================================
# 12. Preserved source comes back, never dropped
# =============================================================================


def test_a_section_with_no_container_block_comes_back_unsupported():
    # CORRECTED FIXTURE: a HELPER_FUNCTION no longer demonstrates this — see
    # `functions.implementation`. GLOBAL_DECLARATIONS is the one kind that
    # still has no container form.
    program = round_trip("int counter = 0;\n\nvoid setup() {\n}\n")
    section = program.section("global")
    assert section.operation is None
    assert len(section.statements) == 1
    assert section.statements[0].reason is UnsupportedReason.UNSUPPORTED_SECTION
    assert "int counter = 0;" in section.statements[0].source_text


def test_b3s_own_reason_travels_back_unchanged():
    program = round_trip("void setup() {\n  ready = table[i];\n}\n")
    statement = program.section("setup").statements[0]
    assert isinstance(statement, UnsupportedStatement)
    assert statement.reason is UnsupportedReason.NOT_A_CALL
    assert statement.source_text == "ready = table[i];"


def test_a_control_flow_block_comes_back_whole_and_in_place():
    source = (
        "void loop() {\n"
        "  if (ready) {\n"
        "    digitalWrite(1, HIGH);\n"
        "  }\n"
        "  delay(5);\n"
        "}\n"
    )
    statements = round_trip(source).section("loop").statements
    assert statements[0].source_text.startswith("if (ready)")
    assert isinstance(statements[1], OperationStatement)


def test_source_b4_understood_but_could_not_draw_keeps_its_exact_text():
    # `delay(BLINK_MS);` was an OperationStatement B4 could not draw, so it
    # crossed as a PreservedSource with a BridgeReason. Coming back, all the
    # workspace holds is the text — so it returns as unsupported, under the
    # IR's nearest member, with the text untouched. This is the ONE place the
    # round trip narrows, and it narrows a reason, never a character of source.
    source = "void loop() {\n  ready = table[i];\n  digitalWrite(2, RUNNING);\n}\n"
    drawn = program_to_blockly(semantic(source))
    assert drawn.preserved[1].source.reason is BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE
    statements = blockly_to_semantic(drawn).section("loop").statements
    assert [statement.source_text for statement in statements] == [
        "ready = table[i];",
        "digitalWrite(2, RUNNING);",
    ]
    assert [statement.reason for statement in statements] == [
        UnsupportedReason.NOT_A_CALL,
        UnsupportedReason.UNSUPPORTED_ARGUMENT,
    ]


def test_no_preserved_fragment_is_lost_for_the_real_firmware():
    drawn = program_to_blockly(semantic(panel_one_source()))
    restored = blockly_to_semantic(drawn)

    def unsupported(statements):
        """Every carried-verbatim statement, nested ones in document order."""
        for statement in statements:
            if isinstance(statement, ConditionalStatement):
                yield from unsupported(statement.body)
                if statement.else_if is not None:
                    yield from unsupported((statement.else_if,))
                yield from unsupported(statement.else_body or ())
            elif isinstance(statement, ForStatement):
                yield from unsupported(statement.body)
            elif not statement.supported:
                yield statement.source_text

    kept = [
        text for section in restored.sections for text in unsupported(section.statements)
    ]
    assert [record.source.text for record in drawn.preserved] == kept


# =============================================================================
# The round trip itself
# =============================================================================


def test_the_blink_program_survives_the_round_trip_exactly():
    assert round_trip(BLINK_SOURCE) == semantic(BLINK_SOURCE)


def test_the_real_panel_one_firmware_survives_the_round_trip_exactly():
    # Every section, every statement. `delay(BUZZER_CHIRP_MS)` used to be the
    # one documented exception (a named constant in a numeric field); the
    # delay block's MS field now holds names, so there is none.
    source = panel_one_source()
    assert round_trip(source) == semantic(source)


def test_panel_ones_pin_modes_survive_with_their_named_pins_intact():
    statements = round_trip(panel_one_source()).section("setup").operation_statements
    assert [statement.value("PIN").source_text for statement in statements] == [
        "START_BUTTON",
        "STOP_BUTTON",
        "MOTOR_IN1",
        "MOTOR_IN2",
        "GREEN_LED",
        "RED_LED",
        "BUZZER",
    ]
    assert all(isinstance(statement.value("PIN"), SymbolValue) for statement in statements)


def test_panel_ones_mqtt_logic_is_still_never_claimed_as_understood():
    # CORRECTED: both are representable containers now
    # (`functions.implementation`, the no-device/Blockly-integration
    # correction), but neither claims a plain `OperationStatement` for any of
    # its MQTT-specific logic — `onMessage()`'s body is entirely preserved,
    # and `applyCommand()`'s only understood statement is a `ConditionalStatement`,
    # never an `OperationStatement`.
    program = round_trip(panel_one_source())
    for section_id in ("callback_onMessage", "helper_applyCommand"):
        section = program.section(section_id)
        assert section.operation is not None
        assert section.operation.operation_id == FUNCTIONS_IMPLEMENTATION
        assert section.operation_statements == ()


@pytest.mark.parametrize(
    "source",
    [
        "void setup() {\n}\n",
        "void loop() {\n}\n",
        "void setup() {\n  pinMode(LED, INPUT_PULLUP);\n}\n",
        "void loop() {\n  digitalWrite(3, LOW);\n}\n",
        "void loop() {\n  delay(250);\n}\n",
        "void loop() {\n  delay(2.5);\n}\n",
    ],
)
def test_each_supported_operation_survives_the_round_trip(source):
    assert round_trip(source) == semantic(source)


def test_the_round_trip_preserves_the_exact_source_text_of_every_statement():
    source = panel_one_source()
    before = [
        statement.source_text
        for section in semantic(source).sections
        for statement in section.statements
    ]
    after = [
        statement.source_text
        for section in round_trip(source).sections
        for statement in section.statements
    ]
    assert after == before


# =============================================================================
# 13. Determinism
# =============================================================================


def test_two_reverse_conversions_of_one_workspace_are_equal():
    drawn = program_to_blockly(semantic(panel_one_source()))
    assert blockly_to_semantic(drawn) == blockly_to_semantic(drawn)


def test_the_reverse_conversion_is_deterministic_across_independent_runs():
    source = panel_one_source()
    assert round_trip(source) == round_trip(source)


def test_the_reverse_conversion_does_not_modify_the_workspace():
    drawn = program_to_blockly(semantic(panel_one_source()))
    before = repr(drawn)
    blockly_to_semantic(drawn)
    assert repr(drawn) == before


# =============================================================================
# 14/15/16. Malformed input is reported, never repaired
# =============================================================================


def test_an_unknown_blockly_block_fails_clearly():
    block = BlocklyBlock(
        operation_id=TIME_DELAY,
        block_id="time.delay",
        block_type="wait_a_bit",
        source_text="delay(5);",
        fields=(BlocklyField("MS", "5"),),
    )
    with pytest.raises(UnknownBlocklyBlockError, match="wait_a_bit"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, block)))


def test_the_carried_operation_id_is_not_trusted_over_the_block_type():
    # A workspace states a TYPE; the operation is the catalog's answer, not the
    # block's claim. A real editor sends no operation id at all.
    definition = block_definition_for(TIME_DELAY, default_block_catalog)
    mislabelled = BlocklyBlock(
        operation_id=GPIO_PIN_MODE,
        block_id="gpio.pin_mode",
        block_type=definition.blockly_type,
        source_text="delay(5);",
        fields=(BlocklyField("MS", "5"),),
    )
    program = blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, mislabelled)))
    assert program.section("setup").statements[0].operation_id == TIME_DELAY


def test_a_missing_required_field_fails_clearly():
    definition = block_definition_for(TIME_DELAY, default_block_catalog)
    without_field = BlocklyBlock(
        operation_id=TIME_DELAY,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text="delay(5);",
    )
    with pytest.raises(MissingBlocklyFieldError, match="MS"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, without_field)))


def test_a_field_the_operation_has_no_operand_for_fails_clearly():
    block = statement_block(TIME_DELAY, "delay(5);", MS="5", UNITS="ms")
    with pytest.raises(UnsupportedBlocklyStructureError, match="UNITS"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, block)))


def test_a_field_value_no_field_can_hold_fails_clearly():
    block = statement_block(TIME_DELAY, "delay(1 + 2);", MS="1 + 2")
    with pytest.raises(InvalidBlocklyFieldValueError, match="1 . 2"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, block)))


def test_a_dropdown_value_outside_its_options_fails_clearly():
    block = statement_block(
        GPIO_DIGITAL_WRITE, "digitalWrite(2, PRESSED);", PIN="2", VALUE="PRESSED"
    )
    with pytest.raises(InvalidBlocklyFieldValueError, match="PRESSED"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, block)))


def test_a_container_inside_a_body_fails_clearly():
    nested = container_block(PROGRAM_LOOP)
    with pytest.raises(UnsupportedBlocklyStructureError, match="container"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, nested)))


def test_a_statement_block_carrying_a_body_fails_clearly():
    definition = block_definition_for(TIME_DELAY, default_block_catalog)
    nesting = BlocklyBlock(
        operation_id=TIME_DELAY,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text="delay(5);",
        fields=(BlocklyField("MS", "5"),),
        body_input="DO",
        body=(statement_block(TIME_DELAY, "delay(1);", MS="1"),),
    )
    with pytest.raises(UnsupportedBlocklyStructureError, match="no statement body"):
        blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, nesting)))


def test_a_container_drawing_fields_fails_clearly():
    definition = block_definition_for(PROGRAM_SETUP, default_block_catalog)
    with_fields = BlocklyBlock(
        operation_id=PROGRAM_SETUP,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        fields=(BlocklyField("PIN", "2"),),
    )
    with pytest.raises(UnsupportedBlocklyStructureError, match="no operands"):
        blockly_to_semantic(one_section_program(with_fields))


def test_a_statement_block_used_as_a_section_container_fails_clearly():
    with pytest.raises(UnsupportedBlocklyStructureError, match="statement"):
        blockly_to_semantic(one_section_program(statement_block(TIME_DELAY, "delay(5);", MS="5")))


@pytest.mark.parametrize("malformed", [None, "a workspace", 42, {"blocks": []}, []])
def test_something_that_is_not_a_workspace_fails_clearly(malformed):
    with pytest.raises(InvalidBlocklyWorkspaceError):
        blockly_to_semantic(malformed)


def test_a_block_with_no_recorded_source_becomes_a_statement_with_none():
    # A block authored in the editor has no source text anywhere, and B5 still
    # writes none: the statement it means simply has no provenance. Until B6
    # this was refused, because a source-less statement could not have been
    # turned back into firmware by anything; now its C++ is written from its
    # operation and its values, so the statement is complete without it. B5
    # itself is unchanged in what it will not do — it invents no text.
    definition = block_definition_for(TIME_DELAY, default_block_catalog)
    authored = BlocklyBlock(
        operation_id=TIME_DELAY,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        fields=(BlocklyField("MS", "5"),),
    )
    program = blockly_to_semantic(one_section_program(container_block(PROGRAM_SETUP, authored)))
    statement = program.setup.operation_statements[0]
    assert statement.operation_id == TIME_DELAY
    assert statement.value("MS") == LiteralValue(5, SemanticType.NUMBER)
    assert statement.source_text is None
    assert statement.has_source is False


def test_a_block_that_does_record_its_source_still_restores_it_exactly():
    program = blockly_to_semantic(
        one_section_program(
            container_block(PROGRAM_SETUP, statement_block(TIME_DELAY, "delay( 5 ) ;", MS="5"))
        )
    )
    assert program.setup.operation_statements[0].source_text == "delay( 5 ) ;"


def test_every_reverse_failure_is_a_bridge_error():
    for error in (
        InvalidBlocklyWorkspaceError,
        UnknownBlocklyBlockError,
        MissingBlocklyFieldError,
        InvalidBlocklyFieldValueError,
        UnsupportedBlocklyStructureError,
    ):
        assert issubclass(error, BlocklyBridgeError)
        assert issubclass(error, ValueError)


# =============================================================================
# 18. Dependency boundary — B5 adds no new dependency of any kind
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


def test_the_reverse_adapter_imports_only_the_two_layers_the_bridge_joins():
    allowed_prefixes = ("app.blockly", "app.build.semantic", "app.build.blockly_bridge")
    offenders = sorted(
        name
        for name in _imports(REVERSE_MODULE)
        if name.startswith("app.") and not name.startswith(allowed_prefixes)
    )
    assert offenders == []


def test_the_reverse_adapter_touches_no_filesystem_or_process():
    banned = ("subprocess", "os", "pathlib", "shutil", "tempfile", "socket", "importlib", "json")
    offenders = sorted(
        name for name in _imports(REVERSE_MODULE) for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert offenders == []


def test_the_reverse_adapter_generates_no_cpp():
    # B5 reads a workspace. B6 is what writes C++, and it is not this module:
    # no generator function, and no C++ fragment in any non-docstring string.
    tree = ast.parse(REVERSE_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            assert "to_cpp" not in lowered and "generate" not in lowered, node.name
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
        ):
            for fragment in ("void setup", "digitalWrite(", "pinMode(", "delay("):
                assert fragment not in node.value, node.value
