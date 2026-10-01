"""General programming blocks: logic, loops, math, text, variables, functions, lists."""

from __future__ import annotations

from app.blockly.definitions.factory import ANY, BODY, BOOLEAN, LIST, NUMBER, TEXT, CategoryFactory

_logic = CategoryFactory("logic")
_loops = CategoryFactory("loops")
_math = CategoryFactory("math")
_text = CategoryFactory("text")
_variables = CategoryFactory("variables")
_functions = CategoryFactory("functions")
_lists = CategoryFactory("lists")

_AB_BOOL = (("A", BOOLEAN), ("B", BOOLEAN))
_AB_ANY = (("A", ANY), ("B", ANY))
_AB_NUM = (("A", NUMBER), ("B", NUMBER))

#: A call block's argument sockets, in order. Fixed rather than a mutator: a
#: Blockly value input is either there or not, so a call with N arguments fills
#: `ARG0..ARG{N-1}` and leaves the rest empty. A call with more arguments than
#: this cannot be drawn and stays visible, preserved source (P2). The bridge's
#: `structural.CALL_ARGUMENT_INPUTS` restates the same names - pinned by a test.
_CALL_ARGS = (("ARG0", ANY), ("ARG1", ANY), ("ARG2", ANY), ("ARG3", ANY))

LOGIC = (
    _logic.value("logic.true", "true", "The boolean literal true.", BOOLEAN, implemented_as="logic_true"),
    _logic.value("logic.false", "false", "The boolean literal false.", BOOLEAN, implemented_as="logic_false"),
    _logic.expression(
        "logic.not", "not", "Logical negation.", BOOLEAN, (("VALUE", BOOLEAN),),
        implemented_as="logic_not",
    ),
    _logic.expression(
        "logic.and", "and", "True when both operands are true.", BOOLEAN, _AB_BOOL,
        implemented_as="logic_and",
    ),
    _logic.expression(
        "logic.or", "or", "True when either operand is true.", BOOLEAN, _AB_BOOL,
        implemented_as="logic_or",
    ),
    # --- Panel 1 token-parsing vocabulary ------------------------------------
    # The generic comparison/`if` blocks below are IMPLEMENTED (not new
    # dedicated ones) because the Blockly bridge now models nested VALUE
    # inputs: a condition is a comparison block plugged into `logic.if`'s
    # CONDITION socket, exactly as the catalog always described them. Only
    # the three operators the IR's `ComparisonValue` states (`==`, `!=`, `<=`)
    # are implemented; the other orderings stay CATALOGED.
    _logic.expression(
        "logic.equal", "equal", "True when both operands are equal.", BOOLEAN, _AB_ANY,
        implemented_as="logic_equal",
    ),
    _logic.expression(
        "logic.not_equal", "not equal", "True when the operands differ.", BOOLEAN, _AB_ANY,
        implemented_as="logic_not_equal",
    ),
    _logic.expression(
        "logic.greater", "greater than", "True when A is greater than B.", BOOLEAN, _AB_NUM,
        implemented_as="logic_greater",
    ),
    _logic.expression(
        "logic.less", "less than", "True when A is less than B.", BOOLEAN, _AB_NUM,
        implemented_as="logic_less",
    ),
    _logic.expression(
        "logic.greater_equal", "greater or equal", "True when A is greater than or equal to B.", BOOLEAN, _AB_NUM,
        implemented_as="logic_greater_equal",
    ),
    _logic.expression(
        "logic.less_equal", "less or equal", "True when A is less than or equal to B.", BOOLEAN, _AB_NUM,
        implemented_as="logic_less_equal",
    ),
    # DO is the primary body; ELSE_IF holds ONE nested `if` (the next link of
    # the chain) and ELSE the final body. A block continues with else-if OR
    # else, never both. `logic.if_else` / `logic.else_if` below stay CATALOGED:
    # one block covers every chain, so they are superseded rather than built.
    _logic.statement(
        "logic.if",
        "if",
        "Runs a body when a condition is true, optionally followed by else-if / else.",
        (("CONDITION", BOOLEAN), ("DO", BODY), ("ELSE_IF", BODY), ("ELSE", BODY)),
        implemented_as="logic_if",
    ),
    _logic.statement(
        "logic.if_else",
        "if / else",
        "Runs one body when a condition is true, another otherwise.",
        (("CONDITION", BOOLEAN), ("DO", BODY), ("ELSE", BODY)),
        op="logic.if",
    ),
    _logic.statement(
        "logic.else_if",
        "if / else if",
        "A chain of conditions, each with its own body, and an optional final else.",
        (("CONDITION", BOOLEAN), ("DO", BODY), ("ELSE_IF_CONDITION", BOOLEAN), ("ELSE_IF_DO", BODY), ("ELSE", BODY)),
        op="logic.if",
    ),
    _logic.expression(
        "logic.ternary",
        "conditional value",
        "Chooses between two values based on a condition.",
        ANY,
        (("CONDITION", BOOLEAN), ("THEN", ANY), ("ELSE", ANY)),
        implemented_as="logic_ternary",
    ),
    # --- no-device/Blockly-integration correction ---------------------------
    # A deliberately narrow, IMPLEMENTED addition, distinct from the generic
    # CATALOGED `logic.if`/`logic.equal` above. Composing those properly needs
    # a value-input (nested expression block) mechanism the Blockly bridge
    # does not have yet (see `app/build/blockly_bridge/adapter.py`); this one
    # compound block — an equality check plus a body, three plain fields and
    # no nested value block — is exactly what an authorization gate
    # (`if (message == "START") { ... }`) needs and nothing more. It reuses
    # `app/build/semantic/models.py`'s `ConditionalStatement`/`ComparisonValue`.
    _logic.statement(
        "logic.if_equals",
        "if equal to",
        "Runs a body when a value equals (or differs from) another value.",
        (("LEFT", TEXT), ("OPERATOR", TEXT), ("RIGHT", TEXT), ("DO", BODY)),
        implemented_as="if_equals",
    ),
)

