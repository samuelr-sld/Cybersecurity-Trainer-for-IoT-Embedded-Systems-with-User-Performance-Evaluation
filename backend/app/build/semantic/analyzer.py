"""The C++ -> semantic IR boundary (Phase B3).

    BuildDocument / CodeSection[]     (app/build/discovery/ — B1)
              |
              v
    analyze_document(document)        (this module)
              |
              v
    SemanticProgram / SemanticSection / SemanticStatement

THIS IS NOT A C++ COMPILER FRONTEND, and even less of one than B1's scanner.
B1 answers a structural question about a whole file; this module only
recognizes a deliberately tiny surface subset inside the two bodies it
understands, and represents literally everything else verbatim. It does not
resolve names, evaluate constants, follow includes, understand types,
namespaces, templates, classes, operators or control flow, and it never
needs to: an unrecognized construct is an `UnsupportedStatement`, which is a
first-class representation rather than a failure (see `models.py`).

WHAT IS RECOGNIZED, AND NOTHING ELSE:

  * a SETUP or LOOP section becomes a container section; every other
    discovered kind (global declarations, helper functions, callbacks)
    becomes one unsupported section carrying its exact text;
  * inside such a body, a statement of the exact shape
    `name(argument, ...);` where `name` is a BARE identifier in the call
    table below and every argument is a literal or a named constant;
  * a zero-argument call to any other bare name (`motorStart();`), a plain
    `if (COMPARISON) { ... }` head, a void `return;`, and a single initialized
    local `String|int|bool NAME = VALUE;` — where a condition or initializer
    may use the value layer's small expression grammar (`_ExpressionReader`:
    `==`/`!=`/`<=`, `+`, and the `String` methods `indexOf`/`substring`/
    `length`);
  * anything else in the body — other control flow, an uninitialized or
    qualified declaration, an assignment, a statement-level method call
    (`client.publish(...)`), a call with an expression argument
    (`digitalWrite(PIN, run ? HIGH : LOW)`) — keeps its exact source text and
    a reason.

ALL OR NOTHING PER STATEMENT. If any argument of a recognized call is not
representable, the WHOLE statement becomes unsupported. A half-understood
statement would hand B4's Blockly adapter a block it cannot finish building
and a fragment of meaning nobody can act on; carrying the original text is
both simpler and more honest.

WHERE THE C++ LIVES. `_CALL_OPERATIONS` below is the only place in this
package that knows what C++ looks like — `operations.py` is language-neutral
and `models.py` holds no syntax at all. A second source language, or B5's
generator going the other way, is a table beside this one rather than a
change to the IR.

PURE, ISOLATED, STDLIB-ONLY. `analyze_document` takes a `BuildDocument` and
returns a `SemanticProgram`; nothing else. No filesystem, no subprocess, no
dynamic execution, and no import of Blockly, `app.build.models`,
`app.build.workspace`, `app.build_sessions`, `app.panels`, `app.hardware` or
`app.scenarios`. The one dependency is `app.build.discovery`, in the one
permitted direction, and `tests/test_build_semantic.py` asserts that
statically. Deterministic: the same document always produces an equal
`SemanticProgram`.

LEXING IS NOT REIMPLEMENTED HERE. Comment/string/char-literal boundaries come
from `app.build.discovery.code_mask`, the public name for the helper B1's own
two passes already share. One implementation of "is this character real
code?" exists in this codebase, not three.
"""

from __future__ import annotations

import re

from app.build.discovery import BuildDocument, CodeSection, SectionKind, code_mask
from app.build.semantic.errors import SemanticAnalysisError, SemanticModelError
from app.build.semantic.models import (
    COMPARISON_OPERATORS,
    ArithmeticValue,
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    OperationStatement,
    OperationValue,
    ReturnStatement,
    SemanticArgument,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticValue,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
    VariableDeclaration,
)
from app.build.semantic.operations import (
    FUNCTIONS_IMPLEMENTATION,
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TEXT_INDEX_OF,
    TEXT_LENGTH,
    TEXT_SUBSTRING,
    TIME_DELAY,
    OperationForm,
    SemanticOperationRegistry,
    SemanticType,
    default_semantic_operations,
)

