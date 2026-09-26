"""The Blockly -> semantic IR conversion (Phase B5).

    BlocklyProgram                   (models.py — what B4 produced, or what an
          |                           editor hands back in the same shape)
          v
    blockly_to_semantic(program)     (this module)
          |
          v
    SemanticProgram                  (app/build/semantic/ — B3)

THE OTHER HALF OF `adapter.py`, AND NOTHING ELSE. This module reads a
workspace and states what it means. It does not emit C++ (B6), reconstruct a
source file, compile, flash, validate, score, or decide whether a student may
change anything. It is as pure as the forward direction: no filesystem, no
process, no session, no panel, no clock, no generated ids, and the same input
always produces an equal `SemanticProgram`.

THE LOOKUP IS THE CATALOG'S, READ BACKWARDS:

    BlocklyBlock.block_type
          -> the one IMPLEMENTED BlockDefinition whose `blockly_type` equals
             it                                      (app/blockly/catalog.py)
          -> its `semantic_operation`
          -> the SemanticOperation the IR declares for that id

No `"pinmode" -> "gpio.pin_mode"` table exists here, and no Blockly type is
written down in this package at all — `tests/test_build_blockly_bridge.py`
scans every module of the package for one. The catalog already guarantees a
Blockly type is claimed by at most one block, so reading the relation backwards
needs no second index and no disambiguation rule of its own. Everything else a
block's shape must obey — the body's input name, whether it draws fields, which
fields — is read off the same `BlockDefinition`, never off a literal.

WHAT THE WORKSPACE IS TRUSTED FOR, AND WHAT IT IS NOT. It is trusted for
STRUCTURE AND TEXT: block order, body nesting, section ids and field text are
taken exactly as given, because they are the student's document. It is trusted
for nothing else: the meaning of a block comes from the catalog and the IR, a
field's text must be a value its parameter can actually hold, and a block whose
shape contradicts the catalog is reported rather than repaired. B4's input was
an IR this codebase had already validated; B5's input is a document, so it is
checked.

NOTHING IS SILENTLY DROPPED, IN EITHER DIRECTION. A `PreservedSource` comes
back as the `UnsupportedStatement` it was, at the same position in the same
section, with its exact text. The one place the round trip narrows is a
fragment B4 preserved for its OWN reason (`BridgeReason`) rather than B3's:
the IR has no member meaning "understood, but no field could hold it", so
`_IR_REASON_FOR_BRIDGE` maps it to the nearest thing the IR does say. The text
— which is what a later phase reconstructs from — is untouched either way.

ORDER COMES FROM THE BODY, NEVER FROM COORDINATES. A workspace's x/y are
layout, and `to_workspace_state()` assigns them deterministically for exactly
that reason. The order of `BlocklyBlock.body` is the order of the statements,
and it is preserved item for item.
"""

from __future__ import annotations

import re

from app.blockly.catalog import BlockCatalog, default_block_catalog
from app.blockly.models import BlockDefinition, BlockKind, ImplementationStatus, ValueType
from app.build.blockly_bridge.bindings import (
    FieldBinding,
    FieldBindingTable,
    default_field_bindings,
)
from app.build.blockly_bridge.errors import (
    InvalidBlocklyFieldValueError,
    InvalidBlocklyWorkspaceError,
    MissingBlocklyFieldError,
    UnknownBlocklyBlockError,
    UnrepresentableOperationError,
    UnsupportedBlocklyStructureError,
)
from app.build.blockly_bridge.models import (
    BlocklyBlock,
    BlocklyProgram,
    BlocklySection,
    BridgeReason,
    PreservedSource,
)
from app.build.blockly_bridge.structural import (
    ARITHMETIC_BLOCK_IDS,
    BINARY_OPERANDS,
    COMPARISON_BLOCK_IDS,
    DECLARATION_TYPE_TOKENS,
    FUNCTIONS_CALL_EXISTING_BLOCK_ID,
    FUNCTIONS_RETURN_VOID_BLOCK_ID,
    LOGIC_IF_BLOCK_ID,
    LOGIC_IF_EQUALS_BLOCK_ID,
    MATH_NUMBER_BLOCK_ID,
    TEXT_LITERAL_BLOCK_ID,
    VARIABLES_DECLARE_BLOCK_ID,
    VARIABLES_GET_BLOCK_ID,
)
from app.build.semantic import (
    ArithmeticValue,
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    OperationForm,
    OperationStatement,
    OperationValue,
    ReturnStatement,
    SemanticArgument,
    SemanticModelError,
    SemanticOperation,
    SemanticOperationRegistry,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticType,
    SemanticValue,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
    VariableDeclaration,
    default_semantic_operations,
)