LOOPS = (
    _loops.statement("loops.repeat", "repeat", "Runs a body a fixed number of times.", (("TIMES", NUMBER), ("DO", BODY))),
    _loops.statement(
        "loops.while", "while", "Runs a body while a condition is true.", (("CONDITION", BOOLEAN), ("DO", BODY))
    ),
    _loops.statement(
        "loops.do_while",
        "do / while",
        "Runs a body once, then repeats while a condition is true.",
        (("DO", BODY), ("CONDITION", BOOLEAN)),
    ),
    # A C-style `for`: INIT and STEP each hold ONE statement (a declaration or
    # assignment; an assignment or ++/--), CONDITION is a boolean value, DO the
    # body. Any of the three header parts may be empty.
    _loops.statement(
        "loops.for",
        "for",
        "Runs an initialization once, then repeats a body and a step while a condition holds.",
        (("DO", BODY), ("INIT", BODY), ("CONDITION", BOOLEAN), ("STEP", BODY)),
        implemented_as="for_loop",
    ),
    _loops.statement(
        "loops.for_each",
        "for each",
        "Runs a body once per item of a list.",
        (("VARIABLE", TEXT), ("LIST", LIST), ("DO", BODY)),
    ),
    _loops.statement("loops.break", "break", "Leaves the innermost loop."),
    _loops.statement("loops.continue", "continue", "Skips to the next iteration of the innermost loop."),
)

MATH = (
    _math.value(
        "math.number", "number", "A numeric literal.", NUMBER, inputs=(("VALUE", NUMBER),),
        implemented_as="math_number",
    ),
    _math.expression("math.add", "add", "A plus B.", NUMBER, _AB_NUM, implemented_as="math_add"),
    _math.expression("math.subtract", "subtract", "A minus B.", NUMBER, _AB_NUM),
    _math.expression("math.multiply", "multiply", "A times B.", NUMBER, _AB_NUM),
    _math.expression("math.divide", "divide", "A divided by B.", NUMBER, _AB_NUM),
    _math.expression("math.modulo", "modulo", "The remainder of A divided by B.", NUMBER, _AB_NUM),
    _math.expression("math.power", "power", "A raised to the power B.", NUMBER, (("BASE", NUMBER), ("EXPONENT", NUMBER))),
    _math.expression("math.abs", "absolute value", "The absolute value of a number.", NUMBER, (("VALUE", NUMBER),)),
    _math.expression("math.min", "minimum", "The smaller of two numbers.", NUMBER, _AB_NUM),
    _math.expression("math.max", "maximum", "The larger of two numbers.", NUMBER, _AB_NUM),
    _math.expression(
        "math.random", "random", "A random integer between a minimum and a maximum.", NUMBER, (("MIN", NUMBER), ("MAX", NUMBER))
    ),
    _math.expression(
        "math.constrain",
        "constrain",
        "Limits a number to a low and high bound.",
        NUMBER,
        (("VALUE", NUMBER), ("LOW", NUMBER), ("HIGH", NUMBER)),
    ),
    _math.expression("math.round", "round", "Rounds a number to the nearest integer.", NUMBER, (("VALUE", NUMBER),)),
    _math.expression("math.floor", "floor", "Rounds a number down.", NUMBER, (("VALUE", NUMBER),)),
    _math.expression("math.ceil", "ceiling", "Rounds a number up.", NUMBER, (("VALUE", NUMBER),)),
    _math.expression("math.sqrt", "square root", "The square root of a number.", NUMBER, (("VALUE", NUMBER),)),
)

