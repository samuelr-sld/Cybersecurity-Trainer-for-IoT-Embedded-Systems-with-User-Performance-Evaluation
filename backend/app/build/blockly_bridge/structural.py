"""The catalog blocks that stand for IR STRUCTURE rather than an IR operation.

Most implemented blocks are found through the catalog relation this package is
built on — `semantic_operation` -> `SemanticOperationRegistry` — and need no
table here. The blocks below cannot be: each stands for a construct the IR
states as its own model class, not as a registered operation —

    functions.call_existing   CallStatement            (an arbitrary function name)
    logic.if_equals           ConditionalStatement     (narrow NAME ==/!= "TEXT" form)
    logic.if                  ConditionalStatement     (any comparison, in a socket)
    variables.declare         VariableDeclaration      (qualifier, type, optional initializer)
    variables.set             AssignmentStatement      (=, +=, -=)
    functions.call_method     MethodCallStatement
    functions.call_value      CallValue                (a call used as a value)
    logic.true / logic.false  LiteralValue (boolean)
    functions.return_void     ReturnStatement
    variables.get             SymbolValue
    text.literal              LiteralValue (text)
    math.number               LiteralValue (number)
    logic.equal / not_equal /
    less_equal / less /
    greater / greater_equal   ComparisonValue          (one block per operator)
    logic.and / logic.or      LogicalValue
    logic.not                 NotValue
    logic.ternary             TernaryValue
    loops.for                 ForStatement             (INIT, CONDITION, STEP, DO)
    variables.update          UpdateStatement          (++, --)
    math.add                  ArithmeticValue

— so `adapter.py` and `reverse.py` address them by CATALOG BLOCK ID. They
share this one module rather than each restating the ids, which is how the two
directions would drift apart. Blockly types appear nowhere here: those still
come off the catalog's `BlockDefinition`, exactly as for every other block.

Each block is one small generic construct. None of them knows about Panel 1,
tokens, motors or MQTT: the remediation is COMPOSED from them in a workspace,
and nothing in this package could build it on its own.
"""

from __future__ import annotations

from app.build.semantic import (
    DECLARATION_TYPE_NAMES,
    DEFAULT_TYPE_NAMES,
    SemanticType,
)

FUNCTIONS_CALL_EXISTING_BLOCK_ID = "functions.call_existing"
LOGIC_IF_EQUALS_BLOCK_ID = "logic.if_equals"
LOGIC_IF_BLOCK_ID = "logic.if"
VARIABLES_DECLARE_BLOCK_ID = "variables.declare"
VARIABLES_SET_BLOCK_ID = "variables.set"
FUNCTIONS_CALL_METHOD_BLOCK_ID = "functions.call_method"
FUNCTIONS_CALL_VALUE_BLOCK_ID = "functions.call_value"
LOGIC_TRUE_BLOCK_ID = "logic.true"
LOGIC_NOT_BLOCK_ID = "logic.not"
LOGIC_TERNARY_BLOCK_ID = "logic.ternary"
LOOPS_FOR_BLOCK_ID = "loops.for"
VARIABLES_UPDATE_BLOCK_ID = "variables.update"

#: The extra statement inputs of the blocks that have more than one. `logic.if`
#: continues with ELSE_IF (one nested `if`) OR ELSE; `loops.for` has an INIT and
#: a STEP beside its DO body.
ELSE_IF_INPUT = "ELSE_IF"
ELSE_INPUT = "ELSE"
FOR_INIT_INPUT = "INIT"
FOR_STEP_INPUT = "STEP"
LOGIC_FALSE_BLOCK_ID = "logic.false"

#: A call block's argument sockets, in order. A call with more arguments than
#: this is understood by the IR and cannot be drawn, so it stays visible as
#: preserved source. Pinned against the catalog's own input names by a test.
CALL_ARGUMENT_INPUTS = ("ARG0", "ARG1", "ARG2", "ARG3")
MAX_CALL_ARGUMENTS = len(CALL_ARGUMENT_INPUTS)
VARIABLES_GET_BLOCK_ID = "variables.get"
FUNCTIONS_RETURN_VOID_BLOCK_ID = "functions.return_void"
TEXT_LITERAL_BLOCK_ID = "text.literal"
MATH_NUMBER_BLOCK_ID = "math.number"

#: `ComparisonValue.operator` -> the block that draws it. One block per
#: operator, as the catalog models them, and exactly the IR's operator set.
COMPARISON_BLOCK_IDS = {
    "==": "logic.equal",
    "!=": "logic.not_equal",
    "<=": "logic.less_equal",
    "<": "logic.less",
    ">": "logic.greater",
    ">=": "logic.greater_equal",
}

#: `LogicalValue.operator` -> the block that draws it (operands A and B).
LOGICAL_BLOCK_IDS = {"&&": "logic.and", "||": "logic.or"}

#: `ArithmeticValue.operator` -> the block that draws it.
ARITHMETIC_BLOCK_IDS = {"+": "math.add"}

#: The field token each declarable type is chosen by in `variables.declare`'s
#: TYPE dropdown: the `SemanticType` value itself (`text`/`number`/
#: `boolean`), never a C++ type name — how a type is SPELLED in C++ is
#: `app/build/semantic/emissions.py`'s business, and the dropdown's visible
#: label is the frontend's.
DECLARATION_TYPE_TOKENS = {
    SemanticType.TEXT: "text",
    SemanticType.NUMBER: "number",
    SemanticType.BOOLEAN: "boolean",
}

#: The operand sockets of the two-operand value blocks, in catalog order.
BINARY_OPERANDS = ("A", "B")

#: Declaration qualifiers <-> the QUALIFIER dropdown's tokens.
DECLARATION_QUALIFIER_TOKENS = {
    (): "none",
    ("static",): "static",
    ("const",): "const",
    ("static", "const"): "static_const",
}

#: The TYPE dropdown's tokens. A type written as its DEFAULT spelling (`String`,
#: `int`, `bool`) keeps the original semantic-type token (`text`, `number`,
#: `boolean`); any other spelling is its own name with spaces as underscores.
#: The value is `(SemanticType, type_name)` where `type_name` is None for a default.
def _build_declaration_type_tokens() -> dict[str, tuple[SemanticType, str | None]]:
    tokens: dict[str, tuple[SemanticType, str | None]] = {}
    for value_type, default_name in DEFAULT_TYPE_NAMES.items():
        tokens[DECLARATION_TYPE_TOKENS[value_type]] = (value_type, None)
    for name, value_type in DECLARATION_TYPE_NAMES.items():
        if name != DEFAULT_TYPE_NAMES[value_type]:
            tokens[name.replace(" ", "_")] = (value_type, name)
    return tokens


DECLARATION_TYPE_FOR_TOKEN = _build_declaration_type_tokens()


def declaration_type_token(value_type: SemanticType, type_name: str | None) -> str:
    """The TYPE dropdown token that draws this declaration's type."""
    if type_name is None:
        return DECLARATION_TYPE_TOKENS[value_type]
    return type_name.replace(" ", "_")