#: Operator and type tokens read backwards — the inverses of `structural.py`'s
#: tables, derived rather than restated so the two directions cannot disagree.
_COMPARISON_OPERATOR_FOR = {block_id: operator for operator, block_id in COMPARISON_BLOCK_IDS.items()}
_ARITHMETIC_OPERATOR_FOR = {block_id: operator for operator, block_id in ARITHMETIC_BLOCK_IDS.items()}
_DECLARATION_TYPE_FOR = {token: value_type for value_type, token in DECLARATION_TYPE_TOKENS.items()}

#: A NAME field's token must be a C++ identifier: it is written into firmware
#: verbatim, so anything wider would let a field carry arbitrary source.
_IDENTIFIER_PATTERN = r"[A-Za-z_]\w*"
_NUMBER_PATTERN = r"-?(?:\d+\.\d*|\.\d+|\d+)"

#: What a fragment B4 preserved for its own reason means to the IR.
#:
#: `BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE` says the IR DID understand the
#: statement and the editor could not draw one of its operands. Coming back,
#: that understanding is gone: all the workspace kept is the text, and deriving
#: the statement from it again would mean reading C++, which is B1/B3's job and
#: not this module's. So the fragment returns as unsupported, under the IR's
#: closest existing member — its operand is precisely what had no
#: representation. This is the ONE narrowing in the round trip, it is a reason
#: rather than a text, and `UnsupportedReason` is not extended to hide it: an
#: IR member meaning "B4 could not draw this" would make the IR describe the
#: editor, which is the dependency this whole package exists to avoid.
_IR_REASON_FOR_BRIDGE = {
    BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE: UnsupportedReason.UNSUPPORTED_ARGUMENT,
}


def blockly_to_semantic(
    program: BlocklyProgram,
    *,
    catalog: BlockCatalog = default_block_catalog,
    bindings: FieldBindingTable = default_field_bindings,
    operations: SemanticOperationRegistry = default_semantic_operations,
) -> SemanticProgram:
    """State what `program` means, section for section and item for item.

    Every `BlocklySection` produces exactly one `SemanticSection`, in the same
    order and UNDER THE SAME ID — the id B1 minted, B2 reused as a region id,
    B3 reused as a section id and B4 carried through. It is never regenerated,
    never derived from a display name and never inferred from position.

    `catalog`, `bindings` and `operations` are injectable for the same reason
    they are in `adapter.py`: a test, or a later phase with a different block
    set, converts against its own tables without this module reaching for a
    global.
    """
    if not isinstance(program, BlocklyProgram):
        raise InvalidBlocklyWorkspaceError(
            f"expected a BlocklyProgram, got {type(program).__name__}"
        )
    if not isinstance(catalog, BlockCatalog):
        raise TypeError(f"expected a BlockCatalog, got {type(catalog).__name__}")
    if not isinstance(bindings, FieldBindingTable):
        raise TypeError(f"expected a FieldBindingTable, got {type(bindings).__name__}")
    if not isinstance(operations, SemanticOperationRegistry):
        raise TypeError(f"expected a SemanticOperationRegistry, got {type(operations).__name__}")
    return SemanticProgram(
        sections=tuple(
            _section(section, catalog, bindings, operations) for section in program.sections
        )
    )


