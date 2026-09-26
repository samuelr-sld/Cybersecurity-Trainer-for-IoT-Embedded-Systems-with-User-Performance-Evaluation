"""Passive data model for the semantic IR (Phase B3).

    SemanticProgram
      -> SemanticSection      (one per CodeSection, by id)
           -> SemanticStatement
                -> OperationStatement    (operation + named arguments)
                -> CallStatement         (call an existing, zero-arg function)
                -> ConditionalStatement  (if (CONDITION) { body: SemanticStatement[] })
                -> VariableDeclaration   (TYPE NAME = VALUE, a local)
                -> ReturnStatement       (return; — void)
                -> UnsupportedStatement  (exact source text + why)
                     -> SemanticValue
                          -> LiteralValue    (typed scalar)
                          -> SymbolValue     (named constant/parameter/local reference)
                          -> ComparisonValue (LEFT ==/!=/<= RIGHT, always boolean)
                          -> ArithmeticValue (LEFT + RIGHT, numeric)
                          -> OperationValue  (a VALUE operation + named operands)

THE QUESTION THIS LAYER ANSWERS. `app/build/discovery/models.py` already
states the split it keeps from `FileSegment`; this module adds the third:

    CodeSection   "Where is this code and what structural kind is it?"
    FileSegment   "How does this source participate in the Build workspace?"
    SemanticIR    "What does this supported code MEAN?"

None of them learn each other's fields. Nothing here carries an offset, a
`SectionKind`, a lock/editable flag, a region id, a path, a panel, a session,
or a Blockly type. A `SemanticSection` references its originating
`CodeSection` by ID ONLY — a plain string, never the object — so the IR can
be produced, stored, compared and consumed without dragging the discovery
model along behind it.

SHAPE ONLY, LIKE ITS NEIGHBOURS. Exactly as with `app/build/models.py` and
`app/build/discovery/models.py`, nothing here parses. `analyzer.py` is the
one thing that produces a `SemanticProgram`, and it always hands back a
fully-formed, already-validated one.

SOURCE IS PRESERVED, NOT REPLACED. The IR is an ADDITIONAL representation.
The `CodeSection` it was derived from still holds the exact original text and
is never modified; B2's `FileSegment`s — the text a compiler actually sees —
are untouched by this layer's existence. An unsupported construct keeps its
own exact `source_text`, so it is carried verbatim rather than dropped or
approximated. An UNDERSTOOD statement keeps that text too, but only as
provenance and only when it has any: a statement authored in an editor has no
source anywhere, and since B6 its C++ is written from its meaning rather than
from a remembered string (see `OperationStatement`).

UNSUPPORTED IS A REPRESENTATION, NOT A FAILURE. Most real firmware is not
expressible in five operations, and pretending otherwise is the failure mode
worth designing against. An `OperationStatement` is therefore FULLY
representable by construction — every argument matched a declared parameter
and is a value the IR can state. Anything less becomes an
`UnsupportedStatement` carrying its exact text and a reason. There is no
partially-understood statement, so a later consumer (B4's Blockly adapter)
never has to decide what to do with half a meaning.

NO BLOCKLY, NO FILESYSTEM, NO SESSION. This module imports one thing:
`operations.py`, next to it. See that module's docstring for why the block
catalog's vocabulary is reused without importing `app.blockly`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.build.semantic.errors import SemanticModelError
from app.build.semantic.operations import (
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    OperationForm,
    SemanticOperation,
    SemanticType,
)


# --- values (the leaf expression layer) -------------------------------------


class SemanticValue:
    """Base for anything that can stand in an operation's argument slot.

    Two leaf forms exist today, which is what the supported operations need:
    a literal scalar and a reference to a named constant. There is no
    computed-expression form (`a + b`, a ternary, a function call) — a
    statement containing one is not representable at all and becomes an
    `UnsupportedStatement`, rather than being half-captured here.

    `fits` is the one piece of behaviour: whether this value may occupy a
    parameter of a given type. It lives on the value rather than in a
    validation helper so each form states its own rule once.
    """

    __slots__ = ()

    def fits(self, value_type: SemanticType) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def source_text(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(frozen=True)
class LiteralValue(SemanticValue):
    """A scalar written out in the source: `27`, `1000`, `true`, `"text"`.

    `value_type` is carried explicitly rather than inferred on demand so the
    model says what it means; `__post_init__` checks the Python value agrees
    with it, so the two can never disagree.
    """

    value: int | float | str | bool
    value_type: SemanticType

    def __post_init__(self) -> None:
        if not isinstance(self.value_type, SemanticType):
            raise SemanticModelError(f"invalid literal type: {self.value_type!r}")
        # bool before int: `isinstance(True, int)` is True in Python, so the
        # boolean check has to come first or `true` would validate as a number.
        if self.value_type is SemanticType.BOOLEAN:
            if not isinstance(self.value, bool):
                raise SemanticModelError(f"boolean literal expected, got {self.value!r}")
        elif self.value_type in (SemanticType.NUMBER, SemanticType.PIN):
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise SemanticModelError(f"numeric literal expected, got {self.value!r}")
        else:
            if not isinstance(self.value, str):
                raise SemanticModelError(f"text literal expected, got {self.value!r}")

    def fits(self, value_type: SemanticType) -> bool:
        """Literals must match their slot, with one deliberate widening.

        A `PIN` parameter accepts a `NUMBER` literal: `pinMode(2, OUTPUT)`
        writes a plain integer where a pin is meant, and that is ordinary
        Arduino source, not a type error. The widening is one-way — a NUMBER
        parameter does not accept a PIN literal — so the `PIN` type stays the
        identifiable thing the block catalog's README wants it to be.
        """
        if not isinstance(value_type, SemanticType):
            return False
        if self.value_type is value_type:
            return True
        return value_type is SemanticType.PIN and self.value_type is SemanticType.NUMBER

    @property
    def source_text(self) -> str:
        """How this literal reads. Diagnostics only — never a C++ fragment."""
        if self.value_type is SemanticType.BOOLEAN:
            return "true" if self.value else "false"
        if self.value_type is SemanticType.TEXT:
            return f'"{self.value}"'
        return str(self.value)


#: The comparison operators `ComparisonValue` may express. Still a closed set
#: rather than every C++ relational operator: `==`/`!=` are what an
#: authorization-style check needs (`message == expected`, `token != TOKEN`),
#: and `<=` is the one ordering test Panel 1's token parser needs — rejecting
#: a payload whose separator is missing or leading (`separator <= 0`). `<`,
#: `>` and `>=` stay absent until a construct needs one.
COMPARISON_OPERATORS = ("==", "!=", "<=")
_COMPARISON_OPERATORS = COMPARISON_OPERATORS

#: The arithmetic operators `ArithmeticValue` may express: `+`, for the one
#: offset the token parser computes (`separator + 1`). Nothing wider.
ARITHMETIC_OPERATORS = ("+",)


@dataclass(frozen=True)
class ComparisonValue(SemanticValue):
    """`LEFT == RIGHT` or `LEFT != RIGHT` — the one expression form the IR has.

    Added for `ConditionalStatement`'s condition (see below): an authorization
    check is a comparison, and representing `message == "START"` needs an
    expression node the two leaf forms above cannot be. It is still narrow on
    purpose — no `&&`/`||`, no arithmetic, no nesting beyond one comparison —
    because that is exactly the vocabulary the remediation this phase targets
    needs, and a wider expression grammar is a later phase's addition, not a
    silent side effect of this one.

    `left`/`right` are themselves `SemanticValue`s (a `LiteralValue` or a
    `SymbolValue` today — nothing stops a future `ComparisonValue` from
    nesting, but nothing produces one), so this class adds no new leaf shape,
    only a way to combine the existing ones into something that FITS a
    boolean slot.
    """

    left: SemanticValue
    operator: str
    right: SemanticValue

    def __post_init__(self) -> None:
        if not isinstance(self.left, SemanticValue):
            raise SemanticModelError(f"comparison left operand is not a value: {self.left!r}")
        if not isinstance(self.right, SemanticValue):
            raise SemanticModelError(f"comparison right operand is not a value: {self.right!r}")
        if self.operator not in _COMPARISON_OPERATORS:
            raise SemanticModelError(f"invalid comparison operator: {self.operator!r}")

    def fits(self, value_type: SemanticType) -> bool:
        """A comparison is always boolean — it is the IR's only source of one."""
        return value_type is SemanticType.BOOLEAN

    @property
    def source_text(self) -> str:
        return f"{self.left.source_text} {self.operator} {self.right.source_text}"


