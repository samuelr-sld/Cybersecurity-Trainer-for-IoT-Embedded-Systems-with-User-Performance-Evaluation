"""The semantic operation vocabulary: what the IR can mean (Phase B3).

    operation id  ->  SemanticOperation  ->  what an OperationStatement may say

ONE EXPLICIT TABLE, NOT A CONDITIONAL CHAIN. `SemanticOperationRegistry` is
the same shape as `app/panels/panels.py`'s `PanelRegistry`,
`app/scenarios/registry.py`'s `ScenarioRegistry` and `app/blockly/catalog.py`'s
`BlockCatalog`: one normalized dict lookup, built once, validated as a set, so
an unknown id is a clean miss rather than a branch nobody wrote.

WHY THIS LAYER OWNS THE OPERATIONS AND THE BLOCK CATALOG DOES NOT. The master
catalog (`app/blockly/`) carries a `semantic_operation` field on every block
and its own README states what that field is: "a stable identifier, never a
C++ fragment", naming an abstract action that "depends on nothing". Until this
phase that field was a reference with no referent — the README says as much
("not through a semantic IR (which does not exist yet)"). B3 creates the
referent. The ids, the parameter names (`PIN`, `MODE`, `VALUE`, `MS`) and the
value types are therefore REUSED VERBATIM from the catalog rather than
reinvented.

They are reused WITHOUT AN IMPORT, in both directions:

  * this package must not import `app.blockly` — a `BlockDefinition` carries
    `blockly_type` and `generator_id`, and pulling that type in would put
    Blockly-specific fields inside the semantic package, which is exactly what
    B3 forbids. Blockly becomes an ADAPTER in B4; it is not a dependency.
  * `app.blockly` must not import this package either — the catalog is
    asserted to import no other `app` layer at all.

So the agreement between the two vocabularies is enforced by the TEST SUITE
(`tests/test_build_semantic.py`), not by a type dependency. That is the
established discipline here, not a new one: `WorkflowStep.command`
(`app/panels/models.py`) names a real command from the global registry the
same way, and `tests/test_panel_packages.py` is what proves the name exists
rather than `app.panels` importing `app.commands`.

A CONTAINER'S BODY IS NOT A PARAMETER. `program.setup`'s Blockly block has a
`DO` input of type STATEMENTS; in the IR that body is structural — it is the
owning `SemanticSection.statements` — so container operations declare no
parameters at all. The conformance test accounts for this explicitly by
comparing only the catalog's non-STATEMENTS inputs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from app.build.semantic.errors import SemanticModelError, UnknownOperationError

#: `namespace.name`, each part lowercase snake_case — the same shape the block
#: catalog's `QUALIFIED_ID_PATTERN` requires of a `semantic_operation`, since
#: these are literally the same identifiers. Restated rather than imported for
#: the dependency reason in the module docstring; a test pins that the two
#: patterns still agree.
QUALIFIED_OPERATION_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"

#: Parameter names are SCREAMING_SNAKE, matching the catalog's input names so
#: `gpio.pin_mode`'s `PIN`/`MODE` mean the same thing on both sides.
_PARAMETER_NAME_PATTERN = r"^[A-Z][A-Z0-9_]*$"


def is_operation_id(value: str) -> bool:
    """True for a well-formed `namespace.name` operation identifier."""
    return isinstance(value, str) and re.match(QUALIFIED_OPERATION_PATTERN, value) is not None


class SemanticType(str, Enum):
    """The coarse type of an operation parameter or a literal value.

    Deliberately a SUBSET of the block catalog's `ValueType`, carrying the
    identical string values for the members it does have. Only the four types
    the supported operations actually use exist; `list`, `any` and the
    catalog's `statements` marker are absent because declaring members no
    operation uses would be inventing vocabulary this phase cannot exercise.
    A later phase adds a member when it adds the operation that needs it.

    `PIN` is its own type for the reason the catalog's README gives: a pin
    assignment is the operand a future exploration policy singles out as
    safely changeable, so it must stay identifiable rather than collapsing
    into an ordinary number.
    """

    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"
    PIN = "pin"


class OperationForm(str, Enum):
    """The syntactic role an operation plays in the IR.

    CONTAINER  owns an ordered statement body and is what a `SemanticSection`
               is identified by (`program.setup`, `program.loop`). Declares no
               parameters — the body is the section's `statements`.
    STATEMENT  performs one action inside a body (`gpio.pin_mode`).
    VALUE      computes a value from its operands (`text.index_of`) and stands
               in an operand slot rather than in a body — the IR form is
               `models.OperationValue`. The only form with a `result` type.
               Added with the first value-producing operations (the string
               queries Panel 1's token-parsing remediation needs); the
               catalog's VALUE/EXPRESSION distinction is not restated, because
               the IR only ever needs "does this yield a value".
    """

    CONTAINER = "container"
    STATEMENT = "statement"
    VALUE = "value"


@dataclass(frozen=True)
class SemanticParameter:
    """One named, typed operand of an operation.

    Order is meaningful: it is the order a source-language recognizer maps
    positional arguments in (`pinMode(pin, mode)` -> `PIN`, `MODE`), and the
    order a future generator emits them in. It is not, however, how an
    argument is ADDRESSED — `OperationStatement` addresses arguments by name,
    so reordering a call's surface syntax could never silently swap operands.
    """

    name: str
    value_type: SemanticType
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.match(_PARAMETER_NAME_PATTERN, self.name):
            raise SemanticModelError(f"invalid parameter name: {self.name!r}")
        if not isinstance(self.value_type, SemanticType):
            raise SemanticModelError(f"parameter {self.name}: invalid type {self.value_type!r}")
        if not isinstance(self.description, str):
            raise SemanticModelError(f"parameter {self.name}: description must be a string")


@dataclass(frozen=True)
class SemanticOperation:
    """One thing the IR can say, independent of any source or editor.

    `operation_id` is the stable, long-term identity — the same string the
    block catalog already names in `semantic_operation`. It is never a C++
    fragment, never a Blockly type, and never derived from either.
    """

    operation_id: str
    form: OperationForm
    parameters: tuple[SemanticParameter, ...] = ()
    description: str = ""
    #: The type a VALUE operation yields; None for every other form.
    result: SemanticType | None = None

    def __post_init__(self) -> None:
        if not is_operation_id(self.operation_id):
            raise SemanticModelError(f"invalid operation id: {self.operation_id!r}")
        if not isinstance(self.form, OperationForm):
            raise SemanticModelError(f"{self.operation_id}: invalid form {self.form!r}")
        if self.form is OperationForm.VALUE:
            if not isinstance(self.result, SemanticType):
                raise SemanticModelError(f"{self.operation_id}: a value operation needs a result type")
            if not self.parameters:
                raise SemanticModelError(f"{self.operation_id}: a value operation needs operands")
        elif self.result is not None:
            raise SemanticModelError(
                f"{self.operation_id}: only a value operation has a result type"
            )
        names = [parameter.name for parameter in self.parameters]
        for parameter in self.parameters:
            if not isinstance(parameter, SemanticParameter):
                raise SemanticModelError(f"{self.operation_id}: not a parameter: {parameter!r}")
        if len(set(names)) != len(names):
            raise SemanticModelError(f"{self.operation_id}: duplicate parameter names")
        if self.form is OperationForm.CONTAINER and self.parameters:
            raise SemanticModelError(
                f"{self.operation_id}: a container's body is its section's statements, "
                "not a parameter"
            )

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """Parameter names in declaration order."""
        return tuple(parameter.name for parameter in self.parameters)

    def parameter(self, name: str) -> SemanticParameter | None:
        """The parameter with this name, or None if this operation has none."""
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        return None


class SemanticOperationRegistry:
    """Every operation the IR can express, indexed by id.

    Closed and validated at construction: a duplicate id is a construction
    error, and `require` raises rather than inventing an operation, so an IR
    can never contain an operation nobody declared.
    """

    def __init__(self, operations: Iterable[SemanticOperation]) -> None:
        indexed: dict[str, SemanticOperation] = {}
        ordered: list[SemanticOperation] = []
        for operation in operations:
            if not isinstance(operation, SemanticOperation):
                raise SemanticModelError(f"not a SemanticOperation: {operation!r}")
            if operation.operation_id in indexed:
                raise SemanticModelError(f"duplicate operation id: {operation.operation_id}")
            indexed[operation.operation_id] = operation
            ordered.append(operation)
        self._index = indexed
        self._operations = tuple(ordered)

    @property
    def operations(self) -> tuple[SemanticOperation, ...]:
        """Every declared operation, in declaration order."""
        return self._operations

    @property
    def operation_ids(self) -> tuple[str, ...]:
        """Every declared operation id, in declaration order."""
        return tuple(operation.operation_id for operation in self._operations)

    def operation(self, operation_id: str) -> SemanticOperation | None:
        """The operation with this id, or None — a clean miss, never a guess."""
        return self._index.get(operation_id)

    def require(self, operation_id: str) -> SemanticOperation:
        """The operation with this id, or `UnknownOperationError`."""
        found = self._index.get(operation_id)
        if found is None:
            raise UnknownOperationError(f"no such semantic operation: {operation_id!r}")
        return found

    def with_form(self, form: OperationForm) -> tuple[SemanticOperation, ...]:
        """Every operation of one form, in declaration order."""
        return tuple(operation for operation in self._operations if operation.form is form)

    def __contains__(self, operation_id: object) -> bool:
        return operation_id in self._index

    def __len__(self) -> int:
        return len(self._operations)


#: Operation ids, named once so the analyzer's recognizer table and the tests
#: refer to the same constants rather than repeating string literals.
PROGRAM_SETUP = "program.setup"
PROGRAM_LOOP = "program.loop"
FUNCTIONS_IMPLEMENTATION = "functions.implementation"
GPIO_PIN_MODE = "gpio.pin_mode"
GPIO_DIGITAL_WRITE = "gpio.digital_write"
TIME_DELAY = "time.delay"
TEXT_INDEX_OF = "text.index_of"
TEXT_SUBSTRING = "text.substring"
TEXT_LENGTH = "text.length"


def build_default_operations() -> SemanticOperationRegistry:
    """The supported subset: the operations that exist end to end today.

    `program.setup`/`program.loop`/`gpio.pin_mode`/`gpio.digital_write`/
    `time.delay` are the five the block catalog originally marked IMPLEMENTED
    — the ones a student could already place in a real Blockly workspace and
    that already reach real C++. `functions.implementation` is the no-device/
    Blockly-integration correction's one addition to this table: it is what
    turns an arbitrary discovered HELPER_FUNCTION or CALLBACK section (a
    firmware's own named routine, not a platform-defined operation) into a
    representable container, the same way `program.setup`/`program.loop`
    already do for the two Arduino calls into a fixed name. It declares no
    parameters — same rule every container follows — because the one thing
    that makes it a *particular* function (its name, return type and C++
    parameter list) is preserved verbatim on the section itself
    (`SemanticSection.signature`), never modelled here: the operation is
    deliberately the SAME for every named function a firmware defines, so the
    panel-agnostic registry never has to learn a panel's own function names.

    `text.index_of`/`text.substring`/`text.length` are the first VALUE
    operations, added for one documented need: Panel 1's remediation parses a
    `"<COMMAND> <TOKEN>"` payload — find the separator, slice the command and
    the token out either side of it. `text.length` is there so `substring` can
    stay a fixed-arity operation (`FROM`, `TO` — the catalog's own inputs)
    rather than growing an optional operand; "the rest of the text" is
    `substring(FROM, length)`. Their ids and operand names are the catalog's
    (`TEXT`/`SEARCH`/`FROM`/`TO`), reused verbatim like every other row here.

    The remaining ~200 catalog entries are intentionally absent. They are
    CATALOGED metadata with no block, no generator and no firmware behind
    them; declaring IR operations for them would assert a capability this
    codebase does not have. Adding one later is adding a table row here, not
    changing any mechanism.
    """
    return SemanticOperationRegistry(
        (
            SemanticOperation(
                operation_id=PROGRAM_SETUP,
                form=OperationForm.CONTAINER,
                description="Runs once at start-up: void setup().",
            ),
            SemanticOperation(
                operation_id=PROGRAM_LOOP,
                form=OperationForm.CONTAINER,
                description="Runs repeatedly forever: void loop().",
            ),
            SemanticOperation(
                operation_id=FUNCTIONS_IMPLEMENTATION,
                form=OperationForm.CONTAINER,
                description=(
                    "The body of an existing named function or callback; its exact "
                    "C++ signature is preserved on the section, not this operation."
                ),
            ),
            SemanticOperation(
                operation_id=GPIO_PIN_MODE,
                form=OperationForm.STATEMENT,
                parameters=(
                    SemanticParameter("PIN", SemanticType.PIN),
                    SemanticParameter(
                        "MODE", SemanticType.TEXT, "OUTPUT, INPUT or INPUT_PULLUP"
                    ),
                ),
                description="Configures a pin as OUTPUT, INPUT or INPUT_PULLUP.",
            ),
            SemanticOperation(
                operation_id=GPIO_DIGITAL_WRITE,
                form=OperationForm.STATEMENT,
                parameters=(
                    SemanticParameter("PIN", SemanticType.PIN),
                    SemanticParameter(
                        "VALUE", SemanticType.BOOLEAN, "HIGH (true) or LOW (false)"
                    ),
                ),
                description="Drives a digital pin HIGH (true) or LOW (false).",
            ),
            SemanticOperation(
                operation_id=TIME_DELAY,
                form=OperationForm.STATEMENT,
                parameters=(SemanticParameter("MS", SemanticType.NUMBER),),
                description="Pauses the program for a number of milliseconds.",
            ),
            SemanticOperation(
                operation_id=TEXT_INDEX_OF,
                form=OperationForm.VALUE,
                parameters=(
                    SemanticParameter("TEXT", SemanticType.TEXT, "the text searched"),
                    SemanticParameter("SEARCH", SemanticType.TEXT, "what to find"),
                ),
                result=SemanticType.NUMBER,
                description="The position of the first SEARCH in TEXT, or -1 when absent.",
            ),
            SemanticOperation(
                operation_id=TEXT_SUBSTRING,
                form=OperationForm.VALUE,
                parameters=(
                    SemanticParameter("TEXT", SemanticType.TEXT, "the text sliced"),
                    SemanticParameter("FROM", SemanticType.NUMBER, "first position, inclusive"),
                    SemanticParameter("TO", SemanticType.NUMBER, "end position, exclusive"),
                ),
                result=SemanticType.TEXT,
                description="The part of TEXT from FROM up to (not including) TO.",
            ),
            SemanticOperation(
                operation_id=TEXT_LENGTH,
                form=OperationForm.VALUE,
                parameters=(SemanticParameter("TEXT", SemanticType.TEXT),),
                result=SemanticType.NUMBER,
                description="The number of characters in TEXT.",
            ),
        )
    )


#: Process-wide default registry. Immutable, so sharing it is safe.
default_semantic_operations = build_default_operations()