def block_definition_for_type(blockly_type: str, catalog: BlockCatalog) -> BlockDefinition:
    """The one implemented catalog block Blockly draws under this type.

    Public because it IS the B5 lookup — `adapter.py`'s
    `block_definition_for()` read the other way. `BlockCatalog` rejects two
    blocks claiming one Blockly type at construction, so a match is unique by
    the catalog's own rule and this function needs no tie-break of its own.

    Only an IMPLEMENTED block qualifies. A CATALOGED entry has no Blockly type
    at all, and a BLOCKLY_DEFINED one is a block with no generator and no IR
    operation behind it, so reading a statement out of it would be claiming a
    meaning nothing in this codebase has defined.
    """
    for block in catalog.blocks:
        if block.blockly_type != blockly_type:
            continue
        if block.status is not ImplementationStatus.IMPLEMENTED:
            raise UnknownBlocklyBlockError(
                f"{blockly_type}: {block.block_id} is {block.status.value}, not implemented — "
                "it cannot be drawn, so it cannot be read back"
            )
        return block
    raise UnknownBlocklyBlockError(f"{blockly_type}: no catalog block draws this Blockly type")


# --- sections ---------------------------------------------------------------


def _section(
    section: BlocklySection,
    catalog: BlockCatalog,
    bindings: FieldBindingTable,
    operations: SemanticOperationRegistry,
) -> SemanticSection:
    if not isinstance(section, BlocklySection):  # pragma: no cover - the model forbids it
        raise InvalidBlocklyWorkspaceError(f"not a BlocklySection: {section!r}")
    if section.block is None:
        # No container block, so the IR established no container form for this
        # construct either. Its items are preserved source and stay that way —
        # a section with no operation cannot hold an understood statement, a
        # rule `SemanticSection` enforces and this direction has no reason to
        # test against.
        return SemanticSection(
            section_id=section.section_id,
            operation=None,
            statements=tuple(_unsupported(item, section.section_id) for item in section.preserved),
        )

    block = section.block
    definition = block_definition_for_type(block.block_type, catalog)
    operation = _operation_of(definition, operations)
    _require_form(definition, operation, BlockKind.CONTAINER, OperationForm.CONTAINER)
    if block.fields or block.values:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: a container takes no operands, but this block draws "
            f"{[item.name for item in (*block.fields, *block.values)]}"
        )
    _require_body_input(block, definition)
    return SemanticSection(
        section_id=section.section_id,
        operation=operation,
        statements=tuple(
            _statement(item, section.section_id, catalog, bindings, operations)
            for item in block.body
        ),
        # The exact inverse of `adapter.py`'s `container_signature=` — present
        # only for an in-process `BlocklyBlock` (never for one parsed off the
        # wire by `workspace_state.py`, which carries no such field). A REAL
        # section edit still needs `section_blockly.py::program_with_section`
        # to copy the CURRENT section's signature across for that reason.
        signature=block.container_signature,
    )


# --- statements -------------------------------------------------------------