@dataclass(frozen=True)
class SymbolValue(SemanticValue):
    """A reference to a named constant: `MOTOR_IN1`, `OUTPUT`, `HIGH`.

    LOAD-BEARING, NOT AN AFTERTHOUGHT. Real firmware names its pins and its
    modes; the committed Panel 1 sketch writes `pinMode(START_BUTTON,
    INPUT_PULLUP)` and never a bare pin number. A literal-only value model
    could not represent a single real statement in that file.

    A symbol FITS EVERY PARAMETER TYPE, and that is honest rather than lax:
    the IR does not resolve names. `BUZZER_CHIRP_MS` is a number, `OUTPUT` is
    a mode, `HIGH` is a level — knowing which requires reading the
    declarations, which this phase deliberately does not do. Claiming a type
    it has not established would be the dishonest option. Resolution is a
    later phase's job and needs no change to this class.
    """

    name: str

    def __post_init__(self) -> None:
        # An identifier, not merely non-empty text: B6 writes the name into
        # firmware verbatim, so a "name" holding `x); system(` would be source
        # smuggled through an editor field. Every producer already yields an
        # identifier; this makes it impossible to construct anything else.
        if not isinstance(self.name, str) or not re.match(r"^[A-Za-z_]\w*$", self.name):
            raise SemanticModelError(f"symbol name must be an identifier, got {self.name!r}")

    def fits(self, value_type: SemanticType) -> bool:
        return isinstance(value_type, SemanticType)

    @property
    def source_text(self) -> str:
        return self.name


