"""The semantic IR -> Blockly conversion (Phase B4).

    SemanticProgram          (app/build/semantic/ — B3)
          |
          v
    program_to_blockly(program)        (this module)
          |
          v
    BlocklyProgram -> to_workspace_state() -> Blockly

ONE WAY ONLY. This module is the whole of B4's direction of travel and only
that direction: the way back is `reverse.py` (B5), which reads a workspace
without this module's help. There is no Blockly -> C++ path, no IR -> C++ path
and no source reconstruction anywhere in the package; those are B6, and the
existing frontend generator (`src/blockly/arduinoGenerator.js`) that already
turns the five blocks into real C++ is left exactly as it is.

THE LOOKUP IS THE CATALOG'S, NOT A SECOND REGISTRY:

    SemanticOperation.operation_id
          -> the one IMPLEMENTED BlockDefinition whose `semantic_operation`
             equals it                              (app/blockly/catalog.py)
          -> its `blockly_type`

`gpio.pin_mode` is named by three catalog blocks (`gpio.pin_mode`,
`gpio.pin_mode_input`, `gpio.pin_mode_input_pullup`) because several blocks
may share one operation — the catalog's own docstring says so. Only one of
them is IMPLEMENTED, and an implemented block is by definition the only one
Blockly can actually draw, so filtering on status is what makes the lookup
single-valued without this module knowing any block id. No
`"gpio.digital_write" -> "digitalwrite"` table exists anywhere in this
package.

TWO KINDS OF "CANNOT", KEPT APART. This distinction is the design:

  * ORDINARY SOURCE VARIANCE — a statement the IR understood whose operand no
    real field can hold (`delay(BUZZER_CHIRP_MS)`: a named constant, a
    `FieldNumber`). Nothing is wrong with the platform, so nothing is raised:
    the statement becomes a `PreservedSource` with a `BridgeReason`, sitting
    at its original position in the body. Firmware is full of this and a
    conversion that failed on it would be useless.
  * PLATFORM DISAGREEMENT — an operation with no implemented block, with two,
    whose catalog inputs differ from its IR parameters, or with no field
    binding. Only drift between three tables can cause that, so it RAISES
    `UnrepresentableOperationError` rather than quietly producing a block with
    a missing operand.

WHAT THIS MODULE DOES NOT DECIDE. Whether a student may edit a construct.
That is B2's `editable_section_ids`/`security_region_id` on `FileSegment`, it
stays there, and no field here mirrors it. This module answers one question:
how is this semantic construct represented in Blockly?

DETERMINISTIC AND PURE. The same `SemanticProgram` always produces an equal
`BlocklyProgram`. No filesystem, no process, no session, no panel, no clock,
no generated ids.
"""

from __future__ import annotations

import math

from app.blockly.catalog import BlockCatalog, default_block_catalog
from app.blockly.models import BlockDefinition, BlockKind, ImplementationStatus, ValueType
from app.build.blockly_bridge.bindings import (
    FieldBinding,
    FieldBindingTable,
    default_field_bindings,
)
from app.build.blockly_bridge.errors import UnrepresentableOperationError
from app.build.blockly_bridge.models import (
    BlocklyBlock,
    BlocklyBranch,
    BlocklyField,
    BlocklyProgram,
    BlocklySection,
    BlocklyValueInput,
    BridgeReason,
    PreservedSource,
)
from app.build.blockly_bridge.structural import (
    ARITHMETIC_BLOCK_IDS,
    BINARY_OPERANDS,
    COMPARISON_BLOCK_IDS,
    CALL_ARGUMENT_INPUTS,
    DECLARATION_QUALIFIER_TOKENS,
    ELSE_IF_INPUT,
    ELSE_INPUT,
    FOR_INIT_INPUT,
    FOR_STEP_INPUT,
    FUNCTIONS_CALL_EXISTING_BLOCK_ID,
    FUNCTIONS_CALL_METHOD_BLOCK_ID,
    FUNCTIONS_CALL_VALUE_BLOCK_ID,
    FUNCTIONS_RETURN_VOID_BLOCK_ID,
    LOGIC_FALSE_BLOCK_ID,
    LOGIC_IF_BLOCK_ID,
    LOGIC_IF_EQUALS_BLOCK_ID,
    LOGIC_NOT_BLOCK_ID,
    LOGIC_TERNARY_BLOCK_ID,
    LOGIC_TRUE_BLOCK_ID,
    LOGICAL_BLOCK_IDS,
    LOOPS_FOR_BLOCK_ID,
    MATH_NUMBER_BLOCK_ID,
    TEXT_LITERAL_BLOCK_ID,
    VARIABLES_DECLARE_BLOCK_ID,
    VARIABLES_GET_BLOCK_ID,
    VARIABLES_SET_BLOCK_ID,
    VARIABLES_UPDATE_BLOCK_ID,
    declaration_type_token,
)
from app.build.semantic import (
    ArithmeticValue,
    AssignmentStatement,
    CallStatement,
    CallValue,
    ComparisonValue,
    ConditionalStatement,
    ForStatement,
    LiteralValue,
    LogicalValue,
    MethodCallStatement,
    NotValue,
    OperationStatement,
    OperationValue,
    ReturnStatement,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticType,
    SemanticValue,
    SymbolValue,
    TernaryValue,
    UnsupportedStatement,
    UpdateStatement,
    VariableDeclaration,
)