def _statement(
    item: BlocklyBlock | PreservedSource,
    section_id: str,
    catalog: BlockCatalog,
    bindings: FieldBindingTable,
    operations: SemanticOperationRegistry,
) -> SemanticStatement:
    """One body item as the statement it stands for, block or preserved text."""
    if isinstance(item, PreservedSource):
        return _unsupported(item, section_id)
    if not isinstance(item, BlocklyBlock):  # pragma: no cover - the model forbids it
        raise InvalidBlocklyWorkspaceError(f"{section_id}: not a body item: {item!r}")

    definition = block_definition_for_type(item.block_type, catalog)
    if definition.kind.yields_value:
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: {definition.block_id} yields a value; it belongs in a "
            "slot, not on its own in a statement body"
        )
    if definition.block_id == LOGIC_IF_BLOCK_ID:
        return _if_statement(item, definition, section_id, catalog, bindings, operations)
    if definition.block_id == VARIABLES_DECLARE_BLOCK_ID:
        return _declare_statement(item, definition, catalog, operations)
    if definition.block_id == FUNCTIONS_RETURN_VOID_BLOCK_ID:
        _require_no_operands(item, definition, fields=())
        if item.body or item.body_input is not None:
            raise UnsupportedBlocklyStructureError(
                f"{item.block_type}: {definition.block_id} declares no statement body"
            )
        return ReturnStatement(text=item.source_text)
    if item.values:
        # No other statement block has a value slot; reading one as if it
        # did would invent an operand the catalog does not declare for it.
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: {definition.block_id} takes no value inputs, but this "
            f"block carries {[value.name for value in item.values]}"
        )
    if definition.block_id == FUNCTIONS_CALL_EXISTING_BLOCK_ID:
        return _call_existing_statement(item, definition, section_id)
    if definition.block_id == LOGIC_IF_EQUALS_BLOCK_ID:
        return _if_equals_statement(item, definition, section_id, catalog, bindings, operations)

    operation = _operation_of(definition, operations)
    _require_form(definition, operation, BlockKind.STATEMENT, OperationForm.STATEMENT)
    if item.body or item.body_input is not None:
        # No OTHER implemented block nests a body inside a statement — the
        # two above are handled first, above. Reading one as if it did would
        # invent a structure the catalog does not declare for it; the first
        # block that nests one declares it there first.
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: {definition.block_id} declares no statement body, "
            "but this block carries one"
        )

    fields = bindings.fields_for(operation.operation_id)
    if fields is None:
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: no field binding declares how {item.block_type}'s "
            "fields reach its operands"
        )
    _check_operands_agree(operation, definition, fields)
    return OperationStatement(
        operation=operation,
        arguments=_arguments(item, operation, fields),
        # Provenance, passed straight through in whichever state it is in. A
        # block read from firmware carries the source B4 recorded; a block
        # authored in the editor carries none, and the statement it means has
        # none either. Writing one HERE would be generating C++ from a
        # workspace, which is still not this module's job — the difference
        # since B6 is that a source-less statement is now a complete, usable
        # statement rather than a dead end, because the generator writes its
        # C++ from its meaning. See `app/build/semantic/generator.py`.
        text=item.source_text,
    )


def _call_existing_statement(
    item: BlocklyBlock, definition: BlockDefinition, section_id: str
) -> CallStatement:
    """A `functions.call_existing` block, read back as a `CallStatement`.

    Bypasses `FieldBindingTable`/`_arguments`, which are shaped around a
    fixed-arity platform operation; this block has exactly one field, NAME,
    holding an arbitrary function name, not an operand of a registered
    operation.
    """
    if item.body or item.body_input is not None:
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: {definition.block_id} declares no statement body, "
            "but this block carries one"
        )
    fields = {field.name: field.value for field in item.fields}
    name = fields.pop("NAME", None)
    if name is None:
        raise MissingBlocklyFieldError(
            f"{item.block_type}: no NAME field, which {definition.block_id} needs"
        )
    if fields:
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: field(s) {sorted(fields)} belong to no operand of "
            f"{definition.block_id}"
        )
    try:
        return CallStatement(function_name=name, text=item.source_text)
    except ValueError as error:
        raise InvalidBlocklyFieldValueError(f"{item.block_type}.NAME: {error}") from error