def _numeric_slot(value_type: SemanticType) -> bool:
    """NUMBER, or PIN under `LiteralValue.fits`' one-way widening."""
    return value_type in (SemanticType.NUMBER, SemanticType.PIN)


@dataclass(frozen=True)
class ArithmeticValue(SemanticValue):
    """`LEFT + RIGHT` — numeric, and only `+` (see `ARITHMETIC_OPERATORS`).

    The shape of `ComparisonValue`, for the same reason: an operator over two
    existing values is structure, not a platform operation, so it needs no row
    in the operation registry. Always numeric, so it fits a NUMBER slot (and a
    PIN slot, by the same widening a numeric literal gets).
    """

    left: SemanticValue
    operator: str
    right: SemanticValue

    def __post_init__(self) -> None:
        if not isinstance(self.left, SemanticValue):
            raise SemanticModelError(f"arithmetic left operand is not a value: {self.left!r}")
        if not isinstance(self.right, SemanticValue):
            raise SemanticModelError(f"arithmetic right operand is not a value: {self.right!r}")
        if self.operator not in ARITHMETIC_OPERATORS:
            raise SemanticModelError(f"invalid arithmetic operator: {self.operator!r}")
        for side, operand in (("left", self.left), ("right", self.right)):
            if not operand.fits(SemanticType.NUMBER):
                raise SemanticModelError(
                    f"arithmetic {side} operand ({operand.source_text}) is not numeric"
                )

    def fits(self, value_type: SemanticType) -> bool:
        return _numeric_slot(value_type)

    @property
    def source_text(self) -> str:
        return f"{self.left.source_text} {self.operator} {self.right.source_text}"


@dataclass(frozen=True)
class OperationValue(SemanticValue):
    """A VALUE-form operation applied to its operands: `text.index_of(...)`.

    The value-position counterpart of `OperationStatement`, validated the same
    way and for the same reason — COMPLETE BY CONSTRUCTION: operands cover
    every declared parameter exactly once, in declared order, and each fits
    its parameter. Its own type is the operation's declared `result`, so
    `fits` needs no guess. Carries no C++ at all; how `text.index_of` is
    spelled is `emissions.py`'s business.
    """

    operation: SemanticOperation
    arguments: tuple["SemanticArgument", ...]

    def __post_init__(self) -> None:
        if not isinstance(self.operation, SemanticOperation):
            raise SemanticModelError(f"not a SemanticOperation: {self.operation!r}")
        if self.operation.form is not OperationForm.VALUE:
            raise SemanticModelError(
                f"{self.operation.operation_id}: a {self.operation.form.value} operation "
                "does not yield a value"
            )
        _check_arguments(self.operation, self.arguments)

    @property
    def operation_id(self) -> str:
        return self.operation.operation_id

    @property
    def result(self) -> SemanticType:
        assert self.operation.result is not None  # guaranteed for a VALUE form
        return self.operation.result

    def value(self, name: str) -> SemanticValue | None:
        """The operand bound to this parameter name, or None."""
        for argument in self.arguments:
            if argument.name == name:
                return argument.value
        return None

    def fits(self, value_type: SemanticType) -> bool:
        if not isinstance(value_type, SemanticType):
            return False
        if self.result is value_type:
            return True
        return value_type is SemanticType.PIN and self.result is SemanticType.NUMBER

    @property
    def source_text(self) -> str:
        """Diagnostics only — `text.index_of(message, " ")`, never C++."""
        operands = ", ".join(argument.value.source_text for argument in self.arguments)
        return f"{self.operation_id}({operands})"


