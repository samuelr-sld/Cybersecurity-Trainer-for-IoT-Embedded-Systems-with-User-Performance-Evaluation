"""The catalog blocks that stand for IR STRUCTURE rather than an IR operation.

Most implemented blocks are found through the catalog relation this package is
built on — `semantic_operation` -> `SemanticOperationRegistry` — and need no
table here. The blocks below cannot be: each stands for a construct the IR
states as its own model class, not as a registered operation —

    functions.call_existing   CallStatement            (an arbitrary function name)
    logic.if_equals           ConditionalStatement     (narrow NAME ==/!= "TEXT" form)
    logic.if                  ConditionalStatement     (any comparison, in a socket)
    variables.declare         VariableDeclaration
    functions.return_void     ReturnStatement
    variables.get             SymbolValue
    text.literal              LiteralValue (text)
    math.number               LiteralValue (number)
    logic.equal / not_equal /
    less_equal                ComparisonValue          (one block per operator)
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

from app.build.semantic import SemanticType

FUNCTIONS_CALL_EXISTING_BLOCK_ID = "functions.call_existing"
LOGIC_IF_EQUALS_BLOCK_ID = "logic.if_equals"
LOGIC_IF_BLOCK_ID = "logic.if"
VARIABLES_DECLARE_BLOCK_ID = "variables.declare"
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
}

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