def _if_equals_statement(
    item: BlocklyBlock,
    definition: BlockDefinition,
    section_id: str,
    catalog: BlockCatalog,
    bindings: FieldBindingTable,
    operations: SemanticOperationRegistry,
) -> ConditionalStatement:
    """A `logic.if_equals` block, read back as a `ConditionalStatement`.

    LEFT is always read as a named reference and RIGHT as a fixed text value
    — the exact inverse of `adapter.py::_if_equals_block`'s narrow shape, not
    a general expression reader. The body is read through the SAME
    `_statement` this function is itself a case of, so a nested `if_equals`
    or `call_existing` block inside this one's DO works without either
    function knowing the other exists.
    """
    fields = {field.name: field.value for field in item.fields}
    left = fields.pop("LEFT", None)
    operator = fields.pop("OPERATOR", None)
    right = fields.pop("RIGHT", None)
    if left is None or operator is None or right is None:
        raise MissingBlocklyFieldError(
            f"{item.block_type}: needs LEFT, OPERATOR and RIGHT fields, which "
            f"{definition.block_id} declares"
        )
    if fields:
        raise UnsupportedBlocklyStructureError(
            f"{item.block_type}: field(s) {sorted(fields)} belong to no operand of "
            f"{definition.block_id}"
        )
    if operator not in ("==", "!="):
        raise InvalidBlocklyFieldValueError(
            f"{item.block_type}.OPERATOR: {operator!r} is not '==' or '!='"
        )
    if not re.fullmatch(_IDENTIFIER_PATTERN, left):
        # LEFT is written into firmware verbatim as a name; anything that is
        # not an identifier would be arbitrary source riding in a field.
        raise InvalidBlocklyFieldValueError(
            f"{item.block_type}.LEFT: {left!r} is not a variable name"
        )
    try:
        condition = ComparisonValue(
            left=SymbolValue(name=left),
            operator=operator,
            right=LiteralValue(value=right, value_type=SemanticType.TEXT),
        )
    except ValueError as error:
        raise InvalidBlocklyFieldValueError(f"{item.block_type}: {error}") from error

    _require_body_input(item, definition)
    body = tuple(
        _statement(child, section_id, catalog, bindings, operations) for child in item.body
    )
    try:
        return ConditionalStatement(condition=condition, body=body, text=item.source_text)
    except ValueError as error:
        raise InvalidBlocklyFieldValueError(f"{item.block_type}: {error}") from error


def _if_statement(
    item: BlocklyBlock,
    definition: BlockDefinition,
    section_id: str,
    catalog: BlockCatalog,
    bindings: FieldBindingTable,
    operations: SemanticOperationRegistry,
) -> ConditionalStatement:
    """A generic `logic.if` block: a comparison in CONDITION, a body in DO.

    The condition must be a COMPARISON block. The IR could state a bare
    boolean condition, but `analyzer.py` reads only a comparison back out of
    an `if` head — so accepting anything else here would write firmware whose
    next reading comes back as opaque source, which the security region's
    full-ownership rule would then refuse to let the student edit again.
    """
    _require_no_operands(item, definition, fields=(), slots=("CONDITION",))
    slot = item.value("CONDITION")
    if slot is None:
        raise MissingBlocklyFieldError(
            f"{item.block_type}: its CONDITION slot is empty, which {definition.block_id} "
            "needs"
        )
    condition = _value_of(slot, section_id, catalog, operations)
    if not isinstance(condition, ComparisonValue):
        raise InvalidBlocklyFieldValueError(
            f"{item.block_type}.CONDITION: an if condition must be a comparison block, "
            f"got {slot.block_id}"
        )
    _require_body_input(item, definition)
    body = tuple(
        _statement(child, section_id, catalog, bindings, operations) for child in item.body
    )
    return ConditionalStatement(condition=condition, body=body, text=item.source_text)