def _check_arguments(operation: SemanticOperation, arguments: tuple) -> None:
    """The completeness rule `OperationStatement` and `OperationValue` share."""
    for argument in arguments:
        if not isinstance(argument, SemanticArgument):
            raise SemanticModelError(f"{operation.operation_id}: not an argument: {argument!r}")
    supplied = tuple(argument.name for argument in arguments)
    expected = operation.parameter_names
    if supplied != expected:
        raise SemanticModelError(
            f"{operation.operation_id}: arguments {list(supplied)} do not match "
            f"parameters {list(expected)}"
        )
    for argument in arguments:
        parameter = operation.parameter(argument.name)
        assert parameter is not None  # guaranteed by the name check above
        if not argument.value.fits(parameter.value_type):
            raise SemanticModelError(
                f"{operation.operation_id}: argument {argument.name} "
                f"({argument.value.source_text}) does not fit a "
                f"{parameter.value_type.value} parameter"
            )


# --- statements -------------------------------------------------------------


class UnsupportedReason(str, Enum):
    """Why a span of source has no semantic representation.

    A closed vocabulary rather than free text so a consumer (and a test) can
    distinguish the cases without string matching. None of them is an error:
    each one names source the IR is carrying verbatim instead of interpreting.

    UNSUPPORTED_SECTION   the whole section has no container operation — a
                          helper function, a callback, global declarations.
    NOT_A_CALL            not a plain `name(arguments);` call: a control-flow
                          block, a declaration, an assignment, a method call
                          on an object (`client.publish(...)`).
    UNKNOWN_CALL          a plain call whose name maps to no operation.
    ARGUMENT_COUNT        a known call given the wrong number of arguments.
    UNSUPPORTED_ARGUMENT  an argument is neither a literal nor a symbol (an
                          arithmetic expression, a ternary, a nested call).
    ARGUMENT_TYPE         a literal whose type cannot occupy its parameter.
    """

    UNSUPPORTED_SECTION = "unsupported_section"
    NOT_A_CALL = "not_a_call"
    UNKNOWN_CALL = "unknown_call"
    ARGUMENT_COUNT = "argument_count"
    UNSUPPORTED_ARGUMENT = "unsupported_argument"
    ARGUMENT_TYPE = "argument_type"


class SemanticStatement:
    """Base for one ordered step of a section's body.

    A marker base with no fields of its own: the two forms share the idea of
    a position in a body and a preserved `source_text`, but nothing else, and
    giving the base a dataclass field would force both subclasses into its
    field order for no gain.
    """

    __slots__ = ()

    @property
    def source_text(self) -> str | None:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def supported(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(frozen=True)
class SemanticArgument:
    """One operand of an `OperationStatement`, bound to a parameter by name."""

    name: str
    value: SemanticValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SemanticModelError(f"argument name must be a non-empty string: {self.name!r}")
        if not isinstance(self.value, SemanticValue):
            raise SemanticModelError(f"argument {self.name}: not a semantic value")


@dataclass(frozen=True)
class OperationStatement(SemanticStatement):
    """One fully-understood action: an operation and all of its arguments.

    Carries the `SemanticOperation` itself, not just its id, so the statement
    validates completely at construction against the definition it names
    rather than against whatever registry a caller happens to consult later.
    The stable id is always one attribute away (`statement.operation_id`).

    COMPLETE BY CONSTRUCTION. Arguments must cover every parameter, exactly
    once, in the operation's own declared order, and each value must fit its
    parameter's type. An `OperationStatement` that exists is therefore one a
    consumer can act on without re-checking anything.

    `text` IS PROVENANCE, NOT MEANING, and it is optional for that reason. It
    is the exact source this statement was READ from, when it was read from
    source at all — the same field, with the same meaning, that B4's
    `BlocklyBlock.source_text` already carries and leaves None for a block
    authored in the editor. A statement's meaning is entirely its operation and
    its arguments: nothing decides what it does by reading `text`, and B6's
    generator writes its C++ from the operation and the values rather than from
    this field, so a source-less statement generates exactly like a source-
    backed one. Before B6 existed, a statement with no text could not have been
    turned back into firmware by anything, which is why it was required then
    and is not now.
    """

    operation: SemanticOperation
    arguments: tuple[SemanticArgument, ...]
    text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, SemanticOperation):
            raise SemanticModelError(f"not a SemanticOperation: {self.operation!r}")
        if self.operation.form is not OperationForm.STATEMENT:
            raise SemanticModelError(
                f"{self.operation.operation_id}: a {self.operation.form.value} operation "
                "cannot be a statement in a body"
            )
        if self.text is not None and (not isinstance(self.text, str) or not self.text.strip()):
            raise SemanticModelError(
                f"{self.operation.operation_id}: statement text must be non-empty source "
                "or absent"
            )
        _check_arguments(self.operation, self.arguments)

    @property
    def operation_id(self) -> str:
        """The stable operation identity this statement expresses."""
        return self.operation.operation_id

    @property
    def source_text(self) -> str | None:
        """The exact source this statement was read from, or None if authored."""
        return self.text

    @property
    def has_source(self) -> bool:
        """True when this statement was read from source rather than authored."""
        return self.text is not None

    @property
    def supported(self) -> bool:
        return True

    def argument(self, name: str) -> SemanticArgument | None:
        """The argument bound to this parameter name, or None."""
        for argument in self.arguments:
            if argument.name == name:
                return argument
        return None

    def value(self, name: str) -> SemanticValue | None:
        """The value bound to this parameter name, or None."""
        argument = self.argument(name)
        return None if argument is None else argument.value


