"""Passive data model for the semantic IR (Phase B3).

    SemanticProgram
      -> SemanticSection      (one per CodeSection, by id)
           -> SemanticStatement
                -> OperationStatement   (operation + named arguments)
                -> UnsupportedStatement (exact source text + why)
                     -> SemanticValue
                          -> LiteralValue (typed scalar)
                          -> SymbolValue  (named constant reference)

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
are untouched by this layer's existence. Every statement here additionally
keeps its own `source_text`, so an unsupported construct is carried verbatim
rather than dropped or approximated.

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
        if not isinstance(self.name, str) or not self.name.strip():
            raise SemanticModelError(f"symbol name must be a non-empty string, got {self.name!r}")

    def fits(self, value_type: SemanticType) -> bool:
        return isinstance(value_type, SemanticType)

    @property
    def source_text(self) -> str:
        return self.name


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
    def source_text(self) -> str:  # pragma: no cover - abstract
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
    """

    operation: SemanticOperation
    arguments: tuple[SemanticArgument, ...]
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation, SemanticOperation):
            raise SemanticModelError(f"not a SemanticOperation: {self.operation!r}")
        if self.operation.form is not OperationForm.STATEMENT:
            raise SemanticModelError(
                f"{self.operation.operation_id}: a {self.operation.form.value} operation "
                "cannot be a statement in a body"
            )
        if not isinstance(self.text, str) or not self.text.strip():
            raise SemanticModelError(
                f"{self.operation.operation_id}: statement text must be non-empty"
            )
        for argument in self.arguments:
            if not isinstance(argument, SemanticArgument):
                raise SemanticModelError(
                    f"{self.operation.operation_id}: not an argument: {argument!r}"
                )
        supplied = tuple(argument.name for argument in self.arguments)
        expected = self.operation.parameter_names
        if supplied != expected:
            raise SemanticModelError(
                f"{self.operation.operation_id}: arguments {list(supplied)} do not match "
                f"parameters {list(expected)}"
            )
        for argument in self.arguments:
            parameter = self.operation.parameter(argument.name)
            assert parameter is not None  # guaranteed by the name check above
            if not argument.value.fits(parameter.value_type):
                raise SemanticModelError(
                    f"{self.operation.operation_id}: argument {argument.name} "
                    f"({argument.value.source_text}) does not fit a "
                    f"{parameter.value_type.value} parameter"
                )

    @property
    def operation_id(self) -> str:
        """The stable operation identity this statement expresses."""
        return self.operation.operation_id

    @property
    def source_text(self) -> str:
        """The exact source this statement was read from."""
        return self.text

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
    """

    section_id: str
    operation: SemanticOperation | None
    statements: tuple[SemanticStatement, ...]

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
            for statement in section.operation_statements:
                used.add(statement.operation_id)
        return tuple(sorted(used))
