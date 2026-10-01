"""The semantic IR -> Arduino C++ boundary (Phase B6).

    SemanticProgram / SemanticSection / SemanticStatement
              |
              v
    generate_cpp(program)             (this module)
              |
              v
    Arduino C++ source text

THE OTHER END OF `analyzer.py`. B3 reads a deliberately tiny subset of C++ and
carries everything else verbatim; this module writes that subset back out and
carries everything else verbatim. It is the last link of the loop the earlier
phases built:

    .ino -> B1 sections -> B3 IR -> B4 Blockly -> B5 IR -> B6 .ino

THIS IS NOT A COMPILER BACKEND, for the same reason `analyzer.py` is not a
frontend. It renders five operations and two leaf value forms. It resolves no
names (`START_BUTTON` is written as `START_BUTTON`, never as 32), evaluates no
constants, knows no types, emits no includes, declares nothing, and reformats
nothing it did not generate. Anything outside the supported subset reached this
module as an `UnsupportedStatement` and leaves it as the exact characters it
arrived with.

PROVENANCE IS NOT THE OUTPUT. An `OperationStatement` may carry the source it
was read from, and this module never writes it: a supported statement's C++ is
generated from its operation and its values, every time. That is what makes a
statement authored in the editor — which has no source text at all, and since
B6 exists no longer needs any — generate exactly like one read from firmware.
`OperationStatement.text` is therefore provenance for a reader, not a cached
rendering, and nothing here can fall back to it.

    source-backed operation   the original source is its provenance
    authored operation        the operation and its values ARE the source

EXACT PRESERVATION IS THE LOAD-BEARING PROPERTY. Real firmware is mostly
constructs the IR does not model — this panel's Wi-Fi setup, its MQTT client,
its callback, its helpers, its globals. Every one of those is an
`UnsupportedStatement`, and its text is emitted CHARACTER FOR CHARACTER: not
re-parsed, not reformatted, not normalized, not commented out, not dropped, and
never translated. The one thing this module adds to such a fragment is the
indentation B3 stripped from its first line when it lifted the statement out of
a body (see `_placed`), so a body's contents line up where they came from.

ORDER IS THE DOCUMENT'S. Sections are written in the program's order and
statements in their section's order, generated and preserved alike. Nothing is
sorted, grouped by kind, hoisted or moved — `supported / unsupported /
supported` comes out as exactly that, in that order.

PURE, ISOLATED, STDLIB-ONLY, DETERMINISTIC. `generate_cpp` takes a
`SemanticProgram` and returns a string; nothing else. No filesystem, no
process, no `arduino-cli`, no dynamic execution, no Blockly, no session, no
panel, no hardware, no clock, no randomness. The same program always produces
a byte-identical string. Compiling or flashing what it returns is B7's job and
nothing here reaches toward it.
"""

from __future__ import annotations

import math

from app.build.semantic.emissions import (
    CallEmission,
    CppEmissionTable,
    FunctionEmission,
    MethodEmission,
    default_cpp_emissions,
)
from app.build.semantic.errors import (
    InvalidContainerError,
    InvalidSemanticValueError,
    MissingOperationArgumentError,
    UnsupportedOperationError,
)
from app.build.semantic.models import (
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
    SemanticValue,
    SymbolValue,
    TernaryValue,
    UnsupportedStatement,
    UpdateStatement,
    VariableDeclaration,
)
from app.build.semantic.operations import (
    OperationForm,
    SemanticOperation,
    SemanticOperationRegistry,
    SemanticType,
    default_semantic_operations,
)

#: One level of body indentation. A fixed, conventional two spaces — the same
#: width the committed firmware and the default Build Mode project are written
#: with. This is the whole of this module's "formatting": there is no general
#: formatter here, no line-width rule, no brace-style option and no alignment
#: pass, because a deterministic conventional layout is all a round trip needs.
INDENT = "  "

#: What separates one generated section from the next.
SECTION_SEPARATOR = "\n\n"

#: The escapes written back into a text literal. The inverse of the analyzer's
#: `_ESCAPES`, kept small and explicit for the same reason: an escape nobody
#: listed is not guessed at. NUL is written in its three-digit octal form so a
#: following digit can never extend the escape — `"\0" + "1"` must not become
#: the single character `\01`.
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\0": "\\000",
}