@dataclass(frozen=True)
class UnsupportedStatement(SemanticStatement):
    """Source the IR carries verbatim instead of interpreting.

    This is the class that lets B3 be a small, honest layer rather than a C++
    frontend: anything outside the supported subset lands here with its exact
    text intact and a `reason` saying why, so no construct is silently lost
    and no construct forces the operation table to grow.
    """

    text: str
    reason: UnsupportedReason

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise SemanticModelError("unsupported statement text must be non-empty")
        if not isinstance(self.reason, UnsupportedReason):
            raise SemanticModelError(f"invalid unsupported reason: {self.reason!r}")

    @property
    def source_text(self) -> str:
        return self.text

    @property
    def supported(self) -> bool:
        return False


@dataclass(frozen=True)
class CallStatement(SemanticStatement):
    """A call to an existing, arbitrarily-named function, for its effect.

    NOT AN `OperationStatement`. An `OperationStatement` names one of the
    closed, platform-defined `SemanticOperation`s (`gpio.pin_mode`, ...) whose
    C++ spelling is a fixed row in `emissions.py`; this is the opposite case —
    a call to whatever function the *firmware itself* already defines
    (`motorStart()`, `chirpBuzzer()`), which cannot be a global operation
    without teaching the panel-agnostic registry a panel's own function names.
    `function_name` is therefore data on the statement, not an operation id.

    DELIBERATELY ZERO-ARGUMENT ONLY, for now. Real firmware calls existing
    helpers with no arguments constantly (`motorStart(); chirpBuzzer();
    ensureConnected(); pollButtons();`), and every one of those becomes
    representable with nothing more than a function name. A call WITH
    arguments is not modelled here — it stays an `UnsupportedStatement`
    exactly as it always has — because representing an arbitrary argument
    list safely (typed, order-preserving, round-tripping through one Blockly
    field) is a real extension this phase does not need to make. Widening this
    is adding an `arguments` field here plus a matching Blockly value input,
    not a change to what this class already means.
    """

    function_name: str
    text: str | None = None

    def __post_init__(self) -> None:
        # The C++ identifier shape a bare function name must have. The same
        # pattern `app/build/semantic/emissions.py` requires of a
        # `CallEmission`, restated rather than imported for the same
        # layering reason `operations.py` gives for not importing Blockly:
        # this module must not depend on the C++-aware emissions table to
        # validate its own shape. `re.match` inline, not `re.compile` —
        # `compile` is one of the bare names the build layer's own static
        # scan refuses everywhere in this package (see
        # `tests/test_build_workspace.py`), because it is also Python's
        # dynamic-code builtin; this module has no dynamic execution to
        # justify tripping that check.
        if not isinstance(self.function_name, str) or not re.match(
            r"^[A-Za-z_]\w*$", self.function_name
        ):
            raise SemanticModelError(f"invalid call target name: {self.function_name!r}")
        if self.text is not None and (not isinstance(self.text, str) or not self.text.strip()):
            raise SemanticModelError("call statement text must be non-empty source or absent")

    @property
    def source_text(self) -> str | None:
        return self.text

    @property
    def supported(self) -> bool:
        return True