TEXTS = (
    _text.value(
        "text.literal", "text", "A text literal.", TEXT, inputs=(("VALUE", TEXT),),
        implemented_as="text_literal",
    ),
    _text.expression("text.join", "join", "Concatenates two pieces of text.", TEXT, (("A", TEXT), ("B", TEXT))),
    _text.expression(
        "text.length", "length", "The number of characters in some text.", NUMBER, (("TEXT", TEXT),),
        implemented_as="text_length",
    ),
    _text.expression(
        "text.contains", "contains", "True when text contains a search string.", BOOLEAN, (("TEXT", TEXT), ("SEARCH", TEXT))
    ),
    _text.expression(
        "text.index_of",
        "find position",
        "The position of the first occurrence of a search string in some text, or -1.",
        NUMBER,
        (("TEXT", TEXT), ("SEARCH", TEXT)),
        implemented_as="text_index_of",
    ),
    _text.expression(
        "text.substring",
        "substring",
        "A slice of some text between two positions.",
        TEXT,
        (("TEXT", TEXT), ("FROM", NUMBER), ("TO", NUMBER)),
        implemented_as="text_substring",
    ),
    _text.expression(
        "text.char_at", "character at", "The character at a position.", TEXT, (("TEXT", TEXT), ("INDEX", NUMBER))
    ),
    _text.expression(
        "text.replace",
        "replace",
        "Replaces every occurrence of one string with another.",
        TEXT,
        (("TEXT", TEXT), ("FROM", TEXT), ("TO", TEXT)),
    ),
    _text.expression("text.upper", "uppercase", "Text converted to upper case.", TEXT, (("TEXT", TEXT),)),
    _text.expression("text.lower", "lowercase", "Text converted to lower case.", TEXT, (("TEXT", TEXT),)),
    _text.expression("text.from_number", "number to text", "A number written as text.", TEXT, (("VALUE", NUMBER),)),
    _text.expression("text.to_number", "text to number", "Text parsed as a number.", NUMBER, (("TEXT", TEXT),)),
)

VARIABLES = (
    # QUALIFIER (none / static / const / static const) and TYPE (a C++ type
    # spelling token) are dropdown fields; INITIAL is OPTIONAL - an empty
    # socket declares the variable without a value (`String message;`).
    # `variables.get` reads a local, a parameter or a constant.
    _variables.statement(
        "variables.declare",
        "declare variable",
        "Declares a variable of a type, optionally static/const and optionally with an initial value.",
        (("QUALIFIER", TEXT), ("TYPE", TEXT), ("NAME", TEXT), ("INITIAL", ANY)),
        implemented_as="variables_declare",
    ),
    _variables.value(
        "variables.get", "get variable", "Reads a variable.", ANY, inputs=(("NAME", TEXT),),
        implemented_as="variables_get",
    ),
    # OPERATOR is `=`, `+=` or `-=` and is kept as chosen - `x += v` is never `x = v`.
    _variables.statement(
        "variables.set",
        "set variable",
        "Assigns a value to a variable, or adds to / subtracts from it.",
        (("NAME", TEXT), ("OPERATOR", TEXT), ("VALUE", ANY)),
        implemented_as="variables_set",
    ),
    # Postfix `x++;` / `x--;` - what a `for` loop's step almost always is.
    _variables.statement(
        "variables.update",
        "step variable",
        "Adds one to (++) or subtracts one from (--) a variable.",
        (("NAME", TEXT), ("OPERATOR", TEXT)),
        implemented_as="variables_update",
    ),
    _variables.statement(
        "variables.increment", "increment", "Adds an amount to a numeric variable.", (("NAME", TEXT), ("BY", NUMBER))
    ),
    _variables.statement(
        "variables.decrement", "decrement", "Subtracts an amount from a numeric variable.", (("NAME", TEXT), ("BY", NUMBER))
    ),
)