#: Which discovered section kinds have a container operation. GLOBAL_DECLARATIONS
#: is the one kind still represented whole and uninterpreted — a run of
#: top-level declarations has no single body to recognize statements inside.
#: HELPER_FUNCTION and CALLBACK map to the SAME generic `functions.implementation`
#: operation (see `operations.py`): every named function becomes a representable
#: container, whatever it is called, because the operation carries no identity
#: of its own — see `_analyze_section`, which is what attaches the one thing
#: that DOES vary per function (its preserved C++ signature).
_SECTION_OPERATIONS = {
    SectionKind.SETUP: PROGRAM_SETUP,
    SectionKind.LOOP: PROGRAM_LOOP,
    SectionKind.HELPER_FUNCTION: FUNCTIONS_IMPLEMENTATION,
    SectionKind.CALLBACK: FUNCTIONS_IMPLEMENTATION,
}

#: Section kinds whose container is a generic named function rather than one
#: of the two fixed Arduino entry points — the ones that need `signature`
#: attached. `program.setup`/`program.loop` keep `signature=None`, unaffected,
#: exactly as before this correction.
_GENERIC_FUNCTION_KINDS = frozenset({SectionKind.HELPER_FUNCTION, SectionKind.CALLBACK})

#: The C++ call name each supported operation is written as. The ONLY
#: C++-aware table in this package, and the reason `operations.py` can stay
#: language-neutral. A name absent from here is not an error — its call is
#: carried verbatim as UNKNOWN_CALL.
_CALL_OPERATIONS = {
    "pinMode": GPIO_PIN_MODE,
    "digitalWrite": GPIO_DIGITAL_WRITE,
    "delay": TIME_DELAY,
}

#: The `String` method each VALUE operation is written as — the receiver is
#: the operation's first operand. The inverse of `emissions.py`'s
#: `MethodEmission` rows, pinned against them by the test suite.
_METHOD_OPERATIONS = {
    "indexOf": TEXT_INDEX_OF,
    "substring": TEXT_SUBSTRING,
    "length": TEXT_LENGTH,
}

#: The C++ type names a local declaration is recognized with. The inverse of
#: `emissions.py`'s `CPP_DECLARATION_TYPES`, pinned by the test suite.
_DECLARATION_TYPES = {
    "String": SemanticType.TEXT,
    "int": SemanticType.NUMBER,
    "bool": SemanticType.BOOLEAN,
}

#: C++'s own fixed control-flow vocabulary. Excluding these by name is not
#: panel knowledge — it is the language's reserved words, the same guard and
#: the same justification B1's analyzer documents for `_CONTROL_KEYWORDS`.
#: Without it, `if (ready) return;` would read as a call to a function named
#: `if` and be reported as an unknown call rather than as not-a-call.
_CONTROL_KEYWORDS = frozenset(
    {"if", "else", "for", "while", "do", "switch", "case", "default", "return", "catch", "try"}
)

_IDENTIFIER_PATTERN = r"[A-Za-z_]\w*"
_INTEGER_PATTERN = r"-?\d+"
_DECIMAL_PATTERN = r"-?(?:\d+\.\d*|\.\d+)"
_STRING_PATTERN = r"\"(?:[^\"\\]|\\.)*\""

#: The escape sequences a text literal's content is unescaped through. Small
#: and explicit: an unlisted escape keeps its backslash rather than being
#: guessed at.
_ESCAPES = {
    "\\\\": "\\",
    '\\"': '"',
    "\\'": "'",
    "\\n": "\n",
    "\\t": "\t",
    "\\r": "\r",
    "\\0": "\0",
}


def analyze_document(
    document: BuildDocument,
    *,
    operations: SemanticOperationRegistry = default_semantic_operations,
) -> SemanticProgram:
    """Represent what `document`'s supported code means.

    Every section of the document produces exactly one `SemanticSection`, in
    document order and under the same `section_id`, so the two
    representations stay addressable by one name. The document itself is not
    read, copied or modified — it keeps its exact source, and so does every
    `CodeSection` in it.

    `operations` is injectable so a test (or a later phase with a narrower
    subset) can analyze against a different table without this module
    reaching for a global. An operation the registry does not declare is
    simply not recognized; the source is carried verbatim instead.
    """
    if not isinstance(document, BuildDocument):
        raise TypeError(f"expected a BuildDocument, got {type(document).__name__}")
    if not isinstance(operations, SemanticOperationRegistry):
        raise TypeError(f"expected a SemanticOperationRegistry, got {type(operations).__name__}")

    source = document.source
    mask = code_mask(source)
    sections = tuple(
        _analyze_section(source, mask, section, operations) for section in document.sections
    )
    return SemanticProgram(sections=sections)