# The blocks that stand for IR structure rather than a registered operation
# (a call to an existing function, a conditional, a declaration, a return,
# and the value leaves/operators) are addressed by catalog block id — see
# `structural.py` for the list and why. Everything else routes through the
# operation registry, as before.


def program_to_blockly(
    program: SemanticProgram,
    *,
    catalog: BlockCatalog = default_block_catalog,
    bindings: FieldBindingTable = default_field_bindings,
) -> BlocklyProgram:
    """Represent `program` in Blockly, losing nothing it cannot draw.

    Every `SemanticSection` produces exactly one `BlocklySection`, in document
    order and under the same id, so the two representations stay addressable
    by one name — the same contract B3 keeps with B1 and B2 keeps with B1.

    `catalog` and `bindings` are injectable so a test (or a later phase with a
    different block set) can convert against another table without this module
    reaching for a global.
    """
    if not isinstance(program, SemanticProgram):
        raise TypeError(f"expected a SemanticProgram, got {type(program).__name__}")
    if not isinstance(catalog, BlockCatalog):
        raise TypeError(f"expected a BlockCatalog, got {type(catalog).__name__}")
    if not isinstance(bindings, FieldBindingTable):
        raise TypeError(f"expected a FieldBindingTable, got {type(bindings).__name__}")
    return BlocklyProgram(
        sections=tuple(_section(section, catalog, bindings) for section in program.sections)
    )


def block_definition_for(operation_id: str, catalog: BlockCatalog) -> BlockDefinition:
    """The one implemented catalog block that draws this operation.

    Public because it IS the B4 lookup — the relationship the catalog's
    `semantic_operation` field was added for, finally exercised. Raises rather
    than returning None: a caller reaching this point has an operation the IR
    declared, and the IR only declares operations with a block behind them
    (`tests/test_build_semantic.py` pins that), so a miss is drift.
    """
    candidates = [
        block
        for block in catalog.blocks
        if block.semantic_operation == operation_id
        and block.status is ImplementationStatus.IMPLEMENTED
    ]
    if not candidates:
        raise UnrepresentableOperationError(
            f"{operation_id}: no implemented block in the catalog draws this operation"
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(block.block_id for block in candidates))
        raise UnrepresentableOperationError(
            f"{operation_id}: ambiguous — {len(candidates)} implemented blocks claim it ({names})"
        )
    definition = candidates[0]
    if definition.blockly_type is None:  # pragma: no cover - the catalog forbids it
        raise UnrepresentableOperationError(
            f"{operation_id}: {definition.block_id} is implemented but names no Blockly type"
        )
    return definition


# --- sections ---------------------------------------------------------------