def _declare_statement(
    item: BlocklyBlock,
    definition: BlockDefinition,
    catalog: BlockCatalog,
    operations: SemanticOperationRegistry,
) -> VariableDeclaration:
    """A `variables.declare` block: TYPE and NAME fields, an INITIAL value."""
    _require_no_operands(item, definition, fields=("TYPE", "NAME"), slots=("INITIAL",))
    fields = {field.name: field.value for field in item.fields}
    type_token, name = fields.get("TYPE"), fields.get("NAME")
    if type_token is None or name is None:
        raise MissingBlocklyFieldError(
            f"{item.block_type}: needs TYPE and NAME fields, which {definition.block_id} declares"
        )
    value_type = _DECLARATION_TYPE_FOR.get(type_token)
    if value_type is None:
        raise InvalidBlocklyFieldValueError(
            f"{item.block_type}.TYPE: {type_token!r} is not one of "
            f"{sorted(_DECLARATION_TYPE_FOR)}"
        )
    slot = item.value("INITIAL")
    if slot is None:
        raise MissingBlocklyFieldError(
            f"{item.block_type}: {name} needs an initial value in its INITIAL slot"
        )
    initializer = _value_of(slot, item.block_type, catalog, operations)
    try:
        return VariableDeclaration(
            value_type=value_type, name=name, initializer=initializer, text=item.source_text
        )
    except SemanticModelError as error:
        raise InvalidBlocklyFieldValueError(f"{item.block_type}: {error}") from error


def _value_of(
    block: BlocklyBlock,
    section_id: str,
    catalog: BlockCatalog,
    operations: SemanticOperationRegistry,
) -> SemanticValue:
    """One value block, read back as the IR value it stands for.

    The exact inverse of `adapter.py::_value_block`. Structural value blocks
    are found by block id (`structural.py`); any other value block must name a
    VALUE operation the IR declares, whose operands hang from the catalog
    block's input slots and are read through this same function — so an
    `indexOf` inside a `+` inside a `substring` needs no special case.
    """
    definition = block_definition_for_type(block.block_type, catalog)
    if not definition.kind.yields_value:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: {definition.block_id} is a {definition.kind.value}; only a "
            "value block can fill a slot"
        )
    if block.body or block.body_input is not None:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: a value block carries no statement body"
        )
    block_id = definition.block_id
    fields = {field.name: field.value for field in block.fields}

    if block_id == TEXT_LITERAL_BLOCK_ID:
        _require_no_operands(block, definition, fields=("VALUE",))
        return LiteralValue(value=_field(block, fields, "VALUE"), value_type=SemanticType.TEXT)
    if block_id == MATH_NUMBER_BLOCK_ID:
        _require_no_operands(block, definition, fields=("VALUE",))
        text = _field(block, fields, "VALUE").strip()
        if not re.fullmatch(_NUMBER_PATTERN, text):
            raise InvalidBlocklyFieldValueError(f"{block.block_type}.VALUE: {text!r} is not a number")
        number = float(text) if "." in text else int(text)
        return LiteralValue(value=number, value_type=SemanticType.NUMBER)
    if block_id == VARIABLES_GET_BLOCK_ID:
        _require_no_operands(block, definition, fields=("NAME",))
        name = _field(block, fields, "NAME")
        if not re.fullmatch(_IDENTIFIER_PATTERN, name):
            raise InvalidBlocklyFieldValueError(
                f"{block.block_type}.NAME: {name!r} is not a variable name"
            )
        return SymbolValue(name=name)

    binary = _COMPARISON_OPERATOR_FOR.get(block_id) or _ARITHMETIC_OPERATOR_FOR.get(block_id)
    if binary is not None:
        _require_no_operands(block, definition, fields=(), slots=BINARY_OPERANDS)
        left, right = (
            _value_of(_slot(block, name), section_id, catalog, operations)
            for name in BINARY_OPERANDS
        )
        kind = ComparisonValue if block_id in _COMPARISON_OPERATOR_FOR else ArithmeticValue
        try:
            return kind(left=left, operator=binary, right=right)
        except SemanticModelError as error:
            raise InvalidBlocklyFieldValueError(f"{block.block_type}: {error}") from error

    operation = _operation_of(definition, operations)
    if operation.form is not OperationForm.VALUE:
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: the catalog calls {block_id} a value, the IR calls "
            f"it a {operation.form.value}"
        )
    slots = tuple(
        item.name for item in definition.inputs if item.value_type is not ValueType.STATEMENTS
    )
    if slots != operation.parameter_names:
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: IR parameters {list(operation.parameter_names)} do not "
            f"match {block_id}'s inputs {list(slots)}"
        )
    _require_no_operands(block, definition, fields=(), slots=slots)
    arguments = tuple(
        SemanticArgument(
            name=name,
            value=_value_of(_slot(block, name), section_id, catalog, operations),
        )
        for name in slots
    )
    try:
        return OperationValue(operation=operation, arguments=arguments)
    except SemanticModelError as error:
        raise InvalidBlocklyFieldValueError(f"{block.block_type}: {error}") from error


