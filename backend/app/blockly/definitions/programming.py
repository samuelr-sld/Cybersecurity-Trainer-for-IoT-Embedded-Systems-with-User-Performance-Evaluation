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

LOGIC = (
    _logic.value("logic.true", "true", "The boolean literal true.", BOOLEAN),
    _logic.value("logic.false", "false", "The boolean literal false.", BOOLEAN),
    _logic.expression("logic.not", "not", "Logical negation.", BOOLEAN, (("VALUE", BOOLEAN),)),
    _logic.expression("logic.and", "and", "True when both operands are true.", BOOLEAN, _AB_BOOL),
    _logic.expression("logic.or", "or", "True when either operand is true.", BOOLEAN, _AB_BOOL),
    _logic.expression("logic.equal", "equal", "True when both operands are equal.", BOOLEAN, _AB_ANY),
    _logic.expression("logic.not_equal", "not equal", "True when the operands differ.", BOOLEAN, _AB_ANY),
    _logic.expression("logic.greater", "greater than", "True when A is greater than B.", BOOLEAN, _AB_NUM),
    _logic.expression("logic.less", "less than", "True when A is less than B.", BOOLEAN, _AB_NUM),
    _logic.expression(
        "logic.greater_equal", "greater or equal", "True when A is greater than or equal to B.", BOOLEAN, _AB_NUM
    ),
    _logic.expression(
        "logic.less_equal", "less or equal", "True when A is less than or equal to B.", BOOLEAN, _AB_NUM
    ),
    _logic.statement(
        "logic.if", "if", "Runs a body when a condition is true.", (("CONDITION", BOOLEAN), ("DO", BODY))
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
    _loops.statement(
        "loops.for",
        "for",
        "Counts a variable from a start to an end value by a step.",
        (("VARIABLE", TEXT), ("FROM", NUMBER), ("TO", NUMBER), ("STEP", NUMBER), ("DO", BODY)),
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
    _math.value("math.number", "number", "A numeric literal.", NUMBER, inputs=(("VALUE", NUMBER),)),
    _math.expression("math.add", "add", "A plus B.", NUMBER, _AB_NUM),
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
    _text.value("text.literal", "text", "A text literal.", TEXT, inputs=(("VALUE", TEXT),)),
    _text.expression("text.join", "join", "Concatenates two pieces of text.", TEXT, (("A", TEXT), ("B", TEXT))),
    _text.expression("text.length", "length", "The number of characters in some text.", NUMBER, (("TEXT", TEXT),)),
    _text.expression(
        "text.contains", "contains", "True when text contains a search string.", BOOLEAN, (("TEXT", TEXT), ("SEARCH", TEXT))
    ),
    _text.expression(
        "text.substring",
        "substring",
        "A slice of some text between two positions.",
        TEXT,
        (("TEXT", TEXT), ("FROM", NUMBER), ("TO", NUMBER)),
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
    _variables.statement(
        "variables.declare",
        "declare variable",
        "Declares a variable with an optional initial value.",
        (("NAME", TEXT), ("INITIAL", ANY)),
    ),
    _variables.value("variables.get", "get variable", "Reads a variable.", ANY, inputs=(("NAME", TEXT),)),
    _variables.statement(
        "variables.set", "set variable", "Assigns a value to a variable.", (("NAME", TEXT), ("VALUE", ANY))
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
        (("NAME", TEXT), ("ARGUMENTS", LIST)),
        op="functions.call",
    ),
    _functions.value("functions.parameter", "parameter", "Reads a parameter inside a function.", ANY, inputs=(("NAME", TEXT),)),
    _functions.statement("functions.return", "return", "Returns a value from a function.", (("VALUE", ANY),)),
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