@dataclass(frozen=True)
class ConditionalStatement(SemanticStatement):
    """`if (CONDITION) { BODY }` — no `else`.

    THE ONE CONTROL-FLOW FORM THE IR HAS, and deliberately the smallest one
    that can express an authorization gate: a condition and a body to run when
    it holds. There is no `else`/`else if` here on purpose. C++ does not
    require one: `if (a) { x(); }` immediately followed by `if (b) { y(); }`
    behaves identically to `if (a) { x(); } else if (b) { y(); }` whenever the
    conditions are mutually exclusive — which a string-equality authorization
    check always is — and two independent `if`s is also the more natural
    Blockly shape (drag two blocks, not one block with a growing mutator).
    Firmware read from source that DOES use `else`/`else if` is unaffected:
    the `if` head B3 recognizes becomes one `ConditionalStatement` and the
    `else ...` that follows becomes its own `UnsupportedStatement`, carried
    verbatim immediately after it — C++ does not require anything but
    whitespace between a `}` and the `else` that follows it, so the two
    statements placed back to back by `generator.py` still compile and still
    behave exactly as the original did. What changes is only that the `else`
    branch is not independently editable as blocks unless it is rewritten as
    its own `if`.

    `condition` must `fits(SemanticType.BOOLEAN)` — a `ComparisonValue`
    always does, a boolean `LiteralValue` does, and a bare `SymbolValue` does
    too (symbols fit every type; the IR does not resolve names — see
    `SymbolValue`). `body` is this construct's own nested statement list, may
    be empty (an emptied Blockly body is a legitimate edit, exactly as an
    emptied container body is), and may itself contain further supported or
    unsupported statements, including a nested `ConditionalStatement`.
    """

    condition: SemanticValue
    body: tuple[SemanticStatement, ...]
    text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.condition, SemanticValue):
            raise SemanticModelError(f"condition is not a value: {self.condition!r}")
        if not self.condition.fits(SemanticType.BOOLEAN):
            raise SemanticModelError(
                f"condition ({self.condition.source_text}) does not fit a boolean"
            )
        for statement in self.body:
            if not isinstance(statement, SemanticStatement):
                raise SemanticModelError(f"not a statement in an if-body: {statement!r}")
        if self.text is not None and (not isinstance(self.text, str) or not self.text.strip()):
            raise SemanticModelError(
                "conditional statement text must be non-empty source or absent"
            )

    @property
    def source_text(self) -> str | None:
        return self.text

    @property
    def supported(self) -> bool:
        return True


#: Words a local variable may not be named. C++'s keywords that can plausibly
#: be typed into a name field, plus the three type names a declaration is
#: written with — a variable called `String` or `int` is source nobody means.
#: Identifier shape is checked separately; this is only the reserved set.
_RESERVED_NAMES = frozenset(
    {
        "auto", "bool", "break", "case", "char", "class", "const", "continue",
        "default", "delete", "do", "double", "else", "enum", "false", "float",
        "for", "goto", "if", "int", "long", "new", "nullptr", "return", "short",
        "signed", "sizeof", "static", "String", "struct", "switch", "this",
        "true", "typedef", "union", "unsigned", "void", "volatile", "while",
    }
)

#: The types a local may be declared with — the three the value layer can
#: already state. Their C++ spellings live in `emissions.py`, not here.
DECLARABLE_TYPES = (SemanticType.TEXT, SemanticType.NUMBER, SemanticType.BOOLEAN)


def is_variable_name(name: object) -> bool:
    """True for a name a local variable may be declared under."""
    return (
        isinstance(name, str)
        and re.match(r"^[A-Za-z_]\w*$", name) is not None
        and name not in _RESERVED_NAMES
    )


