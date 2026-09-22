"""Passive data model for the master Blockly block catalog.

Frozen, validated dataclasses and enums — no behaviour beyond checking their
own shape, the same split `app/build/models.py` and `app/panels/models.py`
use. Cross-definition rules (unique ids, known categories, known dependency
ids) belong to `app/blockly/catalog.py`, which sees the whole set.

A `BlockDefinition` is the platform-level answer to "what programming concept
does this block stand for?". It is deliberately not Blockly JSON: it names an
abstract `semantic_operation` (`gpio.digital_write`, `mqtt.publish`, ...) that
is independent of how Blockly draws the block and of what C++ eventually
comes out of it. `blockly_type` and `generator_id` are the only places the
catalog touches those two neighbours, and both are optional until a block has
actually been built.

`semantic_operation` is a stable identifier, never a C++ fragment. Several
blocks may share one operation (an `if` and an `if/else` are one operation
with an optional branch), which is why it is a separate field from `block_id`
even though the two are equal for most blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: `namespace.name`, each part lowercase snake_case. Used for both block ids
#: and semantic operation ids, which are long-term contract identifiers.
QUALIFIED_ID_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
_SIMPLE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
_INPUT_NAME_PATTERN = r"^[A-Z][A-Z0-9_]*$"
_BLOCKLY_TYPE_PATTERN = r"^[a-z][a-z0-9_]*$"
_GENERATOR_ID_PATTERN = r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.]+$"


def is_qualified_id(value: str) -> bool:
    """True for a `namespace.name` identifier (block ids, semantic operations)."""
    return re.match(QUALIFIED_ID_PATTERN, value) is not None


class CatalogError(ValueError):
    """The catalog, or a definition in it, breaks a structural rule."""


class BlockKind(str, Enum):
    """The syntactic role a block plays.

    VALUE      a literal or named reference (`true`, a number, a variable
               read): no computation of its own.
    EXPRESSION an operation over inputs that yields a value (`a + b`, a
               hardware read that takes a pin). Needs at least one input.
    STATEMENT  performs an action and chains to the next block; it may still
               nest bodies (`if`, `repeat`).
    CONTAINER  a hat/definition block that holds a statement stack and does
               not chain (`setup`, `loop`, a function definition, a callback).
    """

    VALUE = "value"
    EXPRESSION = "expression"
    STATEMENT = "statement"
    CONTAINER = "container"

    @property
    def yields_value(self) -> bool:
        return self in (BlockKind.VALUE, BlockKind.EXPRESSION)


class ValueType(str, Enum):
    """Coarse type of a block input or output.

    Catalog-level typing, not a type checker: it says what a block logically
    consumes and produces so a later layer can reason about it. `PIN` is its
    own type on purpose — a pin assignment is the thing a future exploration
    policy will single out as safely changeable. `STATEMENTS` marks a nested
    statement body and is never a legal output type.
    """

    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"
    LIST = "list"
    PIN = "pin"
    ANY = "any"
    STATEMENTS = "statements"


class Capability(str, Enum):
    """What a board or attached panel must offer for a block to make sense.

    The hook a later phase uses to filter blocks by hardware (a panel with no
    display never offers display blocks). Purely descriptive here.
    """

    GPIO = "gpio"
    ANALOG_INPUT = "analog_input"
    PWM_OUTPUT = "pwm_output"
    SERIAL = "serial"
    I2C = "i2c"
    SPI = "spi"
    WIFI = "wifi"
    MQTT = "mqtt"
    HTTP = "http"
    BLUETOOTH = "bluetooth"
    ESP_NOW = "esp_now"
    FILESYSTEM = "filesystem"
    DISPLAY = "display"
    NEOPIXEL = "neopixel"
    MOTOR_DRIVER = "motor_driver"
    SERVO = "servo"


class ImplementationStatus(str, Enum):
    """How far a block has progressed from an idea to working firmware.

    CATALOGED          metadata only. No Blockly block exists; it cannot
                       appear in a workspace or toolbox.
    BLOCKLY_DEFINED    a Blockly block type exists and can be dragged out.
                       It emits no code and has no semantic definition.
    SEMANTIC_DEFINED   reserved for the semantic-IR phase: the operation has a
                       real typed definition in the IR. Not yet populated.
    GENERATOR_PENDING  reserved: Blockly block and semantic operation both
                       exist, only the C++ generator is missing.
    IMPLEMENTED        end to end today: the block exists and a generator
                       turns it into real Arduino C++.

    Only CATALOGED and IMPLEMENTED are in use in Phase B0.1. The three middle
    values are the vocabulary later phases advance blocks through; they are
    listed now so the field never has to change meaning. An IMPLEMENTED block
    today reaches C++ directly from Blockly — it predates the semantic IR and
    does not pass through it.
    """

    CATALOGED = "cataloged"
    BLOCKLY_DEFINED = "blockly_defined"
    SEMANTIC_DEFINED = "semantic_defined"
    GENERATOR_PENDING = "generator_pending"
    IMPLEMENTED = "implemented"


class CategoryGroup(str, Enum):
    """The top-level grouping a category sits under in a toolbox."""

    PROGRAMMING = "programming"
    ARDUINO = "arduino"
    COMMUNICATION = "communication"
    HARDWARE = "hardware"
    STORAGE = "storage"


class DependencyProvider(str, Enum):
    """Where a dependency's header comes from."""

    ESP32_CORE = "esp32_core"
    ARDUINO_LIBRARY = "arduino_library"