def _analyze_section(
    source: str,
    mask: str,
    section: CodeSection,
    operations: SemanticOperationRegistry,
) -> SemanticSection:
    operation_id = _SECTION_OPERATIONS.get(section.kind)
    operation = None if operation_id is None else operations.operation(operation_id)
    if operation is None:
        return SemanticSection(
            section_id=section.section_id,
            operation=None,
            statements=_whole_section_statement(source, section),
        )
    body_start, body_end = _body_span(source, mask, section)
    statements = _body_statements(source, mask, body_start, body_end, operations)
    # A generic named-function container's identity is its preserved C++
    # declarator — see `SemanticSection.signature`. B1 already discovered it
    # (`CodeSection.signature`, the exact text before the body's opening
    # brace); this is a straight carry-through, never re-derived from source.
    signature = section.signature if section.kind in _GENERIC_FUNCTION_KINDS else None
    return SemanticSection(
        section_id=section.section_id,
        operation=operation,
        statements=statements,
        signature=signature,
    )


def _whole_section_statement(source: str, section: CodeSection) -> tuple[SemanticStatement, ...]:
    """One unsupported statement holding a whole uninterpreted section.

    Empty when the section is only whitespace — the blank run between two
    functions is a real `GLOBAL_DECLARATIONS` section but means nothing, and
    an `UnsupportedStatement` over it would assert that it did.
    """
    text = source[section.start_offset : section.end_offset].strip()
    if not text:
        return ()
    return (UnsupportedStatement(text=text, reason=UnsupportedReason.UNSUPPORTED_SECTION),)


def _body_span(source: str, mask: str, section: CodeSection) -> tuple[int, int]:
    """The half-open range of a function section's body, braces excluded."""
    start = mask.find("{", section.start_offset, section.end_offset)
    if start == -1:
        raise SemanticAnalysisError(
            f"{section.section_id}: a {section.kind.value} section has no body braces"
        )
    end = _matching_brace(mask, start, section.end_offset)
    if end is None:
        raise SemanticAnalysisError(f"{section.section_id}: body braces never close")
    return start + 1, end


def _body_statements(
    source: str,
    mask: str,
    start: int,
    end: int,
    operations: SemanticOperationRegistry,
) -> tuple[SemanticStatement, ...]:
    """Every statement of a body, INCLUDING comment-only gaps between them.

    `_statement_spans` finds statement boundaries on the MASK, which blanks
    every comment to whitespace (`code_mask`) — so a comment sitting between
    two statements, or trailing after the last one, opens no span of its own
    and `_statement_spans` walks straight past it. Without this wrapper, that
    text would not be silently misread; it would be silently GONE — no
    statement carries it, so `generate_cpp` would never write it back. This is
    the one place that notices the gap and turns it into an ordinary
    `UnsupportedStatement`, exactly as if a recognizer had looked at it and
    declined; `NOT_A_CALL` is the closest existing reason, and gap text is
    never a call.

    Shared by a section's own body (`_analyze_section`) and an `if`'s nested
    body (`_try_conditional`) so both round-trip a comment the same way.
    """
    statements: list[SemanticStatement] = []
    cursor = start
    for span_start, span_end in _statement_spans(mask, start, end):
        gap = source[cursor:span_start].strip()
        if gap:
            statements.append(UnsupportedStatement(text=gap, reason=UnsupportedReason.NOT_A_CALL))
        statements.append(_statement(source, mask, span_start, span_end, operations))
        cursor = span_end
    trailing = source[cursor:end].strip()
    if trailing:
        statements.append(UnsupportedStatement(text=trailing, reason=UnsupportedReason.NOT_A_CALL))
    return tuple(statements)


# --- splitting a body into statements ---------------------------------------