@dataclass(frozen=True)
class VariableDeclaration(SemanticStatement):
    """Declare a local variable and initialize it: `String token = ...;`.

    LOCAL, TYPED, ALWAYS INITIALIZED — the minimum a function body needs to
    name an intermediate value (Panel 1's parser names its token, the
    separator position, and the two slices either side of it). A declaration
    with no initializer, a global, an array, a `const`/`static` qualifier and
    re-assignment are all absent: nothing in scope needs them, and each is a
    real extension rather than a free one.

    A later REFERENCE to the variable is an ordinary `SymbolValue` carrying
    its name — the IR's existing named-reference leaf, which already stands
    for parameters and constants and deliberately resolves nothing. There is
    no second "variable reference" class to keep in step with it.

    `value_type` is the variable's declared type (`DECLARABLE_TYPES`), and the
    initializer must fit it — so `int separator = "x";` cannot be stated.
    """

    value_type: SemanticType
    name: str
    initializer: SemanticValue
    text: str | None = None

    def __post_init__(self) -> None:
        if self.value_type not in DECLARABLE_TYPES:
            raise SemanticModelError(f"a local cannot be declared as {self.value_type!r}")
        if not is_variable_name(self.name):
            raise SemanticModelError(f"invalid local variable name: {self.name!r}")
        if not isinstance(self.initializer, SemanticValue):
            raise SemanticModelError(f"{self.name}: initializer is not a value")
        if not self.initializer.fits(self.value_type):
            raise SemanticModelError(
                f"{self.name}: initializer ({self.initializer.source_text}) does not fit a "
                f"{self.value_type.value} variable"
            )
        if self.text is not None and (not isinstance(self.text, str) or not self.text.strip()):
            raise SemanticModelError("declaration text must be non-empty source or absent")

    @property
    def source_text(self) -> str | None:
        return self.text

    @property
    def supported(self) -> bool:
        return True


@dataclass(frozen=True)
class ReturnStatement(SemanticStatement):
    """`return;` — leave the current function now, returning nothing.

    What turns a condition into a REJECTION: `if (token != TOKEN) { return; }`
    stops a malformed or unauthorized command before any branch below can act
    on it. Void only — a value-returning `return` is a different construct
    (the catalog's still-CATALOGED `functions.return`), and every function
    whose body Build Mode edits today returns `void`.
    """

    text: str | None = None

    def __post_init__(self) -> None:
        if self.text is not None and (not isinstance(self.text, str) or not self.text.strip()):
            raise SemanticModelError("return statement text must be non-empty source or absent")

    @property
    def source_text(self) -> str | None:
        return self.text

    @property
    def supported(self) -> bool:
        return True


# --- sections and programs --------------------------------------------------


@dataclass(frozen=True)
class SemanticSection:
    """The meaning of one discovered section, referenced by its id.

    `section_id` is the ONLY link back to `app/build/discovery` — the same
    string a `CodeSection` already carries (`setup`, `loop`,
    `callback_onMessage`, `helper_applyCommand`, `global_3`) and, since B2,
    the same string a `FileSegment`'s `region_id` uses. Reusing it rather than
    minting a parallel identifier means all three layers name one construct
    the same way, with no translation table to drift.

    `operation` is the section's container operation, or None when the
    section has no representable container form. A section with no operation
    holds only `UnsupportedStatement`s — enforced below, because inspecting
    the body of a construct we do not understand would be claiming knowledge
    the analyzer never established.

    `signature` is the PRESERVED, VERBATIM C++ declarator of a generic named
    function container (`functions.implementation` — see `operations.py`),
    e.g. `"static void applyCommand(const String &message)"`. It exists
    because that operation is deliberately generic — one operation id shared
    by every helper function and callback a firmware defines, since the
    semantic layer must not learn a panel's own function names (see
    `operations.py`'s module docstring) — so the one thing that makes a
    container's C++ a *particular* function (its name, return type, qualifiers
    and parameter list) cannot live on the shared operation and has to live
    here, on the one section it belongs to. It is None for `program.setup`/
    `program.loop`, whose signature is a fixed row in `emissions.py` instead,
    and for any section with no container operation. Blockly never sees or
    edits it: `app/build/section_blockly.py::program_with_section` copies the
    current section's `signature` onto whatever `generator.py` writes for the
    section, exactly as it already refuses to let the container operation
    itself change — the signature is fixed scaffolding, not a block.
    """

    section_id: str
    operation: SemanticOperation | None
    statements: tuple[SemanticStatement, ...]
    signature: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.section_id, str) or not self.section_id.strip():
            raise SemanticModelError(f"section id must be a non-empty string: {self.section_id!r}")
        if self.operation is not None:
            if not isinstance(self.operation, SemanticOperation):
                raise SemanticModelError(f"{self.section_id}: not a SemanticOperation")
            if self.operation.form is not OperationForm.CONTAINER:
                raise SemanticModelError(
                    f"{self.section_id}: a section operation must be a container, got "
                    f"{self.operation.form.value}"
                )
        if self.signature is not None:
            if self.operation is None:
                raise SemanticModelError(
                    f"{self.section_id}: a signature needs a container operation"
                )
            if not isinstance(self.signature, str) or not self.signature.strip():
                raise SemanticModelError(
                    f"{self.section_id}: signature must be non-empty text or absent"
                )
        for statement in self.statements:
            if not isinstance(statement, SemanticStatement):
                raise SemanticModelError(f"{self.section_id}: not a statement: {statement!r}")
        if self.operation is None and any(statement.supported for statement in self.statements):
            raise SemanticModelError(
                f"{self.section_id}: a section with no container operation cannot hold "
                "understood statements"
            )

    @property
    def operation_id(self) -> str | None:
        """The container operation's stable id, or None."""
        return None if self.operation is None else self.operation.operation_id

    @property
    def supported(self) -> bool:
        """True when this section maps to a container operation."""
        return self.operation is not None

    @property
    def operation_statements(self) -> tuple[OperationStatement, ...]:
        """The understood statements of this section's body, in order."""
        return tuple(
            statement for statement in self.statements if isinstance(statement, OperationStatement)
        )

    @property
    def unsupported_statements(self) -> tuple[UnsupportedStatement, ...]:
        """The carried-verbatim statements of this section's body, in order."""
        return tuple(
            statement
            for statement in self.statements
            if isinstance(statement, UnsupportedStatement)
        )