@dataclass(frozen=True)
class LibraryDependency:
    """A header/library a block's eventual C++ needs, by stable id."""

    dependency_id: str
    display_name: str
    header: str
    provider: DependencyProvider

    def __post_init__(self) -> None:
        if not re.match(_SIMPLE_ID_PATTERN, self.dependency_id):
            raise CatalogError(f"invalid dependency id: {self.dependency_id!r}")
        if not self.display_name.strip() or not self.header.strip():
            raise CatalogError(f"dependency {self.dependency_id} needs a name and header")


@dataclass(frozen=True)
class BlockInput:
    """One logical operand of a block: a value slot, a field, or a body."""

    name: str
    value_type: ValueType
    description: str = ""

    def __post_init__(self) -> None:
        if not re.match(_INPUT_NAME_PATTERN, self.name):
            raise CatalogError(f"invalid input name: {self.name!r}")
        if not isinstance(self.value_type, ValueType):
            raise CatalogError(f"input {self.name} has an invalid value type")


@dataclass(frozen=True)
class BlockCategory:
    """A toolbox category. `hue` is a presentation hint (Blockly hue, 0-359).

    `optional` marks a specialised category a panel is not expected to
    expose by default.
    """

    category_id: str
    display_name: str
    description: str
    group: CategoryGroup
    hue: int
    optional: bool = False

    def __post_init__(self) -> None:
        if not re.match(_SIMPLE_ID_PATTERN, self.category_id):
            raise CatalogError(f"invalid category id: {self.category_id!r}")
        if not self.display_name.strip() or not self.description.strip():
            raise CatalogError(f"category {self.category_id} needs a name and description")
        if not isinstance(self.group, CategoryGroup):
            raise CatalogError(f"category {self.category_id} has an invalid group")
        if not 0 <= self.hue < 360:
            raise CatalogError(f"category {self.category_id} hue out of range: {self.hue}")


@dataclass(frozen=True)
class BlockDefinition:
    """One block concept in the master catalog. See the module docstring."""

    block_id: str
    category_id: str
    display_name: str
    description: str
    kind: BlockKind
    semantic_operation: str
    inputs: tuple[BlockInput, ...] = ()
    output: ValueType | None = None
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    status: ImplementationStatus = ImplementationStatus.CATALOGED
    blockly_type: str | None = None
    generator_id: str | None = None

    def __post_init__(self) -> None:
        name = self.block_id
        if not is_qualified_id(name):
            raise CatalogError(f"invalid block id: {name!r}")
        if not is_qualified_id(self.semantic_operation):
            raise CatalogError(f"{name}: invalid semantic operation {self.semantic_operation!r}")
        if not self.display_name.strip() or not self.description.strip():
            raise CatalogError(f"{name}: needs a display name and description")
        if not isinstance(self.kind, BlockKind):
            raise CatalogError(f"{name}: invalid kind {self.kind!r}")
        if not isinstance(self.status, ImplementationStatus):
            raise CatalogError(f"{name}: invalid status {self.status!r}")
        self._check_shape()
        self._check_implementation()

    def _check_shape(self) -> None:
        name = self.block_id
        input_names = [item.name for item in self.inputs]
        if len(set(input_names)) != len(input_names):
            raise CatalogError(f"{name}: duplicate input names")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise CatalogError(f"{name}: duplicate dependencies")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise CatalogError(f"{name}: duplicate capabilities")
        has_body = any(item.value_type is ValueType.STATEMENTS for item in self.inputs)
        if self.kind.yields_value:
            if self.output is None or self.output is ValueType.STATEMENTS:
                raise CatalogError(f"{name}: a {self.kind.value} block needs a value output")
            if has_body:
                raise CatalogError(f"{name}: a {self.kind.value} block cannot hold a statement body")
            if self.kind is BlockKind.EXPRESSION and not self.inputs:
                raise CatalogError(f"{name}: an expression needs at least one input")
        else:
            if self.output is not None:
                raise CatalogError(f"{name}: a {self.kind.value} block cannot have a value output")
            if self.kind is BlockKind.CONTAINER and not has_body:
                raise CatalogError(f"{name}: a container needs a statement body input")

    def _check_implementation(self) -> None:
        name = self.block_id
        if self.status is ImplementationStatus.CATALOGED:
            if self.blockly_type is not None or self.generator_id is not None:
                raise CatalogError(f"{name}: a cataloged block has no Blockly type or generator")
            return
        if self.blockly_type is None or not re.match(_BLOCKLY_TYPE_PATTERN, self.blockly_type):
            raise CatalogError(f"{name}: {self.status.value} needs a valid Blockly type")
        implemented = self.status is ImplementationStatus.IMPLEMENTED
        if implemented != (self.generator_id is not None):
            raise CatalogError(f"{name}: a generator id exists exactly when status is implemented")
        if self.generator_id is not None and not re.match(_GENERATOR_ID_PATTERN, self.generator_id):
            raise CatalogError(f"{name}: invalid generator id {self.generator_id!r}")