def generate_cpp(
    program: SemanticProgram,
    *,
    operations: SemanticOperationRegistry = default_semantic_operations,
    emissions: CppEmissionTable = default_cpp_emissions,
) -> str:
    """Write `program` as Arduino C++ source.

    Every section produces one block of source, in the program's own order;
    a section that says nothing (an empty run of whitespace B3 represented as
    a section with no statements) produces none, rather than a blank the
    document never had. The result ends in a newline, as a source file does,
    and is empty only for a program that says nothing at all.

    `operations` and `emissions` are injectable for the reason every other
    table in this codebase is: a test, or a later phase with a different
    subset, generates against its own tables without this module reaching for
    a global. They are cross-checked on every call — an operation is resolved
    through the REGISTRY, never trusted from the statement that names it, so a
    program carrying an operation the registry does not declare is reported
    instead of written.
    """
    if not isinstance(program, SemanticProgram):
        raise TypeError(f"expected a SemanticProgram, got {type(program).__name__}")
    if not isinstance(operations, SemanticOperationRegistry):
        raise TypeError(f"expected a SemanticOperationRegistry, got {type(operations).__name__}")
    if not isinstance(emissions, CppEmissionTable):
        raise TypeError(f"expected a CppEmissionTable, got {type(emissions).__name__}")

    blocks = [_section(section, operations, emissions) for section in program.sections]
    written = SECTION_SEPARATOR.join(block for block in blocks if block)
    return f"{written}\n" if written else ""


# --- sections ---------------------------------------------------------------


