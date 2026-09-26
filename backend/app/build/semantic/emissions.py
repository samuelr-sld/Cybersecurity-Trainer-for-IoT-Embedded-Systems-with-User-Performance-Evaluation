"""How an operation is WRITTEN in Arduino C++ (Phase B6).

    SemanticOperation.operation_id  ->  CppEmission  ->  the C++ surface form

        program.setup       -> FunctionEmission("void", "setup")  -> void setup()
        gpio.pin_mode       -> CallEmission("pinMode")            -> pinMode(...)

THE TABLE BESIDE THE ANALYZER'S. `analyzer.py` already owns the only other
C++-aware table in this package, `_CALL_OPERATIONS`, and its docstring says
what this module is: "a second source language, or a generator going the other
way, is a table beside this one rather than a change to the IR". This is that
table, read in the emitting direction. `operations.py` stays language-neutral
and `models.py` still holds no syntax at all — the IR does not learn what C++
looks like because B6 exists.

WHY A TABLE AND NOT THE OPERATION ITSELF. An operation id is deliberately
"a stable identifier, never a C++ fragment" (the block catalog's README, and
`operations.py` restating it). `gpio.pin_mode` does not mean `pinMode` — it
means "configure a pin's direction", which a second target language would
write differently. Putting the spelling on `SemanticOperation` would make every
consumer of the IR carry one language's syntax; keeping it here means a second
backend is another table, not a change to the vocabulary.

WHY NOT DERIVE THE SPELLING FROM THE ID. `gpio.pin_mode -> pinMode` looks
mechanical and is not: `time.delay -> delay` drops its namespace,
`program.setup` is a function DEFINITION rather than a call, and the first
operation whose C++ name is not its id in camelCase (an operator, a macro, a
method on an object) would silently generate a call to a function that does not
exist. An explicit row per operation cannot do that.

STILL NOT A COMPILER BACKEND. An emission is a NAME and nothing more — no
argument syntax, no formatting, no statement structure, no types, no includes.
`generator.py` owns how a call is punctuated, exactly as `analyzer.py` owns how
one is recognized.

VALIDATED AS A SET, LIKE EVERY OTHER TABLE HERE. `CppEmissionTable` is the same
shape as `SemanticOperationRegistry`, `app/blockly/catalog.py`'s `BlockCatalog`
and the bridge's `FieldBindingTable`: one normalized dict lookup, built once,
rejecting a malformed row at construction, and answering a miss with a clean
None rather than a guess. Agreement with the operation registry is checked by
`generator.py` on every generation (the tables are injectable) and by
`tests/test_build_semantic_generator.py`, never assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.build.semantic.errors import SemanticModelError, UnsupportedOperationError
from app.build.semantic.operations import (
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TEXT_INDEX_OF,
    TEXT_LENGTH,
    TEXT_SUBSTRING,
    TIME_DELAY,
    SemanticType,
    is_operation_id,
)

#: A C++ identifier. Narrow on purpose: an emission names one plain function,
#: never an expression, a qualified name, a macro invocation or anything with
#: punctuation in it. A `.`, a `(`, a space or a `;` here would mean this table
#: had started carrying syntax, which is `generator.py`'s job.
_CPP_IDENTIFIER_PATTERN = r"^[A-Za-z_]\w*$"

#: A C++ return type, as written before a function's name. Slightly wider than
#: an identifier (`unsigned long`, `const char *`) and still nowhere near a type
#: parser — it is text this table copies out verbatim.
_CPP_RETURN_TYPE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_:*&\s]*$"


class CppEmission:
    """Base for the C++ surface form of one semantic operation.

    One form per IR operation form: a CONTAINER is written as a function
    definition, a STATEMENT as a call, and a VALUE as a method on its first
    operand. They share no fields, so they share no dataclass — only the base
    type the table indexes and the generator matches on.
    """

    __slots__ = ()


@dataclass(frozen=True)
class CallEmission(CppEmission):
    """A statement operation, written as a call to this function.

    `call_name` is exactly the name `analyzer.py`'s `_CALL_OPERATIONS` maps the
    other way, and the test suite pins that the two agree rather than either
    importing the other.
    """

    call_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.call_name, str) or not re.match(
            _CPP_IDENTIFIER_PATTERN, self.call_name
        ):
            raise SemanticModelError(f"invalid C++ call name: {self.call_name!r}")


@dataclass(frozen=True)
class MethodEmission(CppEmission):
    """A VALUE operation, written as a method called on its FIRST operand.

    `text.index_of` -> `MethodEmission("indexOf")` -> `TEXT.indexOf(SEARCH)`:
    the operation's first declared parameter is the receiver and the rest are
    the call's arguments, in declared order. This is the Arduino `String`
    API's shape (`indexOf`, `substring`, `length`) — the class the ESP32 core
    already provides, so the generated firmware needs no new include.
    """

    method_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.method_name, str) or not re.match(
            _CPP_IDENTIFIER_PATTERN, self.method_name
        ):
            raise SemanticModelError(f"invalid C++ method name: {self.method_name!r}")


@dataclass(frozen=True)
class FunctionEmission(CppEmission):
    """A container operation, written as this function's definition.

    No parameter list, because a container declares no parameters
    (`operations.py`: its body is its section's statements). `signature` is
    therefore always `<return type> <name>()`.
    """

    return_type: str
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.return_type, str) or not re.match(
            _CPP_RETURN_TYPE_PATTERN, self.return_type
        ):
            raise SemanticModelError(f"invalid C++ return type: {self.return_type!r}")
        if not isinstance(self.name, str) or not re.match(_CPP_IDENTIFIER_PATTERN, self.name):
            raise SemanticModelError(f"invalid C++ function name: {self.name!r}")

    @property
    def signature(self) -> str:
        """The definition's declarator: `void setup()`."""
        return f"{self.return_type.strip()} {self.name}()"


class CppEmissionTable:
    """Every operation's C++ surface form, indexed by operation id.

    Closed and validated at construction: a malformed id or a value that is not
    an emission is a construction error, `emission` answers a miss with None,
    and `require` raises rather than inventing a spelling — a generator can
    never emit a call to a function nobody declared here.
    """

    def __init__(self, emissions: dict[str, CppEmission]) -> None:
        indexed: dict[str, CppEmission] = {}
        for operation_id, emission in emissions.items():
            if not is_operation_id(operation_id):
                raise SemanticModelError(f"invalid operation id: {operation_id!r}")
            if not isinstance(emission, CppEmission):
                raise SemanticModelError(f"{operation_id}: not a C++ emission: {emission!r}")
            indexed[operation_id] = emission
        self._index = indexed

    @property
    def operation_ids(self) -> tuple[str, ...]:
        """Every operation this table can write, in declaration order."""
        return tuple(self._index)

    def emission(self, operation_id: str) -> CppEmission | None:
        """This operation's surface form, or None — a clean miss, never a guess."""
        return self._index.get(operation_id)

    def require(self, operation_id: str) -> CppEmission:
        """This operation's surface form, or `UnsupportedOperationError`."""
        found = self._index.get(operation_id)
        if found is None:
            raise UnsupportedOperationError(
                f"{operation_id}: no C++ emission declares how this operation is written"
            )
        return found

    def __contains__(self, operation_id: object) -> bool:
        return operation_id in self._index

    def __len__(self) -> int:
        return len(self._index)


def build_default_emissions() -> CppEmissionTable:
    """The C++ spelling of every operation that has a fixed spelling.

    Every statement and value operation `build_default_operations()` declares,
    plus the two fixed-name containers. `functions.implementation` is absent
    by design: its declarator is preserved per section, never looked up here
    (see `generator.py`). An emission for an operation the IR does not declare
    would be a spelling nothing can reach; the generator checks on every run.
    """
    return CppEmissionTable(
        {
            PROGRAM_SETUP: FunctionEmission(return_type="void", name="setup"),
            PROGRAM_LOOP: FunctionEmission(return_type="void", name="loop"),
            GPIO_PIN_MODE: CallEmission(call_name="pinMode"),
            GPIO_DIGITAL_WRITE: CallEmission(call_name="digitalWrite"),
            TIME_DELAY: CallEmission(call_name="delay"),
            TEXT_INDEX_OF: MethodEmission(method_name="indexOf"),
            TEXT_SUBSTRING: MethodEmission(method_name="substring"),
            TEXT_LENGTH: MethodEmission(method_name="length"),
        }
    )


#: How a local of each declarable type is written. The Arduino `String` class
#: (not `const char *`) for text, because the value layer's text operations
#: are `String` methods; `int` because every numeric local this IR can compute
#: is a position or a length. `analyzer.py` holds the inverse table and a test
#: pins the two against each other.
CPP_DECLARATION_TYPES = {
    SemanticType.TEXT: "String",
    SemanticType.NUMBER: "int",
    SemanticType.BOOLEAN: "bool",
}


#: Process-wide default table. Immutable, so sharing it is safe.
default_cpp_emissions = build_default_emissions()
