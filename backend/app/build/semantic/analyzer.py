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
  * anything else in the body — a control-flow block, a declaration, an
    assignment, a method call on an object (`client.publish(...)`), a call
    with an expression argument (`digitalWrite(PIN, run ? HIGH : LOW)`) —
    keeps its exact source text and a reason.

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
from app.build.semantic.errors import SemanticAnalysisError
from app.build.semantic.models import (
    LiteralValue,
    OperationStatement,
    SemanticArgument,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticValue,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
)
from app.build.semantic.operations import (
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TIME_DELAY,
    SemanticOperationRegistry,
    SemanticType,
    default_semantic_operations,
)

#: Which discovered section kinds have a container operation. Every other
#: kind — GLOBAL_DECLARATIONS, HELPER_FUNCTION, CALLBACK — is represented
#: whole and uninterpreted. This is not a judgement about those constructs;
#: it is the absence of one, exactly as B2's "every section is LOCKED until
#: someone decides otherwise" was.
_SECTION_OPERATIONS = {
    SectionKind.SETUP: PROGRAM_SETUP,
    SectionKind.LOOP: PROGRAM_LOOP,
}

#: The C++ call name each supported operation is written as. The ONLY
#: C++-aware table in this package, and the reason `operations.py` can stay
#: language-neutral. A name absent from here is not an error — its call is
#: carried verbatim as UNKNOWN_CALL.
_CALL_OPERATIONS = {
    "pinMode": GPIO_PIN_MODE,
    "digitalWrite": GPIO_DIGITAL_WRITE,
    "delay": TIME_DELAY,
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
    statements = tuple(
        _statement(source, mask, start, end, operations)
        for start, end in _statement_spans(mask, body_start, body_end)
    )
    return SemanticSection(
        section_id=section.section_id, operation=operation, statements=statements
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
    """
    if _next_code(mask, start, end) is None:
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