def _slot(block: BlocklyBlock, name: str) -> BlocklyBlock:
    found = block.value(name)
    if found is None:
        raise MissingBlocklyFieldError(f"{block.block_type}: its {name} slot is empty")
    return found


def _field(block: BlocklyBlock, fields: dict[str, str], name: str) -> str:
    value = fields.get(name)
    if value is None:
        raise MissingBlocklyFieldError(f"{block.block_type}: no {name} field")
    return value


def _require_no_operands(
    block: BlocklyBlock,
    definition: BlockDefinition,
    *,
    fields: tuple[str, ...],
    slots: tuple[str, ...] = (),
) -> None:
    """Refuse any field or slot this block's reader does not consume.

    A workspace is untrusted input, so a field or input the catalog block has
    no use for is reported rather than silently ignored — the same rule
    `_arguments` applies to a registered operation's fields.
    """
    extra_fields = sorted({field.name for field in block.fields} - set(fields))
    extra_slots = sorted({value.name for value in block.values} - set(slots))
    if extra_fields or extra_slots:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: {sorted(extra_fields + extra_slots)} belong to no operand "
            f"of {definition.block_id}"
        )


def _arguments(
    block: BlocklyBlock, operation: SemanticOperation, fields: tuple[FieldBinding, ...]
) -> tuple[SemanticArgument, ...]:
    """The block's fields as the operation's operands, in PARAMETER order.

    Fields are matched BY NAME, never by position, for the reason
    `app/build/semantic/operations.py` gives: addressing an operand by where it
    happens to sit is how two operands of the same shape get silently swapped.
    The arguments come out in the operation's declared order whatever order the
    block lists its fields in.
    """
    written = {field.name: field.value for field in block.fields}
    arguments: list[SemanticArgument] = []
    for binding in fields:
        text = written.pop(binding.input_name, None)
        if text is None:
            raise MissingBlocklyFieldError(
                f"{block.block_type}: no {binding.input_name} field, which "
                f"{operation.operation_id} needs for its {binding.input_name} operand"
            )
        value = binding.parse(text)
        if value is None:
            raise InvalidBlocklyFieldValueError(
                f"{block.block_type}.{binding.input_name}: {text!r} is not a value a "
                f"{binding.kind.value} field can hold"
            )
        parameter = operation.parameter(binding.input_name)
        assert parameter is not None  # guaranteed by the agreement check
        if not value.fits(parameter.value_type):
            raise InvalidBlocklyFieldValueError(
                f"{block.block_type}.{binding.input_name}: {text!r} does not fit a "
                f"{parameter.value_type.value} operand"
            )
        arguments.append(SemanticArgument(name=binding.input_name, value=value))
    if written:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: field(s) {sorted(written)} belong to no operand of "
            f"{operation.operation_id}"
        )
    return tuple(arguments)