def _statement_spans(mask: str, start: int, end: int) -> list[tuple[int, int]]:
    """The half-open span of each top-level statement in a body.

    Boundaries are decided on the MASK, so a `;` inside a string literal or a
    comment can never end a statement. Parenthesis/bracket depth is tracked
    for the same reason a `for (a; b; c)` header must not split into three.

    A `{` reached at statement level opens a nested block (a control-flow
    body, an initializer list). The whole group is consumed and becomes ONE
    span, extended over a following `;` when there is one, so `while (…) { … }`
    and `int table[] = {1, 2};` each come out as a single carried-verbatim
    statement rather than fragments.
    """
    spans: list[tuple[int, int]] = []
    begin: int | None = None
    depth = 0
    i = start
    while i < end:
        char = mask[i]
        if char.isspace():
            i += 1
            continue
        if begin is None:
            begin = i
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "{":
            close = _matching_brace(mask, i, end)
            if close is None:
                raise SemanticAnalysisError(f"nested block at {i} never closes")
            stop = close + 1
            following = _next_code(mask, stop, end)
            if following is not None and mask[following] == ";":
                stop = following + 1
            spans.append((begin, stop))
            begin = None
            depth = 0
            i = stop
            continue
        elif char == ";" and depth == 0:
            spans.append((begin, i + 1))
            begin = None
            i += 1
            continue
        i += 1
    if begin is not None:
        spans.append((begin, end))
    return spans


