"""Phase B4 — the semantic IR -> Blockly adapter (`app/build/blockly_bridge/`).

Covers the bridge in isolation: the catalog lookup it is built on, the block
representation it produces, and what it does with source no block can draw.
Nothing here touches a `BuildSession`, `BuildWorkspace`, `BuildProject`,
`FileSegment`, a panel package, hardware or the compiler — the layer is
exercised against plain source strings, the real committed Panel 1 firmware
read off disk, and the real block catalog.

The three things this suite exists to pin down, beyond ordinary behaviour:

  * the two packages it bridges STILL DO NOT KNOW EACH OTHER (static import
    checks below, in both directions) — B4 is the only connection;
  * the catalog is the SOURCE OF TRUTH for operation -> block: no
    `"gpio.digital_write" -> "digitalwrite"` mapping exists in the bridge, and
    the one mapping it does add (field kinds and dropdown options) is checked
    against the real Blockly block definitions in `src/blockly/`;
  * NOTHING IS SILENTLY DROPPED OR COERCED — a literal never becomes a symbol,
    a symbol never becomes a number, and both kinds of undrawable source stay
    visible at the position they came from.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from app.blockly.categories import CATEGORIES
from app.blockly.catalog import BlockCatalog, default_block_catalog
from app.blockly.definitions.factory import BODY, BOOLEAN, NUMBER, PIN, TEXT, CategoryFactory
from app.blockly.models import ImplementationStatus, ValueType
from app.build.blockly_bridge import (
    BLOCKLY_LANGUAGE_VERSION,
    BlocklyBlock,
    BlocklyBridgeError,
    BlocklyField,
    BlocklyModelError,
    BlocklyProgram,
    BlocklySection,
    BridgeReason,
    FieldBinding,
    FieldBindingTable,
    FieldKind,
    PreservedSource,
    UnrepresentableOperationError,
    block_definition_for,
    build_default_bindings,
    default_field_bindings,
    program_to_blockly,
)
from app.build.discovery import analyze_source
from app.build.semantic import (
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TIME_DELAY,
    LiteralValue,
    OperationStatement,
    SemanticArgument,
    SemanticProgram,
    SemanticSection,
    SemanticType,
    SymbolValue,
    UnsupportedReason,
    analyze_document,
    default_semantic_operations,
)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
APP_DIR = BACKEND_DIR / "app"
BRIDGE_DIR = APP_DIR / "build" / "blockly_bridge"
SEMANTIC_DIR = APP_DIR / "build" / "semantic"
BLOCKLY_DIR = APP_DIR / "blockly"
ARDUINO_BLOCKS_JS = REPO_DIR / "src" / "blockly" / "arduinoBlocks.js"
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


def convert(source: str) -> BlocklyProgram:
    """Source text all the way through all three layers — B1, B3, then B4."""
    return program_to_blockly(analyze_document(analyze_source(source)))


def panel_one_source() -> str:
    return PANEL_ONE_INO.read_text(encoding="utf-8")


def body_types(block: BlocklyBlock) -> list[str]:
    """Each body item as either its Blockly type or its preserved text."""
    return [
        item.block_type if isinstance(item, BlocklyBlock) else item.text for item in block.body
    ]


def fields_of(block: BlocklyBlock) -> dict[str, str]:
    return {field.name: field.value for field in block.fields}


# =============================================================================
# 1. Operation lookup — the catalog is the source of truth
# =============================================================================


@pytest.mark.parametrize(
    "operation_id,blockly_type",
    [
        (PROGRAM_SETUP, "arduino_setup"),
        (PROGRAM_LOOP, "arduino_loop"),
        (GPIO_PIN_MODE, "pinmode"),
        (GPIO_DIGITAL_WRITE, "digitalwrite"),
        (TIME_DELAY, "delay"),
    ],
)
def test_every_ir_operation_resolves_through_the_catalog(operation_id, blockly_type):
    definition = block_definition_for(operation_id, default_block_catalog)
    assert definition.semantic_operation == operation_id
    assert definition.blockly_type == blockly_type


def test_the_lookup_ignores_cataloged_blocks_that_share_one_operation():
    # `gpio.pin_mode` is named by three catalog blocks; only one is
    # IMPLEMENTED, and only that one can actually be drawn.
    claimants = [
        block.block_id
        for block in default_block_catalog.blocks
        if block.semantic_operation == GPIO_PIN_MODE
    ]
    assert len(claimants) == 3
    assert block_definition_for(GPIO_PIN_MODE, default_block_catalog).block_id == "gpio.pin_mode"


def test_the_bridge_states_no_operation_to_block_type_mapping_of_its_own():
    # The relation belongs to the catalog. If a Blockly type is ever written
    # into this package, two places would own one fact.
    types = {"arduino_setup", "arduino_loop", "pinmode", "digitalwrite", "delay"}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in types
            }
        )
        assert offenders == [], f"{path.name} names Blockly type(s) the catalog owns: {offenders}"


# =============================================================================
# 2/3. Containers — program.setup and program.loop
# =============================================================================


def test_setup_becomes_its_container_block_with_the_catalogs_body_input():
    setup = convert(BLINK_SOURCE).section("setup")
    assert setup.block is not None
    assert setup.block.operation_id == PROGRAM_SETUP
    assert setup.block.block_type == "arduino_setup"
    assert setup.block.body_input == "DO"
    assert setup.block.fields == ()


def test_loop_becomes_its_own_container_block():
    loop = convert(BLINK_SOURCE).section("loop")
    assert loop.block is not None
    assert loop.block.operation_id == PROGRAM_LOOP
    assert loop.block.block_type == "arduino_loop"


def test_a_containers_body_holds_its_statements_as_children():
    loop = convert(BLINK_SOURCE).section("loop").block
    assert body_types(loop) == ["digitalwrite", "delay", "digitalwrite", "delay"]


def test_the_body_input_name_comes_from_the_catalog_not_from_a_constant():
    # Rename the body input in a private catalog; the bridge must follow it.
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
    program = program_to_blockly(
        analyze_document(analyze_source(BLINK_SOURCE)),
        catalog=BlockCatalog(CATEGORIES, blocks),
    )
    assert program.section("setup").block.body_input == "STACK"


def test_an_empty_container_body_is_a_block_with_no_children():
    program = convert("void setup() {\n}\n\nvoid loop() {\n}\n")
    assert program.section("setup").block.body == ()
    assert program.section("loop").block.body == ()


# =============================================================================
# 4/5/6. The three statement operations
# =============================================================================


def test_pin_mode_maps_its_two_operands_to_the_right_fields():
    program = convert("void setup() {\n  pinMode(4, INPUT);\n}\n")
    block = program.section("setup").block.body[0]
    assert block.operation_id == GPIO_PIN_MODE
    assert block.block_type == "pinmode"
    assert fields_of(block) == {"PIN": "4", "MODE": "INPUT"}


def test_digital_write_maps_its_two_operands_to_the_right_fields():
    program = convert("void loop() {\n  digitalWrite(13, LOW);\n}\n")
    block = program.section("loop").block.body[0]
    assert block.operation_id == GPIO_DIGITAL_WRITE
    assert block.block_type == "digitalwrite"
    assert fields_of(block) == {"PIN": "13", "VALUE": "LOW"}


def test_delay_maps_its_single_operand():
    program = convert("void loop() {\n  delay(250);\n}\n")
    block = program.section("loop").block.body[0]
    assert block.operation_id == TIME_DELAY
    assert fields_of(block) == {"MS": "250"}


def test_operands_are_never_swapped_when_their_values_look_alike():
    program = convert("void setup() {\n  pinMode(MODE_PIN, OUTPUT);\n}\n")
    assert fields_of(program.section("setup").block.body[0]) == {
        "PIN": "MODE_PIN",
        "MODE": "OUTPUT",
    }


def test_a_statement_block_carries_all_three_identities():
    block = convert("void loop() {\n  delay(5);\n}\n").section("loop").block.body[0]
    assert (block.operation_id, block.block_id, block.block_type) == (
        TIME_DELAY,
        "time.delay",
        "delay",
    )


# =============================================================================
# 7/8. Literal and symbolic values are not confused
# =============================================================================


def test_a_literal_pin_stays_the_number_that_was_written():
    program = convert("void setup() {\n  pinMode(2, OUTPUT);\n}\n")
    assert fields_of(program.section("setup").block.body[0])["PIN"] == "2"


def test_a_named_pin_constant_is_never_resolved_to_a_number():
    # The real Panel 1 firmware writes `pinMode(START_BUTTON, INPUT_PULLUP)`
    # and B3 does not resolve names. Neither does B4.
    program = convert("void setup() {\n  pinMode(START_BUTTON, INPUT_PULLUP);\n}\n")
    assert fields_of(program.section("setup").block.body[0]) == {
        "PIN": "START_BUTTON",
        "MODE": "INPUT_PULLUP",
    }


def test_a_symbol_and_a_literal_that_read_the_same_produce_the_same_field_text():
    # A field holds the operand AS WRITTEN; nothing about the value's kind is
    # lost, because the IR beside it still says which it was.
    symbolic = analyze_document(analyze_source("void loop() {\n  delay(1000);\n}\n"))
    assert isinstance(symbolic.loop.operation_statements[0].value("MS"), LiteralValue)
    assert fields_of(program_to_blockly(symbolic).section("loop").block.body[0]) == {"MS": "1000"}


def test_a_symbolic_duration_is_not_forced_into_a_numeric_field():
    # `delay(BUZZER_CHIRP_MS)` is understood by B3 and undrawable by the real
    # `delay` block, whose MS field is a Blockly FieldNumber. Preserved, not
    # coerced to 0 and not dropped.
    program = convert("void loop() {\n  delay(BUZZER_CHIRP_MS);\n}\n")
    item = program.section("loop").block.body[0]
    assert isinstance(item, PreservedSource)
    assert item.text == "delay(BUZZER_CHIRP_MS);"
    assert item.reason is BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE
    assert item.understood_by_the_ir is True


def test_a_boolean_literal_is_not_rewritten_into_a_dropdowns_token():
    # B3 represents `digitalWrite(2, true)` fine, but the real block's VALUE
    # field offers the tokens HIGH and LOW. Writing HIGH would silently change
    # what the source says, so the statement is carried verbatim instead.
    program = convert("void loop() {\n  digitalWrite(2, true);\n}\n")
    item = program.section("loop").block.body[0]
    assert isinstance(item, PreservedSource)
    assert item.text == "digitalWrite(2, true);"
    assert item.reason is BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE


def test_a_mode_outside_the_dropdowns_options_is_preserved_not_invented():
    program = convert("void setup() {\n  pinMode(2, ANALOG);\n}\n")
    item = program.section("setup").block.body[0]
    assert isinstance(item, PreservedSource)
    assert item.reason is BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE


def test_an_undrawable_authored_statement_has_nothing_to_preserve_and_says_so():
    # The one case B6's optional provenance opens here: a statement with no
    # recorded source whose operand no real field can hold. There is no block
    # to build AND no text to carry, so there is nothing faithful to produce.
    # Reported rather than coerced into a field that would change what it says
    # — its C++ still exists (the generator writes it from the operation), but
    # this workspace cannot show it.
    authored = OperationStatement(
        operation=default_semantic_operations.require(TIME_DELAY),
        arguments=(SemanticArgument("MS", SymbolValue("BUZZER_CHIRP_MS")),),
    )
    program = SemanticProgram(
        sections=(
            SemanticSection(
                section_id="loop",
                operation=default_semantic_operations.require(PROGRAM_LOOP),
                statements=(authored,),
            ),
        )
    )
    with pytest.raises(UnrepresentableOperationError, match="records no source"):
        program_to_blockly(program)


def test_a_free_text_field_accepts_both_value_kinds():
    binding = FieldBinding("PIN", FieldKind.TEXT)
    assert binding.render(LiteralValue(7, SemanticType.NUMBER)) == "7"
    assert binding.render(SymbolValue("MOTOR_IN1")) == "MOTOR_IN1"


def test_a_numeric_field_accepts_a_number_and_refuses_a_symbol():
    binding = FieldBinding("MS", FieldKind.NUMBER)
    assert binding.render(LiteralValue(1000, SemanticType.NUMBER)) == "1000"
    assert binding.render(LiteralValue(3, SemanticType.PIN)) == "3"
    assert binding.render(SymbolValue("BUZZER_CHIRP_MS")) is None
    assert binding.render(LiteralValue(True, SemanticType.BOOLEAN)) is None


def test_a_dropdown_accepts_only_its_declared_tokens():
    binding = FieldBinding("VALUE", FieldKind.DROPDOWN, ("HIGH", "LOW"))
    assert binding.render(SymbolValue("HIGH")) == "HIGH"
    assert binding.render(SymbolValue("PRESSED")) is None
    assert binding.render(LiteralValue(True, SemanticType.BOOLEAN)) is None


def test_a_binding_declares_options_exactly_when_it_is_a_dropdown():
    with pytest.raises(ValueError):
        FieldBinding("MODE", FieldKind.DROPDOWN)
    with pytest.raises(ValueError):
        FieldBinding("PIN", FieldKind.TEXT, ("HIGH",))


# =============================================================================
# 9. Argument names are preserved, not positions
# =============================================================================


def test_field_names_are_the_operations_parameter_names():
    program = convert(BLINK_SOURCE)
    for block in program.section("loop").block.child_blocks:
        operation = default_semantic_operations.require(block.operation_id)
        assert [field.name for field in block.fields] == list(operation.parameter_names)


def test_field_names_are_also_the_catalog_blocks_input_names():
    program = convert(BLINK_SOURCE)
    for block in program.section("loop").block.child_blocks:
        definition = default_block_catalog.block(block.block_id)
        expected = [
            item.name for item in definition.inputs if item.value_type is not ValueType.STATEMENTS
        ]
        assert [field.name for field in block.fields] == expected


def test_every_bound_operation_agrees_with_the_catalog_and_the_ir():
    for operation_id in default_field_bindings.operation_ids:
        operation = default_semantic_operations.require(operation_id)
        definition = block_definition_for(operation_id, default_block_catalog)
        bound = tuple(
            binding.input_name for binding in default_field_bindings.fields_for(operation_id)
        )
        catalog_inputs = tuple(
            item.name for item in definition.inputs if item.value_type is not ValueType.STATEMENTS
        )
        assert operation.parameter_names == bound == catalog_inputs, operation_id


def test_containers_bind_no_fields_because_they_take_no_operands():
    for operation_id in (PROGRAM_SETUP, PROGRAM_LOOP):
        assert operation_id not in default_field_bindings
        assert default_semantic_operations.require(operation_id).parameters == ()


# =============================================================================
# 10/11. Order is preserved, at both levels
# =============================================================================


def test_statement_order_survives_conversion():
    source = (
        "void setup() {\n"
        "  pinMode(1, OUTPUT);\n"
        "  digitalWrite(1, HIGH);\n"
        "  delay(10);\n"
        "  pinMode(2, INPUT);\n"
        "}\n"
    )
    assert body_types(convert(source).section("setup").block) == [
        "pinmode",
        "digitalwrite",
        "delay",
        "pinmode",
    ]


def test_order_is_preserved_across_supported_and_unsupported_statements():
    source = (
        "void setup() {\n"
        "  pinMode(1, OUTPUT);\n"
        "  Serial.begin(115200);\n"
        "  pinMode(2, INPUT);\n"
        "}\n"
    )
    assert body_types(convert(source).section("setup").block) == [
        "pinmode",
        "Serial.begin(115200);",
        "pinmode",
    ]


def test_the_blockly_chain_skips_preserved_source_but_the_record_locates_it():
    source = (
        "void setup() {\n"
        "  pinMode(1, OUTPUT);\n"
        "  Serial.begin(115200);\n"
        "  pinMode(2, INPUT);\n"
        "}\n"
    )
    program = convert(source)
    assert [block.block_type for block in program.section("setup").block.child_blocks] == [
        "pinmode",
        "pinmode",
    ]
    records = program.preserved
    assert [(record.section_id, record.index) for record in records] == [("setup", 1)]


def test_section_order_and_ids_follow_the_document():
    program = convert(BLINK_SOURCE)
    semantic = analyze_document(analyze_source(BLINK_SOURCE))
    assert [section.section_id for section in program.sections] == [
        section.section_id for section in semantic.sections
    ]


def test_top_level_blocks_are_the_container_sections_in_document_order():
    program = convert(BLINK_SOURCE)
    assert [block.block_type for block in program.blocks] == ["arduino_setup", "arduino_loop"]


# =============================================================================
# 12. Unsupported source is preserved, never discarded
# =============================================================================


def test_every_semantic_statement_produces_exactly_one_representation():
    semantic = analyze_document(analyze_source(panel_one_source()))
    program = program_to_blockly(semantic)
    for section in semantic.sections:
        assert len(program.section(section.section_id).items) == len(section.statements), (
            section.section_id
        )


def test_no_unsupported_source_text_is_lost_for_the_real_firmware():
    semantic = analyze_document(analyze_source(panel_one_source()))
    program = program_to_blockly(semantic)
    expected = [
        statement.source_text
        for section in semantic.sections
        for statement in section.statements
        if not statement.supported
    ]
    kept = [record.source.text for record in program.preserved]
    # Every unsupported statement is kept; the only additions are understood
    # statements the editor could not draw.
    assert set(expected).issubset(set(kept))
    assert [text for text in kept if text in set(expected)] == expected


def test_b3s_reason_travels_through_unchanged():
    program = convert("void setup() {\n  client.setServer(HOST, PORT);\n}\n")
    item = program.section("setup").block.body[0]
    assert item.reason is UnsupportedReason.NOT_A_CALL
    assert item.understood_by_the_ir is False


def test_an_unsupported_section_becomes_preserved_source_and_no_block():
    # CORRECTED FIXTURE: a HELPER_FUNCTION no longer demonstrates this — it
    # gained a container form (`functions.implementation`, the no-device/
    # Blockly-integration correction). GLOBAL_DECLARATIONS is the one kind
    # that still has none: it names no single construct a container could
    # represent.
    program = convert("int counter = 0;\n\nvoid setup() {\n}\n")
    section = program.section("global")
    assert section.block is None
    assert len(section.preserved) == 1
    assert section.preserved[0].reason is UnsupportedReason.UNSUPPORTED_SECTION
    assert "int counter = 0;" in section.preserved[0].text


def test_an_unsupported_sections_body_is_never_drawn_as_blocks():
    # A global-scope, call-shaped fragment is a statement B4 could draw
    # inside a function body. It is not drawn here, because
    # GLOBAL_DECLARATIONS has no container form at all — B3 never interpreted
    # it as a body to split into statements in the first place.
    program = convert("digitalWrite(1, HIGH);\n\nvoid setup() {\n}\n")
    assert [block.block_type for block in program.blocks] == ["arduino_setup"]


def test_a_control_flow_block_is_preserved_whole():
    source = (
        "void loop() {\n"
        "  if (ready) {\n"
        "    digitalWrite(1, HIGH);\n"
        "  }\n"
        "  delay(5);\n"
        "}\n"
    )
    program = convert(source)
    items = program.section("loop").block.body
    assert isinstance(items[0], PreservedSource)
    assert items[0].text.startswith("if (ready)")
    assert isinstance(items[1], BlocklyBlock)


def test_the_two_kinds_of_preservation_are_distinguishable():
    source = (
        "void loop() {\n"
        "  Serial.println(1);\n"
        "  delay(BLINK_MS);\n"
        "}\n"
    )
    records = convert(source).preserved
    assert [record.source.understood_by_the_ir for record in records] == [False, True]
    assert [record.source.reason.value for record in records] == [
        "not_a_call",
        "field_value_not_representable",
    ]


# =============================================================================
# 13. Determinism
# =============================================================================


def test_two_conversions_of_one_program_are_equal():
    semantic = analyze_document(analyze_source(panel_one_source()))
    assert program_to_blockly(semantic) == program_to_blockly(semantic)


def test_conversion_is_deterministic_across_independent_runs():
    source = panel_one_source()
    assert convert(source) == convert(source)


def test_the_serialized_representation_is_byte_identical_across_runs():
    source = panel_one_source()
    first = json.dumps(convert(source).to_representation(), sort_keys=False)
    second = json.dumps(convert(source).to_representation(), sort_keys=False)
    assert first == second


def test_conversion_does_not_modify_the_semantic_program():
    semantic = analyze_document(analyze_source(panel_one_source()))
    before = repr(semantic)
    program_to_blockly(semantic)
    assert repr(semantic) == before


# =============================================================================
# 14. Missing / unknown / inconsistent operations fail explicitly
# =============================================================================


def _catalog_without(block_id: str) -> BlockCatalog:
    return BlockCatalog(
        CATEGORIES,
        tuple(block for block in default_block_catalog.blocks if block.block_id != block_id),
    )


def test_an_operation_with_no_implemented_block_raises():
    catalog = _catalog_without(TIME_DELAY)
    with pytest.raises(UnrepresentableOperationError, match="no implemented block"):
        program_to_blockly(analyze_document(analyze_source(BLINK_SOURCE)), catalog=catalog)


def test_a_cataloged_but_unimplemented_block_does_not_satisfy_the_lookup():
    factory = CategoryFactory("time")
    blocks = tuple(
        block for block in default_block_catalog.blocks if block.block_id != TIME_DELAY
    ) + (factory.statement(TIME_DELAY, "wait", "Pauses.", (("MS", NUMBER),)),)
    with pytest.raises(UnrepresentableOperationError):
        program_to_blockly(
            analyze_document(analyze_source(BLINK_SOURCE)),
            catalog=BlockCatalog(CATEGORIES, blocks),
        )


def test_two_implemented_blocks_claiming_one_operation_are_ambiguous():
    factory = CategoryFactory("time")
    blocks = default_block_catalog.blocks + (
        factory.statement(
            "time.delay_alias",
            "wait too",
            "A second implemented block for one operation.",
            (("MS", NUMBER),),
            op=TIME_DELAY,
            implemented_as="delay_alias",
        ),
    )
    with pytest.raises(UnrepresentableOperationError, match="ambiguous"):
        program_to_blockly(
            analyze_document(analyze_source(BLINK_SOURCE)),
            catalog=BlockCatalog(CATEGORIES, blocks),
        )


def test_catalog_inputs_that_disagree_with_the_ir_raise_rather_than_guess():
    factory = CategoryFactory("outputs")
    blocks = tuple(
        block for block in default_block_catalog.blocks if block.block_id != GPIO_DIGITAL_WRITE
    ) + (
        factory.statement(
            GPIO_DIGITAL_WRITE,
            "digital write",
            "Drives a pin.",
            (("PIN", PIN), ("LEVEL", BOOLEAN)),
            implemented_as="digitalwrite",
        ),
    )
    with pytest.raises(UnrepresentableOperationError, match="do not match"):
        program_to_blockly(
            analyze_document(analyze_source(BLINK_SOURCE)),
            catalog=BlockCatalog(CATEGORIES, blocks),
        )


def test_an_operation_with_no_field_binding_raises():
    with pytest.raises(UnrepresentableOperationError, match="no field binding"):
        program_to_blockly(
            analyze_document(analyze_source(BLINK_SOURCE)),
            bindings=FieldBindingTable({}),
        )


def test_a_binding_that_disagrees_with_the_ir_raises():
    bindings = FieldBindingTable(
        {
            GPIO_PIN_MODE: (
                FieldBinding("PIN", FieldKind.TEXT),
                FieldBinding("LEVEL", FieldKind.TEXT),
            )
        }
    )
    with pytest.raises(UnrepresentableOperationError, match="field bindings"):
        program_to_blockly(
            analyze_document(analyze_source("void setup() {\n  pinMode(1, OUTPUT);\n}\n")),
            bindings=bindings,
        )


def test_a_container_operation_drawn_as_a_statement_block_raises():
    factory = CategoryFactory("program")
    blocks = tuple(
        block for block in default_block_catalog.blocks if block.block_id != PROGRAM_SETUP
    ) + (
        factory.statement(
            PROGRAM_SETUP, "setup", "Runs once.", (), implemented_as="arduino_setup"
        ),
    )
    with pytest.raises(UnrepresentableOperationError, match="container"):
        program_to_blockly(
            analyze_document(analyze_source(BLINK_SOURCE)),
            catalog=BlockCatalog(CATEGORIES, blocks),
        )


def test_the_adapter_refuses_input_that_is_not_a_semantic_program():
    with pytest.raises(TypeError):
        program_to_blockly("void setup() {}")
    with pytest.raises(TypeError):
        program_to_blockly(SemanticProgram(sections=()), catalog=object())
    with pytest.raises(TypeError):
        program_to_blockly(SemanticProgram(sections=()), bindings=object())


def test_all_bridge_errors_share_one_base_class():
    assert issubclass(UnrepresentableOperationError, BlocklyBridgeError)
    assert issubclass(BlocklyModelError, BlocklyBridgeError)
    assert issubclass(BlocklyBridgeError, ValueError)


# =============================================================================
# 15. Catalog and frontend conformance — one fact, one owner
# =============================================================================


def test_every_ir_operation_has_exactly_one_implemented_block():
    for operation_id in default_semantic_operations.operation_ids:
        assert block_definition_for(operation_id, default_block_catalog) is not None


def test_the_bridge_binds_exactly_the_ir_operations_that_take_operands():
    expected = {
        operation.operation_id
        for operation in default_semantic_operations.operations
        if operation.parameters
    }
    assert set(default_field_bindings.operation_ids) == expected


def test_two_binding_builds_are_equal():
    first, second = build_default_bindings(), build_default_bindings()
    assert first.operation_ids == second.operation_ids
    for operation_id in first.operation_ids:
        assert first.fields_for(operation_id) == second.fields_for(operation_id)


def _frontend_block_fields() -> dict[str, dict[str, tuple[str, tuple[str, ...]]]]:
    """Field kinds and dropdown options as `src/blockly/arduinoBlocks.js` defines them.

    Read rather than imported for the reason B3 gives for the operation
    vocabulary: a language boundary cannot carry a dependency, so the
    agreement is enforced by a test instead.
    """
    source = ARDUINO_BLOCKS_JS.read_text(encoding="utf-8")
    option_lists = {
        name: tuple(re.findall(r"\['([A-Z_]+)',", body))
        for name, body in re.findall(r"const (\w+) = \[(.*?)\]\n", source, re.S)
    }
    blocks: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {}
    chunks = source.split("Blockly.Blocks['")[1:]
    for chunk in chunks:
        block_type = chunk.split("'", 1)[0]
        fields: dict[str, tuple[str, tuple[str, ...]]] = {}
        for kind, argument, name in re.findall(
            r"new Blockly\.Field(\w+)\(([^)]*)\),\s*'([A-Z_]+)'", chunk
        ):
            options = option_lists.get(argument.strip(), ())
            fields[name] = (kind, options)
        blocks[block_type] = fields
    return blocks


#: How the frontend's Blockly field classes map onto this layer's field kinds.
_FRONTEND_FIELD_KINDS = {
    "TextInput": FieldKind.TEXT,
    "Number": FieldKind.NUMBER,
    "Dropdown": FieldKind.DROPDOWN,
}


def test_the_frontend_block_definitions_were_read_at_all():
    frontend = _frontend_block_fields()
    assert set(frontend) == {
        "arduino_setup",
        "arduino_loop",
        "pinmode",
        "digitalwrite",
        "delay",
        # The no-device/Blockly-integration correction's three additions.
        "function_implementation",
        "call_existing_function",
        "if_equals",
    }
    assert frontend["pinmode"]["MODE"][1] == ("OUTPUT", "INPUT", "INPUT_PULLUP")


def test_every_binding_matches_the_real_blockly_field_it_targets():
    frontend = _frontend_block_fields()
    for operation_id in default_field_bindings.operation_ids:
        blockly_type = block_definition_for(operation_id, default_block_catalog).blockly_type
        drawn = frontend[blockly_type]
        for binding in default_field_bindings.fields_for(operation_id):
            kind, options = drawn[binding.input_name]
            assert _FRONTEND_FIELD_KINDS[kind] is binding.kind, (
                f"{operation_id}.{binding.input_name} is a Field{kind} in arduinoBlocks.js"
            )
            assert binding.options == options, f"{operation_id}.{binding.input_name} options"


def test_the_container_blocks_draw_no_fields_in_the_frontend_either():
    frontend = _frontend_block_fields()
    assert frontend["arduino_setup"] == {}
    assert frontend["arduino_loop"] == {}


def test_the_generated_workspace_state_matches_blocklys_serialization_shape():
    state = convert(BLINK_SOURCE).to_workspace_state()
    assert set(state) == {"blocks"}
    assert state["blocks"]["languageVersion"] == BLOCKLY_LANGUAGE_VERSION
    setup = state["blocks"]["blocks"][0]
    assert setup["type"] == "arduino_setup"
    assert setup["inputs"]["DO"]["block"]["type"] == "pinmode"
    assert setup["inputs"]["DO"]["block"]["fields"] == {"PIN": "LED", "MODE": "OUTPUT"}
    loop = state["blocks"]["blocks"][1]
    chain = loop["inputs"]["DO"]["block"]
    assert chain["type"] == "digitalwrite"
    assert chain["next"]["block"]["type"] == "delay"
    assert chain["next"]["block"]["next"]["block"]["type"] == "digitalwrite"


def test_the_representation_carries_both_halves():
    representation = convert(BLINK_SOURCE).to_representation()
    assert set(representation) == {"workspace", "preserved"}
    assert representation["preserved"] == [
        {
            "sectionId": "global",
            "index": 0,
            "text": "const int LED = 2;",
            "reason": "unsupported_section",
            "understoodByTheIr": False,
        }
    ]


def test_top_level_blocks_are_positioned_deterministically_and_apart():
    blocks = convert(BLINK_SOURCE).to_workspace_state()["blocks"]["blocks"]
    positions = [(block["x"], block["y"]) for block in blocks]
    assert len(set(positions)) == len(positions)
    assert positions == [(block["x"], block["y"]) for block in
                         convert(BLINK_SOURCE).to_workspace_state()["blocks"]["blocks"]]


# =============================================================================
# Bridge model validation
# =============================================================================


def test_a_block_with_a_body_must_name_the_input_it_hangs_from():
    child = BlocklyBlock(operation_id=TIME_DELAY, block_id="time.delay", block_type="delay")
    with pytest.raises(BlocklyModelError):
        BlocklyBlock(
            operation_id=PROGRAM_SETUP,
            block_id="program.setup",
            block_type="arduino_setup",
            body=(child,),
        )


def test_a_section_cannot_hold_a_block_and_loose_preserved_source():
    block = BlocklyBlock(
        operation_id=PROGRAM_SETUP, block_id="program.setup", block_type="arduino_setup"
    )
    with pytest.raises(BlocklyModelError):
        BlocklySection(
            section_id="setup",
            block=block,
            preserved=(PreservedSource("x;", UnsupportedReason.NOT_A_CALL),),
        )


def test_preserved_source_requires_text_and_a_known_reason():
    with pytest.raises(BlocklyModelError):
        PreservedSource("   ", UnsupportedReason.NOT_A_CALL)
    with pytest.raises(BlocklyModelError):
        PreservedSource("x;", "not_a_call")


def test_a_program_rejects_duplicate_section_ids():
    section = BlocklySection(section_id="setup", block=None, preserved=())
    with pytest.raises(BlocklyModelError):
        BlocklyProgram(sections=(section, section))


def test_a_field_name_follows_the_catalogs_input_naming():
    with pytest.raises(BlocklyModelError):
        BlocklyField(name="pin", value="2")
    with pytest.raises(BlocklyModelError):
        BlocklyField(name="PIN", value="")


# =============================================================================
# 16. The real Panel 1 firmware as an integration fixture
# =============================================================================


def test_panel_one_setup_draws_its_seven_pin_modes():
    program = convert(panel_one_source())
    blocks = program.section("setup").block.child_blocks
    assert [fields_of(block)["PIN"] for block in blocks] == [
        "START_BUTTON",
        "STOP_BUTTON",
        "MOTOR_IN1",
        "MOTOR_IN2",
        "GREEN_LED",
        "RED_LED",
        "BUZZER",
    ]
    assert {fields_of(block)["MODE"] for block in blocks} == {"INPUT_PULLUP", "OUTPUT"}


def test_panel_one_setup_keeps_its_wifi_and_mqtt_source_between_the_blocks():
    program = convert(panel_one_source())
    items = body_types(program.section("setup").block)

    # The stable prefix -- Serial.begin, the seven pin_mode blocks, then the
    # initial motor-safe state -- is unaffected by the real Wi-Fi connect
    # sequence appended after it, so it is still pinned by position.
    assert items[0] == "Serial.begin(115200);"
    assert items[1:8] == ["pinmode"] * 7
    assert items[8] == "setMotorOutputs(false);"

    # The real Wi-Fi connect and MQTT client wiring that follows must survive
    # the semantic/block transformation as preserved source, in the order the
    # firmware performs it. Content-based rather than pinned to an exact
    # index: the diagnostic Serial output interleaved between these calls is
    # free to grow or shrink independently of what this test actually cares
    # about, which is that these statements are not lost or reordered.
    def index_of(prefix: str) -> int:
        for i, item in enumerate(items):
            if item.startswith(prefix):
                return i
        raise AssertionError(f"no preserved item starts with {prefix!r}: {items}")

    wifi_begin = index_of("WiFi.begin(")
    wifi_wait = index_of("while (WiFi.status()")
    set_server = index_of("client.setServer(")
    set_callback = index_of("client.setCallback(")

    assert wifi_begin > 8
    assert wifi_begin < wifi_wait < set_server < set_callback


def test_panel_one_loop_draws_its_two_zero_arg_calls_and_preserves_the_rest():
    # CORRECTED: `ensureConnected()`/`pollButtons()` are zero-argument calls
    # to functions this firmware defines itself — both draw as
    # `call_existing_function` blocks now (the no-device/Blockly-integration
    # correction). `client.loop();` is a method call on an object and stays
    # preserved.
    loop = convert(panel_one_source()).section("loop")
    assert loop.block is not None
    assert [block.block_type for block in loop.block.child_blocks] == [
        "call_existing_function",
        "call_existing_function",
    ]
    preserved_texts = [
        item.text for item in loop.block.body if isinstance(item, PreservedSource)
    ]
    assert preserved_texts == ["client.loop();"]


def test_panel_one_mqtt_logic_is_never_claimed_as_a_block():
    # CORRECTED: both sections are representable CONTAINERS now
    # (`functions.implementation`), but their MQTT-specific bodies are still
    # never drawn — only the generic pieces this phase actually models (a
    # comparison-gated `if`) are.
    program = convert(panel_one_source())
    on_message = program.section("callback_onMessage")
    assert on_message.block is not None
    assert on_message.block.child_blocks == ()

    apply_command = program.section("helper_applyCommand")
    assert apply_command.block is not None
    assert [block.block_type for block in apply_command.block.child_blocks] == ["if_equals"]

    for block_type in ("mosquitto_pub", "mqtt_publish", "mqtt_subscribe"):
        assert block_type not in [block.block_type for block in program.blocks]


def test_panel_one_produces_a_loadable_workspace_and_a_full_preserved_list():
    program = convert(panel_one_source())
    state = program.to_workspace_state()
    block_types = [block["type"] for block in state["blocks"]["blocks"]]
    assert block_types.count("arduino_setup") == 1
    assert block_types.count("arduino_loop") == 1
    # Every HELPER_FUNCTION/CALLBACK section is its own container now.
    assert block_types.count("function_implementation") >= 8
    # Every fragment the workspace cannot hold is still reported, located.
    assert len(program.preserved) > 5
    for record in program.preserved:
        assert record.source.text.strip()
        assert record.index >= 0


# =============================================================================
# 17. Dependency boundary — the bridge is the ONLY connection
# =============================================================================

_BRIDGE_MODULES = tuple(sorted(BRIDGE_DIR.glob("*.py")))

#: The bridge may see the two layers it joins and nothing else. In
#: particular it must not learn about sessions, panels, hardware or the
#: compiler — B4 is a representation, not a workflow.
_FORBIDDEN_BRIDGE_IMPORTS = (
    "app.build.models",
    "app.build.workspace",
    "app.build.service",
    "app.build.compiler",
    "app.build.flasher",
    "app.build.process",
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


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_bridge_modules_were_found():
    assert {path.name for path in _BRIDGE_MODULES} == {
        "__init__.py",
        "adapter.py",
        "bindings.py",
        "errors.py",
        "models.py",
        # B5's direction. Every static check in this section covers it too.
        "reverse.py",
        # B8's correction: Blockly's own serialization JSON read back into
        # B4's shape, so B5 is reachable from an editor and not only from
        # inside this codebase. Every static check in this section covers it.
        "workspace_state.py",
    }


def test_the_semantic_layer_still_does_not_import_blockly_or_the_bridge():
    for path in sorted(SEMANTIC_DIR.glob("*.py")):
        offenders = sorted(
            name
            for name in _imports(path)
            if name.startswith("app.blockly") or name.startswith("app.build.blockly_bridge")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_blockly_package_still_does_not_import_the_semantic_layer_or_the_bridge():
    for path in sorted(BLOCKLY_DIR.rglob("*.py")):
        offenders = sorted(name for name in _imports(path) if name.startswith("app.build"))
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_bridge_is_the_only_module_that_imports_both_packages():
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.parent == BRIDGE_DIR:
            continue
        imports = _imports(path)
        both = any(name.startswith("app.blockly") for name in imports) and any(
            name.startswith("app.build.semantic") for name in imports
        )
        assert not both, f"{path.relative_to(APP_DIR)} joins the two packages outside the bridge"


def test_the_bridge_imports_no_workflow_layer():
    for path in _BRIDGE_MODULES:
        offenders = sorted(
            name
            for name in _imports(path)
            for prefix in _FORBIDDEN_BRIDGE_IMPORTS
            if name == prefix or name.startswith(prefix + ".")
        )
        assert offenders == [], f"{path.name} imports forbidden layer(s): {offenders}"


def test_the_bridge_touches_no_filesystem_or_process():
    banned = ("subprocess", "os", "pathlib", "shutil", "tempfile", "socket", "importlib")
    for path in _BRIDGE_MODULES:
        offenders = sorted(
            name for name in _imports(path) for bad in banned
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_bridge_uses_no_dynamic_execution():
    banned_names = {"eval", "exec", "compile", "__import__"}
    banned_attrs = {"system", "popen", "Popen", "run", "spawnv", "spawn"}
    for path in _BRIDGE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in banned_names, f"{path.name} calls {func.id}"
            if isinstance(func, ast.Attribute):
                assert func.attr not in banned_attrs, f"{path.name} calls .{func.attr}"


def test_the_bridge_introduces_no_editability_or_security_vocabulary():
    # B4 answers "how is this represented?", never "may the student change
    # it?". Those decisions stay on B2's FileSegment/BuildProject.
    banned = ("editable", "security_region", "locked", "region_kind", "permission")
    for path in _BRIDGE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        offenders = sorted(name for name in names for bad in banned if bad in name.lower())
        assert offenders == [], f"{path.name} declares {offenders}"


def test_the_bridge_names_no_panel_specific_literal():
    forbidden = {
        "smart-home-mqtt-control",
        "20:9b:a9:88:0b:e4",
        "cybertrainer/smart-home/motor/control",
        "192.168.50.1",
        "onMessage",
        "applyCommand",
        "setMotorOutputs",
        "START_BUTTON",
        "MOTOR_IN1",
    }
    for path in _BRIDGE_MODULES:
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


def _non_docstring_strings(tree: ast.AST) -> set[str]:
    """Every string constant except the module/class/function docstrings.

    Docstrings in this package quote C++ on purpose — that is how they explain
    what is and is not representable. A C++ fragment in any OTHER string would
    mean the bridge had started emitting source.
    """
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    }


def test_the_bridge_implements_no_code_generation_direction():
    # B4 draws the IR as blocks and B5 reads them back; B6 owns Blockly -> C++
    # and IR -> C++. Nothing in this package may emit C++ or reconstruct source
    # — `blockly_to_semantic` produces an IR, never a fragment of a file.
    for path in _BRIDGE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in functions:
            lowered = name.lower()
            assert "to_cpp" not in lowered and "generate" not in lowered, name
            assert "to_source" not in lowered and "reconstruct" not in lowered, name
        for text in _non_docstring_strings(tree):
            assert "void setup" not in text, f"{path.name}: {text!r}"
            assert "digitalWrite(" not in text, f"{path.name}: {text!r}"
            assert "pinMode(" not in text, f"{path.name}: {text!r}"