@dataclass(frozen=True)
class SemanticProgram:
    """One source file's meaning: its sections, in the order they appear.

    Validated at construction like every other model in this codebase:
    section ids are unique, and their order is the document's order. Nothing
    is indexed by position, so a section is always addressed by the id it
    shares with its `CodeSection`.
    """

    sections: tuple[SemanticSection, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for section in self.sections:
            if not isinstance(section, SemanticSection):
                raise SemanticModelError(f"not a SemanticSection: {section!r}")
            if section.section_id in seen:
                raise SemanticModelError(f"duplicate section id: {section.section_id!r}")
            seen.add(section.section_id)

    def section(self, section_id: str) -> SemanticSection | None:
        """The section with this id, or None if this program has none."""
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    def _with_operation(self, operation_id: str) -> SemanticSection | None:
        for section in self.sections:
            if section.operation_id == operation_id:
                return section
        return None

    @property
    def setup(self) -> SemanticSection | None:
        """The `program.setup` section, or None if this source declares none."""
        return self._with_operation(PROGRAM_SETUP)

    @property
    def loop(self) -> SemanticSection | None:
        """The `program.loop` section, or None if this source declares none."""
        return self._with_operation(PROGRAM_LOOP)

    @property
    def supported_sections(self) -> tuple[SemanticSection, ...]:
        """Sections that map to a container operation, in document order."""
        return tuple(section for section in self.sections if section.supported)

    @property
    def operations_used(self) -> tuple[str, ...]:
        """Every operation id this program expresses, sorted — deterministic.

        Container operations included, so the result describes the whole
        program rather than only its statement bodies.
        """
        used: set[str] = set()
        for section in self.sections:
            if section.operation_id is not None:
                used.add(section.operation_id)
            for statement in section.statements:
                used.update(_statement_operations(statement))
        return tuple(sorted(used))


def _statement_operations(statement: SemanticStatement) -> set[str]:
    """Every operation id a statement expresses, nested bodies and values included."""
    if isinstance(statement, OperationStatement):
        found = {statement.operation_id}
        for argument in statement.arguments:
            found |= _value_operations(argument.value)
        return found
    if isinstance(statement, ConditionalStatement):
        found = _value_operations(statement.condition)
        for item in statement.body:
            found |= _statement_operations(item)
        return found
    if isinstance(statement, VariableDeclaration):
        return _value_operations(statement.initializer)
    return set()


def _value_operations(value: SemanticValue) -> set[str]:
    if isinstance(value, OperationValue):
        found = {value.operation_id}
        for argument in value.arguments:
            found |= _value_operations(argument.value)
        return found
    if isinstance(value, (ComparisonValue, ArithmeticValue)):
        return _value_operations(value.left) | _value_operations(value.right)
    return set()