def _unsupported(item: PreservedSource, section_id: str) -> UnsupportedStatement:
    """A preserved fragment as the unsupported statement it stands for.

    The text is carried through untouched — not re-parsed, not reformatted,
    not trimmed. See `_IR_REASON_FOR_BRIDGE` for the one thing that narrows.
    """
    if not isinstance(item, PreservedSource):  # pragma: no cover - the model forbids it
        raise InvalidBlocklyWorkspaceError(f"{section_id}: not preserved source: {item!r}")
    reason = item.reason
    if isinstance(reason, BridgeReason):
        reason = _IR_REASON_FOR_BRIDGE[reason]
    return UnsupportedStatement(text=item.text, reason=reason)


# --- the three tables must agree --------------------------------------------


def _operation_of(
    definition: BlockDefinition, operations: SemanticOperationRegistry
) -> SemanticOperation:
    """The IR operation this catalog block names, or a loud disagreement.

    An implemented block whose `semantic_operation` the IR does not declare is
    drift between two tables, exactly like `adapter.py`'s missing-block case,
    and it cannot be caused by a workspace — so it raises rather than becoming
    a preserved fragment.
    """
    operation = operations.operation(definition.semantic_operation)
    if operation is None:
        raise UnrepresentableOperationError(
            f"{definition.block_id}: the IR declares no operation "
            f"{definition.semantic_operation!r} for this implemented block"
        )
    return operation


def _require_form(
    definition: BlockDefinition,
    operation: SemanticOperation,
    kind: BlockKind,
    form: OperationForm,
) -> None:
    """The block must play the role its position in the workspace implies."""
    if definition.kind is not kind:
        raise UnsupportedBlocklyStructureError(
            f"{definition.blockly_type}: the catalog calls {definition.block_id} a "
            f"{definition.kind.value}, but it is used here as a {kind.value}"
        )
    if operation.form is not form:  # pragma: no cover - the catalog and IR agree today
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: the IR calls this a {operation.form.value}, the "
            f"catalog calls {definition.block_id} a {definition.kind.value}"
        )


def _require_body_input(block: BlocklyBlock, definition: BlockDefinition) -> None:
    """A body must hang from the input name the CATALOG declares for it.

    Not from a constant. The catalog block owns the name of its statement
    input, and renaming it there is a change this module follows rather than
    one that breaks it.
    """
    body_inputs = [item for item in definition.inputs if item.value_type is ValueType.STATEMENTS]
    if len(body_inputs) != 1:  # pragma: no cover - a container declares exactly one
        raise UnrepresentableOperationError(
            f"{definition.block_id}: needs exactly one statement body input, "
            f"it has {len(body_inputs)}"
        )
    declared = body_inputs[0].name
    if block.body_input is not None and block.body_input != declared:
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: its body hangs from {block.body_input!r}, but "
            f"{definition.block_id} declares {declared!r}"
        )
    if block.body and block.body_input is None:  # pragma: no cover - the model forbids it
        raise UnsupportedBlocklyStructureError(
            f"{block.block_type}: a body needs the input name it hangs from"
        )


def _check_operands_agree(
    operation: SemanticOperation,
    definition: BlockDefinition,
    fields: tuple[FieldBinding, ...],
) -> None:
    """The IR, the catalog and the binding table must describe one shape.

    The same check `adapter.py` makes on every forward conversion, made again
    here for the same reason: the tables are injectable, and drift between them
    must surface as an error rather than as a statement with a missing or
    misnamed operand.
    """
    parameters = operation.parameter_names
    catalog_inputs = tuple(
        item.name for item in definition.inputs if item.value_type is not ValueType.STATEMENTS
    )
    bound = tuple(binding.input_name for binding in fields)
    if parameters != catalog_inputs:
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: IR parameters {list(parameters)} do not match "
            f"{definition.block_id}'s inputs {list(catalog_inputs)}"
        )
    if parameters != bound:
        raise UnrepresentableOperationError(
            f"{operation.operation_id}: IR parameters {list(parameters)} do not match its "
            f"field bindings {list(bound)}"
        )