def _matching_brace(mask: str, start: int, limit: int) -> int | None:
    """The index of the `}` balancing `mask[start] == '{'`, or None."""
    depth = 0
    for i in range(start, limit):
        char = mask[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _next_code(mask: str, start: int, limit: int) -> int | None:
    """The index of the next real, non-whitespace code character, or None."""
    for i in range(start, limit):
        if not mask[i].isspace():
            return i
    return None


# --- recognizing one statement ----------------------------------------------


def _statement(
    source: str,
    mask: str,
    start: int,
    end: int,
    operations: SemanticOperationRegistry,
) -> SemanticStatement:
    """One body statement as either an understood operation or raw text."""
    text = source[start:end].strip()
    if not text:
        # Unreachable: `_statement_spans` only opens a span on a code
        # character. Guarded anyway because an empty `UnsupportedStatement`
        # is invalid by construction, and a silent crash here would be a
        # worse failure than an explicit one.
        raise SemanticAnalysisError(f"empty statement span at {start}..{end}")

    conditional = _try_conditional(source, mask, start, end, operations)
    if conditional is not None:
        return conditional

    if re.fullmatch(r"return\s*;", mask[start:end].strip()):
        return ReturnStatement(text=text)

    declaration = _try_declaration(source, mask, start, end, operations)
    if declaration is not None:
        return declaration

    call = _match_call(mask, start, end)
    if call is None:
        return UnsupportedStatement(text=text, reason=UnsupportedReason.NOT_A_CALL)
    name_start, name_end, args_start, args_end = call

    name = source[name_start:name_end]
    if name in _CONTROL_KEYWORDS:
        return UnsupportedStatement(text=text, reason=UnsupportedReason.NOT_A_CALL)

    operation_id = _CALL_OPERATIONS.get(name)
    operation = None if operation_id is None else operations.operation(operation_id)
    if operation is None:
        argument_texts = _argument_texts(source, mask, args_start, args_end)
        if not argument_texts:
            # A call to a function this table does not name, given no
            # arguments — `motorStart();`, `chirpBuzzer();`, `pollButtons();`.
            # See `CallStatement`: deliberately zero-arg only, so a call WITH
            # arguments still falls through to UNKNOWN_CALL below, unchanged.
            return CallStatement(function_name=name, text=text)
        return UnsupportedStatement(text=text, reason=UnsupportedReason.UNKNOWN_CALL)

    argument_texts = _argument_texts(source, mask, args_start, args_end)
    if len(argument_texts) != len(operation.parameters):
        return UnsupportedStatement(text=text, reason=UnsupportedReason.ARGUMENT_COUNT)

    arguments: list[SemanticArgument] = []
    for parameter, argument_text in zip(operation.parameters, argument_texts):
        value = _value(argument_text)
        if value is None:
            return UnsupportedStatement(text=text, reason=UnsupportedReason.UNSUPPORTED_ARGUMENT)
        if not value.fits(parameter.value_type):
            return UnsupportedStatement(text=text, reason=UnsupportedReason.ARGUMENT_TYPE)
        arguments.append(SemanticArgument(name=parameter.name, value=value))

    return OperationStatement(
        operation=operation, arguments=tuple(arguments), text=text
    )


# --- `if (CONDITION) { BODY }`, no `else` ------------------------------------


def _try_conditional(
    source: str,
    mask: str,
    start: int,
    end: int,
    operations: SemanticOperationRegistry,
) -> ConditionalStatement | None:
    """This span as a plain `if (COND) { BODY }`, or None if it is not that shape.

    See `ConditionalStatement`'s docstring for why there is no `else` here: a
    span that IS an `if` followed by `else ...` never reaches this function in
    the first place, because `_statement_spans` already splits the `else` (or
    `else if`) off into its own following span — this function only ever sees
    the `if (...) { ... }` head. A condition or body this function cannot
    parse — anything beyond one top-level `==`/`!=` comparison of a literal or
    a symbol — returns None, and the caller's ordinary NOT_A_CALL fallback
    (`if` is a control keyword) carries the whole span verbatim instead.
    """
    if end - start < 2 or mask[start : start + 2] != "if":
        return None
    if start + 2 < end and (mask[start + 2].isalnum() or mask[start + 2] == "_"):
        return None
    open_paren = _next_code(mask, start + 2, end)
    if open_paren is None or mask[open_paren] != "(":
        return None
    close_paren = _matching_paren(mask, open_paren, end)
    if close_paren is None:
        return None
    condition = _expression(source[open_paren + 1 : close_paren], operations)
    if not isinstance(condition, ComparisonValue):
        # Only a comparison is a condition here, as before: `if (ready)` stays
        # carried verbatim rather than silently becoming a block.
        return None
    brace_open = _next_code(mask, close_paren + 1, end)
    if brace_open is None or mask[brace_open] != "{":
        return None
    brace_close = _matching_brace(mask, brace_open, end)
    if brace_close is None:
        return None
    if _next_code(mask, brace_close + 1, end) is not None:
        # Something follows the closing brace within THIS span. Given how
        # `_statement_spans` builds spans (a `;` immediately after a `}` is
        # folded into the same span, everything else is not), this is not a
        # shape a plain `if` can produce — refuse rather than guess at it.
        return None
    body = _body_statements(source, mask, brace_open + 1, brace_close, operations)
    return ConditionalStatement(
        condition=condition, body=body, text=source[start:end].strip()
    )


def _try_declaration(
    source: str,
    mask: str,
    start: int,
    end: int,
    operations: SemanticOperationRegistry,
) -> VariableDeclaration | None:
    """`TYPE NAME = VALUE;` as a local declaration, or None if it is not one.

    Exactly one declarator of one of `_DECLARATION_TYPES`, always initialized,
    no qualifier — see `VariableDeclaration`. Matched on the MASK so a `=`
    inside a string can never be the initializer's `=`; the initializer itself
    is then read from the SOURCE by `_expression`. Anything else — `String
    message;`, `static int n = 0;`, `int a = 1, b = 2;`, an initializer the
    value layer cannot state — is None, and the caller carries it verbatim.
    """
    match = re.fullmatch(
        r"(String|int|bool)\s+([A-Za-z_]\w*)\s*=([^;]*);", mask[start:end].strip()
    )
    if match is None:
        return None
    offset = start + (len(mask[start:end]) - len(mask[start:end].lstrip()))
    value = _expression(source[offset + match.start(3) : offset + match.end(3)], operations)
    if value is None:
        return None
    try:
        return VariableDeclaration(
            value_type=_DECLARATION_TYPES[match.group(1)],
            name=match.group(2),
            initializer=value,
            text=source[start:end].strip(),
        )
    except SemanticModelError:
        # A reserved name, or an initializer that does not fit the declared
        # type (`int n = "x";`): source the IR must not claim to understand.
        return None


# --- expressions: the value layer, read from source -------------------------
#
# A deliberately tiny recursive-descent reader over ONE expression's source
# text — the grammar the value layer can state and nothing more:
#
#     comparison := sum [ ("==" | "!=" | "<=") sum ]
#     sum        := postfix ( "+" postfix )*
#     postfix    := primary ( "." METHOD "(" arguments ")" )*
#     primary    := literal | NAME | String(TEXT) | "(" comparison ")"
#
# It lexes its own text rather than consulting `code_mask`, because the mask
# blanks string literals and comments alike and an expression needs to tell
# them apart: a string is a value, a comment inside an expression is source
# this reader refuses (None) so the whole statement is carried verbatim.
# Any token outside the grammar — `-` between operands, `<`, `&&`, `*`, a
# bare function call, a char literal — is likewise None. There is no error
# path: unrecognized means unsupported, exactly like the call recognizer.

_TOKEN_PATTERN = (
    r"\s+"
    r"|(?P<comment>//|/\*)"
    r'|(?P<string>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<number>(?:\d+\.\d*|\.\d+|\d+))"
    r"|(?P<name>[A-Za-z_]\w*)"
    r"|(?P<punct>==|!=|<=|[-+().,])"
)


def _tokens(text: str) -> list[tuple[str, str]] | None:
    """`text` as `(kind, token)` pairs, or None if it holds anything else."""
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = re.match(_TOKEN_PATTERN, text[position:])
        if match is None or match.end() == 0 or match.lastgroup == "comment":
            return None
        if match.lastgroup is not None:
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
        position += match.end()
    return tokens


def _expression(text: str, operations: SemanticOperationRegistry) -> SemanticValue | None:
    """One expression's source as a semantic value, or None if it is not one."""
    tokens = _tokens(text)
    if not tokens:
        return None
    reader = _ExpressionReader(tokens, operations)
    try:
        value = reader.comparison()
    except (_NotAnExpression, SemanticModelError):
        return None
    return value if reader.done else None


class _NotAnExpression(Exception):
    """Internal: the tokens left the grammar. Never escapes `_expression`."""


class _ExpressionReader:
    def __init__(self, tokens: list[tuple[str, str]], operations: SemanticOperationRegistry):
        self._tokens = tokens
        self._position = 0
        self._operations = operations

    @property
    def done(self) -> bool:
        return self._position == len(self._tokens)

    def _peek(self, offset: int = 0) -> tuple[str, str] | None:
        index = self._position + offset
        return self._tokens[index] if index < len(self._tokens) else None

    def _take(self, token: str) -> bool:
        current = self._peek()
        if current is not None and current[0] == "punct" and current[1] == token:
            self._position += 1
            return True
        return False

    def _expect(self, token: str) -> None:
        if not self._take(token):
            raise _NotAnExpression(token)

    def comparison(self) -> SemanticValue:
        left = self.sum()
        current = self._peek()
        if current is not None and current[0] == "punct" and current[1] in COMPARISON_OPERATORS:
            self._position += 1
            return ComparisonValue(left=left, operator=current[1], right=self.sum())
        return left

    def sum(self) -> SemanticValue:
        value = self.postfix()
        while self._take("+"):
            value = ArithmeticValue(left=value, operator="+", right=self.postfix())
        return value

    def postfix(self) -> SemanticValue:
        value = self.primary()
        while self._take("."):
            kind, method = self._peek() or ("", "")
            operation_id = _METHOD_OPERATIONS.get(method) if kind == "name" else None
            operation = None if operation_id is None else self._operations.operation(operation_id)
            if operation is None or operation.form is not OperationForm.VALUE:
                raise _NotAnExpression(method)
            self._position += 1
            self._expect("(")
            operands = [value, *self._arguments()]
            if len(operands) != len(operation.parameters):
                raise _NotAnExpression(method)
            value = OperationValue(
                operation=operation,
                arguments=tuple(
                    SemanticArgument(name=parameter.name, value=operand)
                    for parameter, operand in zip(operation.parameters, operands)
                ),
            )
        return value

    def _arguments(self) -> list[SemanticValue]:
        if self._take(")"):
            return []
        values = [self.comparison()]
        while self._take(","):
            values.append(self.comparison())
        self._expect(")")
        return values

    def primary(self) -> SemanticValue:
        current = self._peek()
        if current is None:
            raise _NotAnExpression("end")
        kind, token = current
        if self._take("("):
            value = self.comparison()
            self._expect(")")
            return value
        if self._take("-"):
            number = self._peek()
            if number is None or number[0] != "number":
                raise _NotAnExpression("-")
            self._position += 1
            return _number(f"-{number[1]}")
        self._position += 1
        if kind == "number":
            return _number(token)
        if kind == "string":
            return LiteralValue(value=_unescape(token[1:-1]), value_type=SemanticType.TEXT)
        if kind == "name":
            if token == "String" and self._take("("):
                # `String("...")` — how `generator.py` writes a text literal
                # that a method is called on. Read back as the literal it is.
                literal = self._peek()
                if literal is None or literal[0] != "string":
                    raise _NotAnExpression(token)
                self._position += 1
                self._expect(")")
                return LiteralValue(value=_unescape(literal[1][1:-1]), value_type=SemanticType.TEXT)
            following = self._peek()
            if following is not None and following == ("punct", "("):
                raise _NotAnExpression(token)  # a bare function call
            value = _value(token)
            if value is None:
                raise _NotAnExpression(token)
            return value
        raise _NotAnExpression(token)


def _number(text: str) -> LiteralValue:
    if re.fullmatch(_INTEGER_PATTERN, text):
        return LiteralValue(value=int(text), value_type=SemanticType.NUMBER)
    return LiteralValue(value=float(text), value_type=SemanticType.NUMBER)


def _match_call(mask: str, start: int, end: int) -> tuple[int, int, int, int] | None:
    """`(name_start, name_end, args_start, args_end)` for `name(args);`.

    None whenever the span is not EXACTLY one call of a bare identifier,
    terminated by a semicolon and nothing after it. That single strict shape
    is what keeps every other construct — `client.publish(…)`, `x = f();`,
    `f() + g();`, a declaration, an assignment — out of the understood path
    without this module having to recognize any of them.
    """
    terminator = _last_code(mask, start, end)
    if terminator is None or mask[terminator] != ";":
        return None
    open_paren = mask.find("(", start, terminator)
    if open_paren == -1:
        return None
    close_paren = _matching_paren(mask, open_paren, terminator)
    if close_paren is None:
        return None
    if _next_code(mask, close_paren + 1, terminator) is not None:
        return None
    name_end = open_paren
    while name_end > start and mask[name_end - 1].isspace():
        name_end -= 1
    if re.fullmatch(_IDENTIFIER_PATTERN, mask[start:name_end]) is None:
        return None
    return start, name_end, open_paren + 1, close_paren


def _matching_paren(mask: str, start: int, limit: int) -> int | None:
    depth = 0
    for i in range(start, limit):
        char = mask[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _last_code(mask: str, start: int, end: int) -> int | None:
    for i in range(end - 1, start - 1, -1):
        if not mask[i].isspace():
            return i
    return None


def _argument_texts(source: str, mask: str, start: int, end: int) -> list[str]:
    """The source text of each top-level argument, split on the mask.

    An empty argument list yields no entries; a trailing or doubled comma
    yields an empty entry, which `_value` then refuses — a malformed call is
    carried verbatim rather than silently accepted with a missing operand.

    EMPTINESS IS DECIDED ON THE SOURCE, not the mask: `code_mask` blanks a
    string literal to spaces, so on the mask `print("hi")` has the same empty
    argument list as `print()`. Asking the mask alone once turned such a call
    into a zero-argument `CallStatement` that regenerated without its operand.
    """
    if not source[start:end].strip():
        return []
    texts: list[str] = []
    depth = 0
    begin = start
    for i in range(start, end):
        char = mask[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            texts.append(source[begin:i])
            begin = i + 1
    texts.append(source[begin:end])
    return texts


def _value(text: str) -> SemanticValue | None:
    """One argument as a semantic value, or None if it is not one.

    Only the two leaf forms `models.py` declares are produced. Anything with
    structure — arithmetic, a ternary, a cast, a nested call, an address-of —
    returns None, which makes its whole statement unsupported.
    """
    text = text.strip()
    if not text:
        return None
    if text == "true":
        return LiteralValue(value=True, value_type=SemanticType.BOOLEAN)
    if text == "false":
        return LiteralValue(value=False, value_type=SemanticType.BOOLEAN)
    if re.fullmatch(_INTEGER_PATTERN, text):
        return LiteralValue(value=int(text), value_type=SemanticType.NUMBER)
    if re.fullmatch(_DECIMAL_PATTERN, text):
        return LiteralValue(value=float(text), value_type=SemanticType.NUMBER)
    if re.fullmatch(_STRING_PATTERN, text):
        return LiteralValue(value=_unescape(text[1:-1]), value_type=SemanticType.TEXT)
    if re.fullmatch(_IDENTIFIER_PATTERN, text) and text not in _CONTROL_KEYWORDS:
        return SymbolValue(name=text)
    return None


def _unescape(text: str) -> str:
    return re.sub(r"\\.", lambda match: _ESCAPES.get(match.group(0), match.group(0)), text)
