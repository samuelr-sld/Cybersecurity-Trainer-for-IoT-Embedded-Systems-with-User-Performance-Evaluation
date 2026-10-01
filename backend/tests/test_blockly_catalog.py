"""Phase B0.1: the master Blockly block catalog.

Covers the catalog's structural rules, the toolbox built from it, coverage of
every concept the phase brief lists, that it stays generic and decoupled, and
that the frontend's generated copy has not drifted from it.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

from app.blockly.catalog import BlockCatalog, build_default_catalog, default_block_catalog
from app.blockly.export import FRONTEND_MODULE_PATH, catalog_to_dict, render_frontend_module
from app.blockly.models import (
    BlockCategory,
    BlockDefinition,
    BlockInput,
    BlockKind,
    CatalogError,
    CategoryGroup,
    ImplementationStatus,
    ValueType,
    is_qualified_id,
)
from app.blockly.toolbox import build_toolbox

BLOCKLY_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "blockly"

#: Every category the phase brief names, in the brief's order.
REQUIRED_CATEGORIES = (
    "logic", "loops", "math", "text", "variables", "functions", "lists", "program", "inputs", "outputs",
    "time", "serial", "i2c", "spi", "wifi", "mqtt", "http", "bluetooth", "espnow", "sensors", "displays",
    "graphics", "motors", "neopixel", "charts", "animated_eyes", "filesystem",
)

#: The concepts the brief lists for each category, as catalog block ids.
REQUIRED_BLOCKS = {
    "logic": (
        "logic.true logic.false logic.not logic.and logic.or logic.equal logic.not_equal logic.greater "
        "logic.less logic.greater_equal logic.less_equal logic.if logic.if_else logic.else_if logic.ternary"
    ),
    "loops": "loops.repeat loops.while loops.do_while loops.for loops.for_each loops.break loops.continue",
    "math": (
        "math.number math.add math.subtract math.multiply math.divide math.modulo math.power math.abs "
        "math.min math.max math.random math.constrain math.round math.floor math.ceil math.sqrt"
    ),
    "text": (
        "text.literal text.join text.length text.contains text.substring text.char_at text.replace "
        "text.upper text.lower text.from_number text.to_number"
    ),
    "variables": "variables.declare variables.get variables.set variables.increment variables.decrement",
    "functions": "functions.define functions.call functions.parameter functions.return",
    "lists": "lists.create lists.get lists.set lists.add lists.remove lists.length lists.find lists.sublist",
    "program": "program.setup program.loop program.global_declaration program.include",
    "inputs": "gpio.digital_read gpio.analog_read gpio.pin_mode gpio.pin_mode_input gpio.pin_mode_input_pullup",
    "outputs": "gpio.digital_write gpio.pwm_write gpio.analog_write",
    "time": "time.delay time.millis time.micros time.elapsed time.every",
    "serial": (
        "serial.begin serial.print serial.println serial.available serial.read serial.read_string "
        "serial.read_char serial.parse_number serial.flush"
    ),
    "i2c": "i2c.begin i2c.begin_transmission i2c.write i2c.request i2c.read i2c.end_transmission",
    "spi": "spi.begin spi.transfer spi.read spi.write",
    "wifi": "wifi.connect wifi.disconnect wifi.status wifi.local_ip wifi.rssi wifi.ssid wifi.password wifi.hostname",
    "mqtt": (
        "mqtt.initialize mqtt.connect mqtt.disconnect mqtt.connected mqtt.loop mqtt.subscribe mqtt.publish "
        "mqtt.callback mqtt.topic mqtt.payload mqtt.broker_address mqtt.port mqtt.username mqtt.password"
    ),
    "http": (
        "http.get http.post http.put http.delete http.response_status http.response_body http.set_header"
    ),
    "bluetooth": (
        "bluetooth.initialize bluetooth.connect bluetooth.disconnect bluetooth.send bluetooth.receive "
        "bluetooth.available"
    ),
    "espnow": "espnow.initialize espnow.add_peer espnow.send espnow.on_receive",
    "sensors": (
        "sensor.digital_read sensor.analog_read sensor.dht_temperature sensor.dht_humidity "
        "sensor.ultrasonic_distance sensor.light sensor.temperature sensor.humidity sensor.pressure "
        "sensor.accelerometer sensor.gyroscope sensor.color"
    ),
    "displays": (
        "display.initialize display.clear display.set_cursor display.print display.println display.show "
        "display.text_size display.text_color"
    ),
    "graphics": (
        "graphics.draw_pixel graphics.draw_line graphics.draw_rect graphics.fill_rect "
        "graphics.draw_circle graphics.fill_circle"
    ),
    "motors": (
        "motor.initialize motor.start motor.stop motor.set_speed motor.set_direction motor.set_pwm "
        "servo.attach servo.write servo.detach"
    ),
    "neopixel": (
        "neopixel.initialize neopixel.set_pixel neopixel.set_all neopixel.rgb neopixel.set_brightness "
        "neopixel.show neopixel.clear"
    ),
    "charts": "chart.initialize chart.add_value chart.set_range chart.update chart.clear",
    "animated_eyes": "eyes.initialize eyes.set_expression eyes.look eyes.blink eyes.animate eyes.update",
    "filesystem": (
        "fs.begin fs.open fs.close fs.read fs.write fs.append fs.exists fs.delete fs.size fs.list_files"
    ),
}

#: The five blocks the pre-catalog Blockly POC already builds and generates.
LEGACY_IMPLEMENTED = {
    "program.setup": "arduino_setup",
    "program.loop": "arduino_loop",
    "gpio.pin_mode": "pinmode",
    "gpio.digital_write": "digitalwrite",
    "time.delay": "delay",
}

#: The three blocks the no-device/Blockly-integration correction added,
#: deliberately narrow and distinct from the still-CATALOGED generic
#: `functions.define`/`functions.call`/`logic.if_else` — see
#: `app/blockly/definitions/programming.py`.
CORRECTION_IMPLEMENTED = {
    "functions.implementation": "function_implementation",
    "functions.call_existing": "call_existing_function",
    "logic.if_equals": "if_equals",
}

#: The token-parsing hardening's blocks: the catalog's own GENERIC entries,
#: implemented now that the bridge models nested value inputs, plus one new
#: entry each for `text.index_of` and a value-less `functions.return_void`.
#: Each is one small construct; Panel 1's remediation is composed from them.
TOKEN_PARSING_IMPLEMENTED = {
    "variables.declare": "variables_declare",
    "variables.get": "variables_get",
    "text.literal": "text_literal",
    "text.index_of": "text_index_of",
    "text.substring": "text_substring",
    "text.length": "text_length",
    "math.number": "math_number",
    "math.add": "math_add",
    "logic.equal": "logic_equal",
    "logic.not_equal": "logic_not_equal",
    "logic.less_equal": "logic_less_equal",
    "logic.if": "logic_if",
    "functions.return_void": "return_void",
}

#: P2's generic statement/value blocks: assignment, a call on an object, a call
#: used as a value, and the two boolean literals. (`functions.call_existing` and
#: `variables.declare` above gained argument sockets / qualifier fields.)
P2_IMPLEMENTED = {
    "variables.set": "variables_set",
    "functions.call_method": "call_method",
    "functions.call_value": "call_function_value",
    "logic.true": "logic_true",
    "logic.false": "logic_false",
}

#: P3's control flow and operators: ordering comparisons, `&&`/`||`/`!`, the
#: ternary, a C-style `for`, and postfix `++`/`--`. (`logic.if` above gained the
#: ELSE_IF / ELSE statement inputs.)
P3_IMPLEMENTED = {
    "logic.less": "logic_less",
    "logic.greater": "logic_greater",
    "logic.greater_equal": "logic_greater_equal",
    "logic.and": "logic_and",
    "logic.or": "logic_or",
    "logic.not": "logic_not",
    "logic.ternary": "logic_ternary",
    "loops.for": "for_loop",
    "variables.update": "variables_update",
}

ALL_IMPLEMENTED = {
    **LEGACY_IMPLEMENTED,
    **CORRECTION_IMPLEMENTED,
    **TOKEN_PARSING_IMPLEMENTED,
    **P2_IMPLEMENTED,
    **P3_IMPLEMENTED,
}


def _block(**overrides) -> BlockDefinition:
    fields = dict(
        block_id="gpio.example",
        category_id="inputs",
        display_name="example",
        description="An example block.",
        kind=BlockKind.STATEMENT,
        semantic_operation="gpio.example",
    )
    fields.update(overrides)
    return BlockDefinition(**fields)


def _category(category_id: str = "inputs") -> BlockCategory:
    return BlockCategory(category_id, "Inputs", "Reading pins.", CategoryGroup.ARDUINO, 65)


# --- 1: the catalog loads ---------------------------------------------------


def test_default_catalog_loads() -> None:
    assert isinstance(default_block_catalog, BlockCatalog)
    assert len(default_block_catalog.categories) == len(REQUIRED_CATEGORIES)
    assert len(default_block_catalog) == len(default_block_catalog.blocks) > 0


def test_building_the_catalog_is_repeatable() -> None:
    assert build_default_catalog().blocks == default_block_catalog.blocks


# --- 2-4: identity and referential integrity -------------------------------


def test_category_ids_are_unique_and_ordered_as_the_brief_lists_them() -> None:
    ids = [category.category_id for category in default_block_catalog.categories]
    assert len(ids) == len(set(ids))
    assert tuple(ids) == REQUIRED_CATEGORIES


def test_block_ids_are_unique() -> None:
    ids = [block.block_id for block in default_block_catalog.blocks]
    assert len(ids) == len(set(ids))


def test_every_block_has_a_valid_category() -> None:
    known = {category.category_id for category in default_block_catalog.categories}
    for block in default_block_catalog.blocks:
        assert block.category_id in known, block.block_id


def test_every_category_has_blocks() -> None:
    for category in default_block_catalog.categories:
        assert default_block_catalog.blocks_in(category.category_id), category.category_id


# --- 5: semantic operations -------------------------------------------------


def test_every_block_has_a_stable_namespaced_semantic_operation() -> None:
    for block in default_block_catalog.blocks:
        assert is_qualified_id(block.block_id), block.block_id
        assert is_qualified_id(block.semantic_operation), block.block_id


def test_semantic_operations_are_never_cpp() -> None:
    for block in default_block_catalog.blocks:
        assert not any(char in block.semantic_operation for char in "();{}<>\" ,"), block.block_id


def test_operations_shared_between_blocks_are_intentional() -> None:
    shared = {
        block.block_id: block.semantic_operation
        for block in default_block_catalog.blocks
        if block.semantic_operation != block.block_id
    }
    assert shared == {
        "logic.if_else": "logic.if",
        "logic.else_if": "logic.if",
        "functions.call_value": "functions.call",
        "gpio.pin_mode_input": "gpio.pin_mode",
        "gpio.pin_mode_input_pullup": "gpio.pin_mode",
    }


# --- 6-7: kinds and metadata ------------------------------------------------


def test_block_kinds_are_valid_and_match_their_connections() -> None:
    for block in default_block_catalog.blocks:
        assert isinstance(block.kind, BlockKind), block.block_id
        if block.kind.yields_value:
            assert block.output is not None and block.output is not ValueType.STATEMENTS, block.block_id
        else:
            assert block.output is None, block.block_id
        if block.kind is BlockKind.CONTAINER:
            assert any(item.value_type is ValueType.STATEMENTS for item in block.inputs), block.block_id


def test_required_metadata_is_present() -> None:
    for block in default_block_catalog.blocks:
        assert block.display_name.strip(), block.block_id
        assert block.description.strip(), block.block_id
        assert isinstance(block.status, ImplementationStatus), block.block_id
        assert all(isinstance(item, BlockInput) for item in block.inputs), block.block_id
    for category in default_block_catalog.categories:
        assert category.display_name.strip() and category.description.strip(), category.category_id


@pytest.mark.parametrize("category_id", REQUIRED_CATEGORIES)
def test_every_concept_the_brief_lists_is_cataloged(category_id: str) -> None:
    present = {block.block_id for block in default_block_catalog.blocks_in(category_id)}
    missing = set(REQUIRED_BLOCKS[category_id].split()) - present
    assert not missing, f"{category_id} is missing {sorted(missing)}"


def test_communication_blocks_declare_their_dependencies_and_capabilities() -> None:
    publish = default_block_catalog.block("mqtt.publish")
    assert publish is not None
    assert "pubsubclient" in publish.dependencies
    assert {capability.value for capability in publish.capabilities} == {"wifi", "mqtt"}
    digital_write = default_block_catalog.block("gpio.digital_write")
    assert digital_write is not None and [item.value_type for item in digital_write.inputs][0] is ValueType.PIN


def test_animated_eyes_is_the_only_optional_category() -> None:
    optional = [category.category_id for category in default_block_catalog.categories if category.optional]
    assert optional == ["animated_eyes"]


# --- implementation status --------------------------------------------------


def test_only_the_legacy_and_corrected_blocks_are_implemented() -> None:
    implemented = {
        block.block_id: block.blockly_type
        for block in default_block_catalog.with_status(ImplementationStatus.IMPLEMENTED)
    }
    assert implemented == ALL_IMPLEMENTED
    assert all(
        block.status is ImplementationStatus.CATALOGED
        for block in default_block_catalog.blocks
        if block.block_id not in ALL_IMPLEMENTED
    )


def test_cataloged_blocks_carry_no_blockly_type_or_generator() -> None:
    for block in default_block_catalog.with_status(ImplementationStatus.CATALOGED):
        assert block.blockly_type is None and block.generator_id is None, block.block_id


# --- 8: the toolbox ---------------------------------------------------------


def test_full_toolbox_lists_every_category_in_catalog_order() -> None:
    toolbox = build_toolbox(default_block_catalog)
    assert toolbox["kind"] == "categoryToolbox"
    names = [item["name"] for item in toolbox["contents"] if item["kind"] == "category"]
    assert names == [category.display_name for category in default_block_catalog.categories]


def test_toolbox_only_offers_blocks_blockly_can_build() -> None:
    for hide_empty in (False, True):
        toolbox = build_toolbox(default_block_catalog, hide_empty_categories=hide_empty)
        types = [
            entry["type"]
            for item in toolbox["contents"]
            if item["kind"] == "category"
            for entry in item["contents"]
        ]
        assert sorted(types) == sorted(ALL_IMPLEMENTED.values())


def test_populated_toolbox_hides_empty_categories() -> None:
    toolbox = build_toolbox(default_block_catalog, hide_empty_categories=True)
    categories = [item for item in toolbox["contents"] if item["kind"] == "category"]
    # Category declaration order (app/blockly/categories.py): Logic, Math,
    # Text, Variables and Functions now have IMPLEMENTED blocks too, ahead of
    # the four Arduino categories the legacy POC populated.
    assert [item["name"] for item in categories] == [
        "Logic",
        "Loops",
        "Math",
        "Text",
        "Variables",
        "Functions",
        "Program",
        "Inputs",
        "Outputs",
        "Time",
    ]
    assert all(item["contents"] for item in categories)


def test_toolbox_separates_groups_and_is_json_serialisable() -> None:
    toolbox = build_toolbox(default_block_catalog)
    separators = [index for index, item in enumerate(toolbox["contents"]) if item["kind"] == "sep"]
    assert len(separators) == len({category.group for category in default_block_catalog.categories}) - 1
    json.dumps(toolbox)


def test_toolbox_categories_use_catalog_hues() -> None:
    toolbox = build_toolbox(default_block_catalog)
    hues = {item["name"]: item["colour"] for item in toolbox["contents"] if item["kind"] == "category"}
    assert hues["Program"] == "210" and hues["Time"] == "65"


# --- the catalog rejects malformed definitions ------------------------------


def test_duplicate_category_ids_are_rejected() -> None:
    with pytest.raises(CatalogError, match="duplicate category"):
        BlockCatalog([_category(), _category()], [])


def test_duplicate_block_ids_are_rejected() -> None:
    with pytest.raises(CatalogError, match="duplicate block"):
        BlockCatalog([_category()], [_block(), _block()])


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(CatalogError, match="unknown category"):
        BlockCatalog([_category()], [_block(category_id="nowhere")])


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(CatalogError, match="unknown dependency"):
        BlockCatalog([_category()], [_block(dependencies=("not_a_library",))])


def test_two_blocks_cannot_claim_one_blockly_type() -> None:
    kwargs = dict(status=ImplementationStatus.IMPLEMENTED, blockly_type="shared", generator_id="arduino_cpp:shared")
    with pytest.raises(CatalogError, match="claimed by both"):
        BlockCatalog(
            [_category()],
            [_block(**kwargs), _block(block_id="gpio.other", semantic_operation="gpio.other", **kwargs)],
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"block_id": "digitalWrite"},
        {"block_id": "Gpio.Example"},
        {"semantic_operation": "digitalWrite(pin, HIGH);"},
        {"semantic_operation": "delay"},
        {"display_name": " "},
        {"description": ""},
        {"kind": "statement"},
        {"output": ValueType.NUMBER},
        {"kind": BlockKind.VALUE},
        {"kind": BlockKind.EXPRESSION, "output": ValueType.NUMBER},
        {"kind": BlockKind.CONTAINER},
        {"kind": BlockKind.VALUE, "output": ValueType.NUMBER, "inputs": (BlockInput("DO", ValueType.STATEMENTS),)},
        {"inputs": (BlockInput("PIN", ValueType.PIN), BlockInput("PIN", ValueType.PIN))},
        {"blockly_type": "pinmode"},
        {"status": ImplementationStatus.BLOCKLY_DEFINED},
        {"status": ImplementationStatus.IMPLEMENTED, "blockly_type": "pinmode"},
        {"status": ImplementationStatus.BLOCKLY_DEFINED, "blockly_type": "pinmode", "generator_id": "arduino_cpp:x"},
    ],
)
def test_malformed_block_definitions_are_rejected(overrides: dict) -> None:
    with pytest.raises(CatalogError):
        _block(**overrides)


def test_a_category_hue_must_be_a_valid_hue() -> None:
    with pytest.raises(CatalogError):
        BlockCategory("inputs", "Inputs", "Reading pins.", CategoryGroup.ARDUINO, 360)


# --- generic, not panel-specific --------------------------------------------


def test_no_block_is_tied_to_a_panel_or_scenario() -> None:
    forbidden = ("smart_home", "smart-home", "panel", "scenario", "vulnerab", "exploit")
    for block in default_block_catalog.blocks:
        text = " ".join(
            (block.block_id, block.semantic_operation, block.display_name, block.description)
        ).lower()
        assert not any(word in text for word in forbidden), block.block_id


def test_mqtt_publish_is_plain_publishing_with_no_policy_inputs() -> None:
    publish = default_block_catalog.block("mqtt.publish")
    assert publish is not None
    assert [item.name for item in publish.inputs] == ["TOPIC", "PAYLOAD"]


def test_the_catalog_is_frozen_data() -> None:
    block = default_block_catalog.blocks[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        block.display_name = "changed"  # type: ignore[misc]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_catalog_layer_imports_no_other_application_layer() -> None:
    offenders = []
    for path in sorted(BLOCKLY_DIR.rglob("*.py")):
        for module in _imported_modules(path):
            if module.startswith("app.") and not module.startswith("app.blockly"):
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


# --- the frontend's generated copy ------------------------------------------


def test_catalog_serialises_to_json() -> None:
    data = catalog_to_dict(default_block_catalog)
    assert len(data["blocks"]) == len(default_block_catalog)
    assert json.loads(json.dumps(data)) == data


def test_generated_frontend_module_is_current() -> None:
    if not FRONTEND_MODULE_PATH.parent.parent.exists():
        pytest.skip("frontend sources are not present alongside the backend")
    assert FRONTEND_MODULE_PATH.exists(), "run backend/scripts/export_blockly_catalog.py"
    committed = FRONTEND_MODULE_PATH.read_text(encoding="utf-8")
    assert committed == render_frontend_module(default_block_catalog), (
        "src/blockly/catalog/masterCatalog.generated.js is stale; "
        "run `python scripts/export_blockly_catalog.py` from backend/"
    )