def _section(
    section: SemanticSection, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklySection:
    if section.operation is None:
        # The IR established no container form for this construct (a helper
        # function, a callback, global declarations), so there is no block to
        # build and its source is carried through exactly as B3 carried it.
        return BlocklySection(
            section_id=section.section_id,
            block=None,
            preserved=tuple(_preserved(statement) for statement in section.statements),
        )

    definition = block_definition_for(section.operation.operation_id, catalog)
    if definition.kind is not BlockKind.CONTAINER:
        raise UnrepresentableOperationError(
            f"{section.operation.operation_id}: the IR calls this a container, the catalog "
            f"calls {definition.block_id} a {definition.kind.value}"
        )
    operands = _operand_inputs(definition)
    if operands:
        raise UnrepresentableOperationError(
            f"{section.operation.operation_id}: a container takes no operands, but "
            f"{definition.block_id} declares {[item.name for item in operands]}"
        )
    body_inputs = [item for item in definition.inputs if item.value_type is ValueType.STATEMENTS]
    if len(body_inputs) != 1:
        raise UnrepresentableOperationError(
            f"{section.operation.operation_id}: {definition.block_id} needs exactly one "
            f"statement body input, it has {len(body_inputs)}"
        )
    block = BlocklyBlock(
        operation_id=section.operation.operation_id,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        # Provenance-only, exactly like `source_text` below — a generic named-
        # function container's preserved declarator, carried so an in-process
        # round trip (`blockly_to_semantic(program_to_blockly(program))`, no
        # wire in between) reconstructs an equal `SemanticSection`. See
        # `BlocklyBlock.container_signature`.
        container_signature=section.signature,
        fields=(),
        body_input=body_inputs[0].name,
        body=tuple(_item(statement, catalog, bindings) for statement in section.statements),
    )
    return BlocklySection(section_id=section.section_id, block=block, preserved=())


# --- statements -------------------------------------------------------------


def _item(
    statement: SemanticStatement, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklyBlock | PreservedSource:
    """One body statement as a block, or as source carried verbatim."""
    if isinstance(statement, OperationStatement):
        return _statement_block(statement, catalog, bindings)
    if isinstance(statement, CallStatement):
        return _call_existing_block(statement, catalog)
    if isinstance(statement, MethodCallStatement):
        return _method_call_block(statement, catalog)
    if isinstance(statement, AssignmentStatement):
        return _assign_block(statement, catalog)
    if isinstance(statement, ConditionalStatement):
        # The narrow `if equal to` block has no else; a chain is always drawn
        # by the generic `if`, which does.
        if (
            _fits_if_equals(statement.condition)
            and statement.else_if is None
            and statement.else_body is None
        ):
            return _if_equals_block(statement, catalog, bindings)
        return _if_block(statement, catalog, bindings)
    if isinstance(statement, ForStatement):
        return _for_block(statement, catalog, bindings)
    if isinstance(statement, UpdateStatement):
        return _update_block(statement, catalog)
    if isinstance(statement, VariableDeclaration):
        return _declare_block(statement, catalog)
    if isinstance(statement, ReturnStatement):
        definition = _require_implemented(FUNCTIONS_RETURN_VOID_BLOCK_ID, catalog)
        return BlocklyBlock(
            operation_id=FUNCTIONS_RETURN_VOID_BLOCK_ID,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            source_text=statement.text,
        )
    return _preserved(statement)


def _require_implemented(block_id: str, catalog: BlockCatalog) -> BlockDefinition:
    """The one implemented catalog block with this id, or a loud disagreement.

    The `functions.call_existing`/`logic.if_equals` counterpart of
    `block_definition_for` — looked up by BLOCK id rather than by operation
    id, since neither routes through the operation registry (see the module
    header). A miss here is drift between this module and the catalog, never
    something a workspace can cause.
    """
    definition = catalog.block(block_id)
    if (
        definition is None
        or definition.status is not ImplementationStatus.IMPLEMENTED
        or definition.blockly_type is None
    ):
        raise UnrepresentableOperationError(f"{block_id}: no implemented catalog block")
    return definition


def _single_statements_input(definition: BlockDefinition) -> str:
    """The FIRST STATEMENTS input this block declares — its primary body's name.

    A block with more (`logic.if`: DO, ELSE_IF, ELSE) carries the rest as
    branches; a block with none cannot hold a body at all.
    """
    body_inputs = [item for item in definition.inputs if item.value_type is ValueType.STATEMENTS]
    if not body_inputs:
        raise UnrepresentableOperationError(
            f"{definition.block_id}: needs a statement body input, it has none"
        )
    return body_inputs[0].name


def _require_inputs(definition: BlockDefinition, *names: str) -> None:
    """The catalog block must declare every input this module fills by name."""
    declared = {item.name for item in definition.inputs}
    missing = [name for name in names if name not in declared]
    if missing:
        raise UnrepresentableOperationError(
            f"{definition.block_id}: the bridge fills {missing}, which the catalog block "
            "does not declare"
        )


def _argument_inputs(
    arguments: tuple[SemanticValue, ...], catalog: BlockCatalog
) -> tuple[BlocklyValueInput, ...] | None:
    """A call's arguments as `ARG0..` value inputs, in order - or None.

    None is "understood, undrawable": more arguments than the block has sockets,
    or one argument no value block can draw. The caller then carries the WHOLE
    statement verbatim - an argument is never dropped and a call is never drawn
    with fewer arguments than it has.
    """
    if len(arguments) > len(CALL_ARGUMENT_INPUTS):
        return None
    inputs: list[BlocklyValueInput] = []
    for name, argument in zip(CALL_ARGUMENT_INPUTS, arguments):
        block = _value_block(argument, catalog)
        if block is None:
            return None
        inputs.append(BlocklyValueInput(name=name, block=block))
    return tuple(inputs)


def _call_existing_block(
    statement: CallStatement, catalog: BlockCatalog
) -> BlocklyBlock | PreservedSource:
    """A `CallStatement` as the `functions.call_existing` block.

    The function NAME is always representable (`CallStatement.function_name` is
    validated to be a plain identifier, which a TEXT field can hold); its
    arguments may not be, in which case the statement is preserved whole.
    """
    definition = _require_implemented(FUNCTIONS_CALL_EXISTING_BLOCK_ID, catalog)
    inputs = _argument_inputs(statement.arguments, catalog)
    if inputs is None:
        return _undrawable(statement.text, f"call to {statement.function_name}")
    return BlocklyBlock(
        operation_id=FUNCTIONS_CALL_EXISTING_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(BlocklyField(name="NAME", value=statement.function_name),),
        values=inputs,
    )


def _method_call_block(
    statement: MethodCallStatement, catalog: BlockCatalog
) -> BlocklyBlock | PreservedSource:
    """A `MethodCallStatement` as `functions.call_method`: RECEIVER, METHOD, ARGn."""
    definition = _require_implemented(FUNCTIONS_CALL_METHOD_BLOCK_ID, catalog)
    inputs = _argument_inputs(statement.arguments, catalog)
    if inputs is None:
        return _undrawable(statement.text, f"call to {statement.receiver}.{statement.method_name}")
    return BlocklyBlock(
        operation_id=FUNCTIONS_CALL_METHOD_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(
            BlocklyField(name="RECEIVER", value=statement.receiver),
            BlocklyField(name="METHOD", value=statement.method_name),
        ),
        values=inputs,
    )


def _assign_block(
    statement: AssignmentStatement, catalog: BlockCatalog
) -> BlocklyBlock | PreservedSource:
    """An `AssignmentStatement` as `variables.set`: NAME, OPERATOR, a VALUE socket."""
    definition = _require_implemented(VARIABLES_SET_BLOCK_ID, catalog)
    value = _value_block(statement.value, catalog)
    if value is None:
        return _undrawable(statement.text, f"assignment to {statement.target}")
    return BlocklyBlock(
        operation_id=VARIABLES_SET_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(
            BlocklyField(name="NAME", value=statement.target),
            BlocklyField(name="OPERATOR", value=statement.operator),
        ),
        values=(BlocklyValueInput(name="VALUE", block=value),),
    )


def _if_equals_block(
    statement: ConditionalStatement, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklyBlock | PreservedSource:
    """A `ConditionalStatement` as the `logic.if_equals` block, when it fits.

    THE BLOCK'S SHAPE IS NARROW ON PURPOSE (see its catalog docstring): LEFT
    is always read as a named reference and RIGHT as a fixed text value to
    compare it against — the exact shape an authorization gate needs
    (`message == "START"`), not a general expression. A condition this
    function cannot draw in that shape — anything but a `ComparisonValue` of
    a `SymbolValue` on the left and a text `LiteralValue` on the right — is
    understood by the IR and undrawable by this one block, so it is preserved
    exactly like `_statement_block` preserves an operand no field can hold.
    """
    definition = _require_implemented(LOGIC_IF_EQUALS_BLOCK_ID, catalog)
    condition = statement.condition
    if not _fits_if_equals(condition):
        if statement.text is None:
            raise UnrepresentableOperationError(
                f"{LOGIC_IF_EQUALS_BLOCK_ID}: condition ({condition.source_text}) is not a "
                "name-equals-text comparison, and this statement records no source to carry "
                "verbatim instead"
            )
        return PreservedSource(
            text=statement.text, reason=BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE
        )
    return BlocklyBlock(
        operation_id=LOGIC_IF_EQUALS_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(
            BlocklyField(name="LEFT", value=condition.left.name),
            BlocklyField(name="OPERATOR", value=condition.operator),
            BlocklyField(name="RIGHT", value=str(condition.right.value)),
        ),
        body_input=_single_statements_input(definition),
        body=tuple(_item(item, catalog, bindings) for item in statement.body),
    )


def _fits_if_equals(condition: SemanticValue) -> bool:
    """Whether `logic.if_equals`' narrow NAME ==/!= "TEXT" shape can draw this.

    Kept as the first choice for exactly that shape so every existing
    workspace draws as it always did; any other comparison is drawn with the
    generic `logic.if` and a comparison block in its CONDITION socket.
    """
    return (
        isinstance(condition, ComparisonValue)
        and condition.operator in ("==", "!=")
        and isinstance(condition.left, SymbolValue)
        and isinstance(condition.right, LiteralValue)
        and condition.right.value_type is SemanticType.TEXT
        and bool(condition.right.value)
    )


def _undrawable(text: str | None, what: str) -> PreservedSource:
    """Carry an understood-but-undrawable statement verbatim, or report it.

    The same rule `_statement_block` applies to an operand no field can hold:
    a statement read from source keeps its text; an authored one has nothing
    faithful to fall back on, so it is reported rather than dropped.
    """
    if text is None:
        raise UnrepresentableOperationError(
            f"{what} cannot be drawn, and this statement records no source to carry "
            "verbatim instead"
        )
    return PreservedSource(text=text, reason=BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE)


def _if_block(
    statement: ConditionalStatement, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklyBlock | PreservedSource:
    """A `ConditionalStatement` as the generic `logic.if` block.

    The condition becomes a value block in the CONDITION socket, built by
    `_value_block` from the IR's own value tree, so no condition shape is
    special-cased here. A chain continues in ONE of two extra statement inputs:
    ELSE_IF holds the next link (itself a `logic.if`), ELSE holds the final body.

    ALL OR NOTHING: a link whose condition cannot be drawn, or an `else {}`
    with nothing in it (an empty statement input cannot say "an else exists"),
    makes the WHOLE chain preserved source rather than a chain with a hole.
    """
    definition = _require_implemented(LOGIC_IF_BLOCK_ID, catalog)
    _require_inputs(definition, "CONDITION", ELSE_IF_INPUT, ELSE_INPUT)
    condition = _value_block(statement.condition, catalog)
    if condition is None:
        return _undrawable(statement.text, f"condition ({statement.condition.source_text})")
    branches: list[BlocklyBranch] = []
    if statement.else_if is not None:
        link = _if_block(statement.else_if, catalog, bindings)
        if isinstance(link, PreservedSource):
            return _undrawable(statement.text, "an else-if branch")
        branches.append(BlocklyBranch(name=ELSE_IF_INPUT, items=(link,)))
    elif statement.else_body is not None:
        if not statement.else_body:
            return _undrawable(statement.text, "an empty else")
        branches.append(
            BlocklyBranch(
                name=ELSE_INPUT,
                items=tuple(_item(item, catalog, bindings) for item in statement.else_body),
            )
        )
    return BlocklyBlock(
        operation_id=LOGIC_IF_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        values=(BlocklyValueInput(name="CONDITION", block=condition),),
        body_input=_single_statements_input(definition),
        body=tuple(_item(item, catalog, bindings) for item in statement.body),
        branches=tuple(branches),
    )


def _for_block(
    statement: ForStatement, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklyBlock | PreservedSource:
    """A `ForStatement` as `loops.for`: INIT, CONDITION, STEP and the DO body.

    INIT and STEP each hold the ONE statement block their header part is; an
    empty part is an empty input. Every part must be drawable or the whole loop
    stays preserved source - a `for` with a hole in its header would generate a
    different loop.
    """
    definition = _require_implemented(LOOPS_FOR_BLOCK_ID, catalog)
    _require_inputs(definition, FOR_INIT_INPUT, "CONDITION", FOR_STEP_INPUT)
    values: tuple[BlocklyValueInput, ...] = ()
    if statement.condition is not None:
        condition = _value_block(statement.condition, catalog)
        if condition is None:
            return _undrawable(statement.text, f"the condition ({statement.condition.source_text})")
        values = (BlocklyValueInput(name="CONDITION", block=condition),)
    branches: list[BlocklyBranch] = []
    for name, part in ((FOR_INIT_INPUT, statement.init), (FOR_STEP_INPUT, statement.step)):
        if part is None:
            continue
        drawn = _item(part, catalog, bindings)
        if isinstance(drawn, PreservedSource):
            return _undrawable(statement.text, f"the {name.lower()} of a for loop")
        branches.append(BlocklyBranch(name=name, items=(drawn,)))
    return BlocklyBlock(
        operation_id=LOOPS_FOR_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        values=values,
        body_input=_single_statements_input(definition),
        body=tuple(_item(item, catalog, bindings) for item in statement.body),
        branches=tuple(branches),
    )


def _update_block(statement: UpdateStatement, catalog: BlockCatalog) -> BlocklyBlock:
    """An `UpdateStatement` as `variables.update`: NAME and OPERATOR (++ / --)."""
    definition = _require_implemented(VARIABLES_UPDATE_BLOCK_ID, catalog)
    return BlocklyBlock(
        operation_id=VARIABLES_UPDATE_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(
            BlocklyField(name="NAME", value=statement.target),
            BlocklyField(name="OPERATOR", value=statement.operator),
        ),
    )


def _declare_block(
    statement: VariableDeclaration, catalog: BlockCatalog
) -> BlocklyBlock | PreservedSource:
    """A `VariableDeclaration` as `variables.declare`.

    QUALIFIER, TYPE and NAME are fields; INITIAL is a value socket that is
    simply absent for `String message;`.
    """
    definition = _require_implemented(VARIABLES_DECLARE_BLOCK_ID, catalog)
    values: tuple[BlocklyValueInput, ...] = ()
    if statement.initializer is not None:
        initial = _value_block(statement.initializer, catalog)
        if initial is None:
            return _undrawable(statement.text, f"declaration of {statement.name}")
        values = (BlocklyValueInput(name="INITIAL", block=initial),)
    return BlocklyBlock(
        operation_id=VARIABLES_DECLARE_BLOCK_ID,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        source_text=statement.text,
        fields=(
            BlocklyField(name="QUALIFIER", value=DECLARATION_QUALIFIER_TOKENS[statement.qualifiers]),
            BlocklyField(
                name="TYPE",
                value=declaration_type_token(statement.value_type, statement.type_name),
            ),
            BlocklyField(name="NAME", value=statement.name),
        ),
        values=values,
    )


def _value_block(value: SemanticValue, catalog: BlockCatalog) -> BlocklyBlock | None:
    """One IR value as the value block that draws it, or None if none can.

    None is "understood, undrawable" — an empty text literal (a Blockly field
    cannot hold nothing), a non-finite number, a call with too many arguments — and
    the caller decides whether to preserve or report the enclosing statement.
    A structural value is found by block id (`structural.py`); a VALUE
    operation (`text.index_of`, ...) through the catalog relation, with its
    operands in the catalog block's own input sockets.
    """
    if isinstance(value, SymbolValue):
        return _leaf_block(VARIABLES_GET_BLOCK_ID, "NAME", value.name, catalog)
    if isinstance(value, LiteralValue):
        if value.value_type is SemanticType.TEXT and value.value:
            return _leaf_block(TEXT_LITERAL_BLOCK_ID, "VALUE", str(value.value), catalog)
        if value.value_type is SemanticType.NUMBER and math.isfinite(value.value):
            return _leaf_block(MATH_NUMBER_BLOCK_ID, "VALUE", str(value.value), catalog)
        if value.value_type is SemanticType.BOOLEAN:
            definition = _require_implemented(
                LOGIC_TRUE_BLOCK_ID if value.value else LOGIC_FALSE_BLOCK_ID, catalog
            )
            return BlocklyBlock(
                operation_id=definition.semantic_operation,
                block_id=definition.block_id,
                block_type=definition.blockly_type,
            )
        return None
    if isinstance(value, CallValue):
        inputs = _argument_inputs(value.arguments, catalog)
        if inputs is None:
            return None
        definition = _require_implemented(FUNCTIONS_CALL_VALUE_BLOCK_ID, catalog)
        return BlocklyBlock(
            operation_id=definition.semantic_operation,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            fields=(BlocklyField(name="NAME", value=value.function_name),),
            values=inputs,
        )
    if isinstance(value, NotValue):
        operand = _value_block(value.operand, catalog)
        if operand is None:
            return None
        definition = _require_implemented(LOGIC_NOT_BLOCK_ID, catalog)
        return BlocklyBlock(
            operation_id=definition.semantic_operation,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            values=(BlocklyValueInput(name="VALUE", block=operand),),
        )
    if isinstance(value, TernaryValue):
        parts = [
            _value_block(part, catalog) for part in (value.condition, value.if_true, value.if_false)
        ]
        if any(part is None for part in parts):
            return None
        definition = _require_implemented(LOGIC_TERNARY_BLOCK_ID, catalog)
        return BlocklyBlock(
            operation_id=definition.semantic_operation,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            values=tuple(
                BlocklyValueInput(name=name, block=part)
                for name, part in zip(("CONDITION", "THEN", "ELSE"), parts)
            ),
        )
    if isinstance(value, (ComparisonValue, ArithmeticValue, LogicalValue)):
        if isinstance(value, ComparisonValue):
            table = COMPARISON_BLOCK_IDS
        elif isinstance(value, LogicalValue):
            table = LOGICAL_BLOCK_IDS
        else:
            table = ARITHMETIC_BLOCK_IDS
        block_id = table.get(value.operator)
        left = _value_block(value.left, catalog)
        right = _value_block(value.right, catalog)
        if block_id is None or left is None or right is None:
            return None
        definition = _require_implemented(block_id, catalog)
        return BlocklyBlock(
            operation_id=definition.semantic_operation,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            values=tuple(
                BlocklyValueInput(name=name, block=operand)
                for name, operand in zip(BINARY_OPERANDS, (left, right))
            ),
        )
    if isinstance(value, OperationValue):
        definition = block_definition_for(value.operation_id, catalog)
        if not definition.kind.yields_value:
            raise UnrepresentableOperationError(
                f"{value.operation_id}: the IR calls this a value, the catalog calls "
                f"{definition.block_id} a {definition.kind.value}"
            )
        sockets = tuple(item.name for item in _operand_inputs(definition))
        if sockets != value.operation.parameter_names:
            raise UnrepresentableOperationError(
                f"{value.operation_id}: IR parameters {list(value.operation.parameter_names)} "
                f"do not match {definition.block_id}'s inputs {list(sockets)}"
            )
        operands: list[BlocklyValueInput] = []
        for argument in value.arguments:
            operand = _value_block(argument.value, catalog)
            if operand is None:
                return None
            operands.append(BlocklyValueInput(name=argument.name, block=operand))
        return BlocklyBlock(
            operation_id=value.operation_id,
            block_id=definition.block_id,
            block_type=definition.blockly_type,
            values=tuple(operands),
        )
    return None


def _leaf_block(block_id: str, field: str, text: str, catalog: BlockCatalog) -> BlocklyBlock:
    definition = _require_implemented(block_id, catalog)
    return BlocklyBlock(
        operation_id=definition.semantic_operation,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        fields=(BlocklyField(name=field, value=text),),
    )


def _preserved(statement: SemanticStatement) -> PreservedSource:
    if not isinstance(statement, UnsupportedStatement):  # pragma: no cover - defensive
        raise UnrepresentableOperationError(
            f"cannot preserve a {type(statement).__name__} without a reason"
        )
    # B3's reason travels through unchanged — see `models.PreservedSource`.
    return PreservedSource(text=statement.source_text, reason=statement.reason)


def _statement_block(
    statement: OperationStatement, catalog: BlockCatalog, bindings: FieldBindingTable
) -> BlocklyBlock | PreservedSource:
    definition = block_definition_for(statement.operation_id, catalog)
    if definition.kind is not BlockKind.STATEMENT:
        raise UnrepresentableOperationError(
            f"{statement.operation_id}: the IR calls this a statement, the catalog calls "
            f"{definition.block_id} a {definition.kind.value}"
        )
    fields = bindings.fields_for(statement.operation_id)
    if fields is None:
        raise UnrepresentableOperationError(
            f"{statement.operation_id}: no field binding declares how its operands reach "
            f"{definition.blockly_type}"
        )
    _check_operands_agree(statement, definition, fields)

    rendered: list[BlocklyField] = []
    for binding in fields:
        value = statement.value(binding.input_name)
        assert value is not None  # guaranteed by the agreement check above
        text = binding.render(value)
        if text is None:
            # Understood, undrawable. The whole statement is carried verbatim
            # rather than half-built or silently altered — see the module
            # docstring's "two kinds of cannot".
            if statement.source_text is None:
                # Undrawable AND source-less: an authored statement whose
                # operand no real field can hold. There is no block to build
                # and no text to fall back on, so there is nothing faithful to
                # produce. It is reported rather than dropped or coerced into a
                # field that would silently change what the statement says —
                # its C++ still exists (B6 writes it from the operation), but
                # this workspace cannot show it.
                raise UnrepresentableOperationError(
                    f"{statement.operation_id}: {binding.input_name} "
                    f"({value.source_text}) cannot occupy its field, and this statement "
                    "records no source to carry verbatim instead"
                )
            return PreservedSource(
                text=statement.source_text,
                reason=BridgeReason.FIELD_VALUE_NOT_REPRESENTABLE,
            )
        rendered.append(BlocklyField(name=binding.input_name, value=text))

    return BlocklyBlock(
        operation_id=statement.operation_id,
        block_id=definition.block_id,
        block_type=definition.blockly_type,
        # Provenance, not meaning: the statement's own text, kept so the block
        # can be returned to the IR without anybody generating C++ for it. See
        # `models.BlocklyBlock`.
        source_text=statement.source_text,
        fields=tuple(rendered),
    )


# --- the three tables must agree --------------------------------------------


def _operand_inputs(definition: BlockDefinition) -> tuple:
    """The catalog block's inputs that are operands, bodies excluded."""
    return tuple(
        item for item in definition.inputs if item.value_type is not ValueType.STATEMENTS
    )


def _check_operands_agree(
    statement: OperationStatement,
    definition: BlockDefinition,
    fields: tuple[FieldBinding, ...],
) -> None:
    """The IR, the catalog and the binding table must describe one shape.

    Checked on every conversion rather than once at import, because the tables
    are injectable and a test may supply its own. Cheap — three tuple
    comparisons — and it is what keeps a drifting table from producing a block
    with a missing or misnamed operand instead of an error.
    """
    parameters = statement.operation.parameter_names
    catalog_inputs = tuple(item.name for item in _operand_inputs(definition))
    bound = tuple(binding.input_name for binding in fields)
    if parameters != catalog_inputs:
        raise UnrepresentableOperationError(
            f"{statement.operation_id}: IR parameters {list(parameters)} do not match "
            f"{definition.block_id}'s inputs {list(catalog_inputs)}"
        )
    if parameters != bound:
        raise UnrepresentableOperationError(
            f"{statement.operation_id}: IR parameters {list(parameters)} do not match its "
            f"field bindings {list(bound)}"
        )