FUNCTIONS = (
    _functions.container(
        "functions.define",
        "define function",
        "Defines a reusable function with parameters and a body.",
        (("NAME", TEXT), ("PARAMETERS", LIST), ("BODY", BODY)),
    ),
    _functions.statement(
        "functions.call", "call function", "Calls a function for its effect.", (("NAME", TEXT), ("ARGUMENTS", LIST))
    ),
    _functions.expression(
        "functions.call_value",
        "call function (value)",
        "Calls a function and uses its return value.",
        ANY,
        (("NAME", TEXT), *_CALL_ARGS),
        op="functions.call",
        implemented_as="call_function_value",
    ),
    _functions.value("functions.parameter", "parameter", "Reads a parameter inside a function.", ANY, inputs=(("NAME", TEXT),)),
    _functions.statement("functions.return", "return", "Returns a value from a function.", (("VALUE", ANY),)),
    # A value-less `return;` — how a guard clause rejects its input and leaves
    # a void function early. Distinct from `functions.return` above, which
    # returns a value and stays CATALOGED.
    _functions.statement(
        "functions.return_void",
        "return",
        "Leaves the current function immediately, returning nothing.",
        implemented_as="return_void",
    ),
    # --- no-device/Blockly-integration correction ---------------------------
    # Two deliberately narrow, IMPLEMENTED additions, distinct from the two
    # generic CATALOGED entries above. `functions.define`/`functions.call`
    # model authoring a brand-new function and calling it with an arbitrary
    # argument list — a materially different, still-unimplemented capability.
    # These model the two things the semantic layer's `functions.implementation`
    # and `CallStatement` actually need today: a container for the body of an
    # EXISTING, already-named function (no NAME/PARAMETERS operands — the
    # function's identity is fixed by which section is open, not authored
    # here), and a call to an existing function with no arguments. See
    # `app/build/semantic/operations.py` and `models.py`.
    _functions.container(
        "functions.implementation",
        "function body",
        "The body of an existing named function or callback, whose signature is fixed by the firmware.",
        (("BODY", BODY),),
        implemented_as="function_implementation",
    ),
    _functions.statement(
        "functions.call_existing",
        "call function",
        "Calls an existing, already-defined function, with any arguments, for its effect.",
        (("NAME", TEXT), *_CALL_ARGS),
        implemented_as="call_existing_function",
    ),
    # P2: a call on an OBJECT - `message.trim();`, `client.publish(A, B);`.
    # Distinct from `functions.call_existing`, which names a function: the
    # receiver is part of the meaning and would be lost if the two were merged.
    _functions.statement(
        "functions.call_method",
        "call method",
        "Calls a method on an object, with any arguments, for its effect.",
        (("RECEIVER", TEXT), ("METHOD", TEXT), *_CALL_ARGS),
        implemented_as="call_method",
    ),
)

LISTS = (
    _lists.expression("lists.create", "create list", "Builds a list from items.", LIST, (("ITEMS", ANY),)),
    _lists.expression("lists.get", "get item", "The item at an index.", ANY, (("LIST", LIST), ("INDEX", NUMBER))),
    _lists.statement(
        "lists.set", "set item", "Replaces the item at an index.", (("LIST", LIST), ("INDEX", NUMBER), ("VALUE", ANY))
    ),
    _lists.statement("lists.add", "add item", "Appends an item to a list.", (("LIST", LIST), ("VALUE", ANY))),
    _lists.statement("lists.remove", "remove item", "Removes the item at an index.", (("LIST", LIST), ("INDEX", NUMBER))),
    _lists.expression("lists.length", "length", "The number of items in a list.", NUMBER, (("LIST", LIST),)),
    _lists.expression(
        "lists.find", "find item", "The index of a value in a list.", NUMBER, (("LIST", LIST), ("VALUE", ANY))
    ),
    _lists.expression(
        "lists.sublist",
        "sublist",
        "A slice of a list between two indexes.",
        LIST,
        (("LIST", LIST), ("FROM", NUMBER), ("TO", NUMBER)),
    ),
)

BLOCKS = LOGIC + LOOPS + MATH + TEXTS + VARIABLES + FUNCTIONS + LISTS