def _section(
    section: SemanticSection,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """One section as source: a function definition, or top-level text."""
    if section.operation is None:
        # No container operation, so there is no function to write and nothing
        # to indent into. The section's statements are top-level source the IR
        # never interpreted — a header comment, includes, constants, a helper,
        # a callback — and they are emitted exactly as they arrived.
        return "\n".join(
            _placed(statement, "", operations, emissions) for statement in section.statements
        )

    declared = _declared(section.operation, operations)
    if declared.form is not OperationForm.CONTAINER:
        raise InvalidContainerError(
            f"{section.section_id}: the registry calls {declared.operation_id} a "
            f"{declared.form.value}, but it is this section's container"
        )
    if section.signature is not None:
        # A generic named-function container (`functions.implementation`):
        # the declarator is preserved verbatim on the section, never looked
        # up by operation id — see `SemanticSection.signature`. The emissions
        # table is not consulted at all for this shape.
        header = section.signature
    else:
        emission = emissions.require(declared.operation_id)
        if not isinstance(emission, FunctionEmission):
            raise InvalidContainerError(
                f"{declared.operation_id}: a container is written as a function definition, "
                f"but its emission is a {type(emission).__name__}"
            )
        header = emission.signature
    body = [
        _placed(statement, INDENT, operations, emissions) for statement in section.statements
    ]
    return "\n".join([f"{header} {{", *body, "}"])


# --- statements -------------------------------------------------------------


def _placed(
    statement: SemanticStatement,
    indent: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """One statement's source, indented into the body it belongs to.

    ONLY THE FIRST LINE IS INDENTED, and that is deliberate rather than
    convenient. A preserved fragment's own lines are source this module must
    not touch — a `while` block's inner lines, a helper's body, a multi-line
    comment — and re-indenting them would be the normalization this phase
    refuses. What B3 removed when it lifted the statement out of its body was
    the leading whitespace of its FIRST line and nothing else, so restoring
    exactly that puts the fragment back where it was, character for character,
    for any source indented the way this generator indents.

    A `ConditionalStatement` and a `ForStatement` are the exceptions: each is
    multi-line by construction (a head, an indented body, a closing brace), so
    `_if_lines`/`_for_lines` bake every line's indent in themselves, first line
    included, and are joined directly rather than routed through the
    single-line rule above.
    """
    if isinstance(statement, ConditionalStatement):
        return "\n".join(_if_lines(statement, indent, operations, emissions))
    if isinstance(statement, ForStatement):
        return "\n".join(_for_lines(statement, indent, operations, emissions))
    return indent + _statement(statement, operations, emissions)


def _statement(
    statement: SemanticStatement,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    if isinstance(statement, UnsupportedStatement):
        # The exact characters B3 carried. This is the whole reason a round
        # trip through five operations can survive real firmware.
        return statement.source_text
    if isinstance(statement, OperationStatement):
        return _call(statement, operations, emissions)
    if isinstance(statement, CallStatement):
        arguments = _arguments(statement.arguments, statement.function_name, operations, emissions)
        return f"{statement.function_name}({arguments});"
    if isinstance(statement, MethodCallStatement):
        arguments = _arguments(
            statement.arguments,
            f"{statement.receiver}.{statement.method_name}",
            operations,
            emissions,
        )
        return f"{statement.receiver}.{statement.method_name}({arguments});"
    if isinstance(statement, AssignmentStatement):
        value = _value(statement.value, statement.target, operations, emissions)
        return f"{statement.target} {statement.operator} {value};"
    if isinstance(statement, VariableDeclaration):
        # Qualifiers and the type spelling are written exactly as the
        # declaration carries them: `static unsigned long lastEdge = 0;` is
        # never shortened to `unsigned long lastEdge = 0;`.
        head = " ".join([*statement.qualifiers, statement.cpp_type, statement.name])
        if statement.initializer is None:
            return f"{head};"
        initializer = _value(statement.initializer, statement.name, operations, emissions)
        return f"{head} = {initializer};"
    if isinstance(statement, UpdateStatement):
        return f"{statement.target}{statement.operator};"
    if isinstance(statement, ReturnStatement):
        return "return;"
    raise InvalidContainerError(
        f"no generation rule for a {type(statement).__name__}; the IR states a statement "
        "as an understood operation, a call, a conditional, a local declaration, a "
        "return, an assignment, a method call, or as source carried verbatim, and nothing else"
    )


def _arguments(
    arguments: tuple[SemanticValue, ...],
    where: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """A call's argument list, in order, comma-separated."""
    return ", ".join(_value(argument, where, operations, emissions) for argument in arguments)


def _if_lines(
    statement: ConditionalStatement,
    indent: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> list[str]:
    """`if (COND) { ... }` and its `else if` / `else` chain, as indented lines.

    Written from the linked chain: each link is `} else if (COND) {` on the line
    of the previous closing brace, and a final `else` is `} else {`. Recurses
    through `_placed` for every body, so a nested statement of any kind indents
    correctly without this function knowing anything about nesting.
    """
    inner = indent + INDENT
    lines: list[str] = []
    prefix = ""  # what precedes `if`: nothing for the first link, `} else ` after
    link = statement
    while True:
        condition = _value(link.condition, "condition", operations, emissions)
        lines.append(f"{indent}{prefix}if ({condition}) {{")
        lines.extend(_placed(item, inner, operations, emissions) for item in link.body)
        if link.else_if is not None:
            link, prefix = link.else_if, "} else "
            continue
        if link.else_body is not None:
            lines.append(f"{indent}}} else {{")
            lines.extend(_placed(item, inner, operations, emissions) for item in link.else_body)
        break
    lines.append(f"{indent}}}")
    return lines


def _for_lines(
    statement: ForStatement,
    indent: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> list[str]:
    """`for (INIT; COND; STEP) { ... }` as indented lines.

    Each header part is the SAME statement/value text a body would get, with the
    statement's own trailing `;` taken off (the header supplies the separators).
    An empty part is written empty: `for (;;)` stays `for (;;)`.
    """
    init = "" if statement.init is None else _statement(statement.init, operations, emissions)[:-1]
    condition = (
        ""
        if statement.condition is None
        else _value(statement.condition, "for condition", operations, emissions)
    )
    step = "" if statement.step is None else _statement(statement.step, operations, emissions)[:-1]
    header = f"for ({init}; {condition}; {step})" if (init or condition or step) else "for (;;)"
    inner = indent + INDENT
    lines = [f"{indent}{header} {{"]
    lines.extend(_placed(item, inner, operations, emissions) for item in statement.body)
    lines.append(f"{indent}}}")
    return lines


def _call(
    statement: OperationStatement,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """One understood statement as a call, written from its meaning alone.

    Built from the operation and its values — never from `statement.text`,
    which is provenance (see the module docstring). Operands are written in
    the REGISTRY's declared parameter order and addressed BY NAME, so a table
    that lists them in another order could not silently swap two operands of
    the same shape.
    """
    declared = _declared(statement.operation, operations)
    if declared.form is not OperationForm.STATEMENT:
        raise InvalidContainerError(
            f"{declared.operation_id}: the registry calls this a {declared.form.value}, "
            "but it stands here as a statement in a body"
        )
    emission = emissions.require(declared.operation_id)
    if not isinstance(emission, CallEmission):
        raise InvalidContainerError(
            f"{declared.operation_id}: a statement is written as a call, but its emission "
            f"is a {type(emission).__name__}"
        )

    written: list[str] = []
    for parameter in declared.parameters:
        value = statement.value(parameter.name)
        if value is None:
            raise MissingOperationArgumentError(
                f"{declared.operation_id}: no {parameter.name} operand to write, which the "
                "registry declares for this operation"
            )
        written.append(
            _value(value, f"{declared.operation_id}.{parameter.name}", operations, emissions)
        )
    extra = sorted(
        argument.name
        for argument in statement.arguments
        if declared.parameter(argument.name) is None
    )
    if extra:
        # Writing the call without them would silently drop an operand, which
        # is the one thing this module never does.
        raise UnsupportedOperationError(
            f"{declared.operation_id}: operand(s) {extra} belong to no parameter the "
            "registry declares, so they cannot be written"
        )
    return f"{emission.call_name}({', '.join(written)});"


def _declared(
    operation: SemanticOperation, operations: SemanticOperationRegistry
) -> SemanticOperation:
    """The registry's own definition of the operation a construct names.

    THE IDENTITY CHECK. A statement carries its `SemanticOperation` object, and
    this module deliberately does not take it at its word: the id is resolved
    through the registry, and the registry's definition is what the call is
    written from. An operation nobody declared can therefore never reach C++,
    however it was constructed.
    """
    found = operations.operation(operation.operation_id)
    if found is None:
        raise UnsupportedOperationError(
            f"{operation.operation_id}: the registry declares no such operation, so there "
            "is no meaning to write"
        )
    return found


# --- values -----------------------------------------------------------------


#: Binding strength of each value form, tightest last. A child whose strength is
#: BELOW what its parent slot requires is parenthesized, so the text always reads
#: back as the tree it was written from - and a child that binds tightly enough
#: is written bare, so `a == b && !c` stays exactly that.
_TERNARY, _OR, _AND, _COMPARISON, _SUM, _NOT, _ATOM = 1, 2, 3, 4, 5, 6, 9


def _strength(value: SemanticValue) -> int:
    if isinstance(value, TernaryValue):
        return _TERNARY
    if isinstance(value, LogicalValue):
        return _OR if value.operator == "||" else _AND
    if isinstance(value, ComparisonValue):
        return _COMPARISON
    if isinstance(value, ArithmeticValue):
        return _SUM
    if isinstance(value, NotValue):
        return _NOT
    return _ATOM


def _value(
    value: SemanticValue,
    where: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """One value as a C++ expression.

    A symbol is written as the NAME it is - `START_BUTTON` stays
    `START_BUTTON`, never 32, because the IR does not resolve names and
    generating a resolved value would put a number in firmware the student
    never wrote.

    PARENTHESES FOLLOW THE TREE, NOT THE ORIGINAL TEXT. Each operand is wrapped
    exactly when it binds more loosely than the slot it stands in requires
    (`_operand`), following C++ precedence: `!`, then `+`, then comparison, `&&`,
    `||`, `?:`. Left-associative operators write a left child of their own
    strength bare and wrap a right one; a ternary's else-branch is bare because
    `?:` associates to the right.
    """
    if isinstance(value, SymbolValue):
        return value.name
    if isinstance(value, LiteralValue):
        return _literal(value, where)
    if isinstance(value, ComparisonValue):
        left = _operand(value.left, _SUM, where, operations, emissions)
        right = _operand(value.right, _SUM, where, operations, emissions)
        return f"{left} {value.operator} {right}"
    if isinstance(value, ArithmeticValue):
        left = _operand(value.left, _SUM, where, operations, emissions)
        right = _operand(value.right, _SUM + 1, where, operations, emissions)
        return f"{left} {value.operator} {right}"
    if isinstance(value, LogicalValue):
        own = _OR if value.operator == "||" else _AND
        left = _operand(value.left, own, where, operations, emissions)
        right = _operand(value.right, own + 1, where, operations, emissions)
        return f"{left} {value.operator} {right}"
    if isinstance(value, NotValue):
        return "!" + _operand(value.operand, _NOT, where, operations, emissions)
    if isinstance(value, TernaryValue):
        condition = _operand(value.condition, _OR, where, operations, emissions)
        if_true = _operand(value.if_true, _OR, where, operations, emissions)
        if_false = _operand(value.if_false, _TERNARY, where, operations, emissions)
        return f"{condition} ? {if_true} : {if_false}"
    if isinstance(value, OperationValue):
        return _method_call(value, where, operations, emissions)
    if isinstance(value, CallValue):
        arguments = _arguments(value.arguments, value.function_name, operations, emissions)
        return f"{value.function_name}({arguments})"
    raise InvalidSemanticValueError(f"{where}: no C++ form for a {type(value).__name__}")


def _operand(
    value: SemanticValue,
    required: int,
    where: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """`value`, parenthesized when it binds more loosely than `required`."""
    text = _value(value, where, operations, emissions)
    return f"({text})" if _strength(value) < required else text


def _method_call(
    value: OperationValue,
    where: str,
    operations: SemanticOperationRegistry,
    emissions: CppEmissionTable,
) -> str:
    """A VALUE operation as `RECEIVER.method(ARGS)` — see `MethodEmission`.

    Resolved through the registry exactly like a statement's call, never
    trusted from the value that names it. A text LITERAL receiver is written
    as `String("...")`: a bare C++ string literal is a `const char *`, which
    has no `indexOf`, so writing it bare would be a compile error the student
    never caused.
    """
    declared = _declared(value.operation, operations)
    if declared.form is not OperationForm.VALUE:
        raise InvalidContainerError(
            f"{declared.operation_id}: the registry calls this a {declared.form.value}, "
            "but it stands here as a value"
        )
    emission = emissions.require(declared.operation_id)
    if not isinstance(emission, MethodEmission):
        raise InvalidContainerError(
            f"{declared.operation_id}: a value operation is written as a method, but its "
            f"emission is a {type(emission).__name__}"
        )
    written: list[str] = []
    for parameter in declared.parameters:
        operand = value.value(parameter.name)
        if operand is None:
            raise MissingOperationArgumentError(
                f"{declared.operation_id}: no {parameter.name} operand to write, which the "
                "registry declares for this operation"
            )
        written.append(
            _value(operand, f"{declared.operation_id}.{parameter.name}", operations, emissions)
        )
    receiver_value = value.value(declared.parameters[0].name)
    receiver = written[0]
    if isinstance(receiver_value, LiteralValue):
        receiver = f"String({receiver})"
    elif _strength(receiver_value) < _ATOM:
        receiver = f"({receiver})"
    return f"{receiver}.{emission.method_name}({', '.join(written[1:])})"


def _literal(value: LiteralValue, where: str) -> str:
    """One literal in its own declared type's C++ spelling.

    Type-driven rather than value-driven: `LiteralValue` carries the type it
    means and validates its Python value against it, so a boolean is written
    as `true`/`false` and never as `1`, and nothing here converts between
    types. This is not `LiteralValue.source_text`, which is diagnostics and
    says so — a text literal's source form is unescaped and would put a raw
    newline inside a C++ string.
    """
    if value.value_type is SemanticType.BOOLEAN:
        return "true" if value.value else "false"
    if value.value_type is SemanticType.TEXT:
        return f'"{_escaped(str(value.value), where)}"'
    if isinstance(value.value, float) and not math.isfinite(value.value):
        raise InvalidSemanticValueError(
            f"{where}: {value.value!r} has no C++ literal — writing one would invent a "
            "value the source never held"
        )
    return str(value.value)


def _escaped(text: str, where: str) -> str:
    """A text literal's contents, escaped back into a C++ string body.

    A character with no listed escape is refused rather than written raw or
    guessed at: a control byte inside a string literal is source nobody typed,
    and this module does not invent one.
    """
    written: list[str] = []
    for character in text:
        escape = _ESCAPES.get(character)
        if escape is not None:
            written.append(escape)
            continue
        if ord(character) < 0x20 or ord(character) == 0x7F:
            raise InvalidSemanticValueError(
                f"{where}: no C++ escape is declared for character {character!r}"
            )
        written.append(character)
    return "".join(written)
