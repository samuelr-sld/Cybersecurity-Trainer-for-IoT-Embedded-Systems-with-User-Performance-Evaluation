"""How an operand reaches a real Blockly field, and how it comes back.

    SemanticValue  ->  FieldBinding.render(value)  ->  the field's text   (B4)
    the field's text  ->  FieldBinding.parse(text)  ->  SemanticValue     (B5)

THE ONE THING THE CATALOG CANNOT TELL US. `app/blockly/` says a
`gpio.pin_mode` block takes a `PIN` and a `MODE` and that `MODE` is TEXT. It
does not say — and deliberately does not model — that the built block draws
`PIN` as a free-text field and `MODE` as a dropdown offering exactly OUTPUT,
INPUT and INPUT_PULLUP. That is a property of the Blockly block in
`src/blockly/arduinoBlocks.js`, not of the concept, and the catalog is
concept-level metadata for ~200 blocks that do not exist yet.

So this table exists, and it is the ONLY mapping B4 adds. It is keyed by
SEMANTIC OPERATION ID, never by Blockly type, so the operation -> block ->
`blockly_type` relation the catalog already owns is not restated anywhere in
this package. `adapter.py` cross-checks every binding against the catalog
block's inputs and the IR operation's parameters on each conversion, and
`tests/test_build_blockly_bridge.py` additionally reads the real block
definitions out of `arduinoBlocks.js` and asserts the dropdown options here
are the dropdown options there — the same test-enforced agreement B3 uses for
the operation vocabulary itself, rather than a dependency no language boundary
would allow.

ONE RULE FOR EVERY VALUE: A FIELD HOLDS THE OPERAND AS WRITTEN. The text put
into a field is always `SemanticValue.source_text` — `1000`, `START_BUTTON`,
`INPUT_PULLUP`, `HIGH`. Nothing is resolved (B3 does not resolve names and
neither does this), nothing is reformatted, and no value is translated into a
different token. A field kind only decides whether it may hold that text at
all; when it may not, `render` returns None and `adapter.py` preserves the
whole statement verbatim. There is no coercion path anywhere in this module —
that is the point of it.

AND THE SAME RULE COMING BACK. `parse` reads a field's text as the value it
literally is: a number stays a number, a name stays a name (`START_BUTTON` is
never looked up, resolved or turned into a pin number), and a token the field
cannot hold is refused rather than reinterpreted. It lives here, beside
`render`, because the two are one fact stated twice — a second module deciding
what a field's text means is how the two directions would drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.build.semantic import (
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    TIME_DELAY,
    LiteralValue,
    SemanticType,
    SemanticValue,
    SymbolValue,
)

#: How a field's text is read back as a value. The same three token shapes
#: B3's analyzer recognizes in an argument position, restated here for the
#: dependency reason `app/build/semantic/operations.py` documents (the bridge
#: may import the semantic package, but the token rule is B3's private
#: business and reaching into it would couple the two). A quoted string is
#: deliberately absent: no implemented block draws a field a text literal
#: could occupy, so parsing one would invent a case this phase cannot reach.
_INTEGER_PATTERN = r"-?\d+"
_DECIMAL_PATTERN = r"-?(?:\d+\.\d*|\.\d+)"
_IDENTIFIER_PATTERN = r"[A-Za-z_]\w*"


def _token_value(text: str) -> SemanticValue | None:
    """One field token as the value it is written as, or None if it is none.

    Deliberately narrow. Anything with structure — arithmetic, a ternary, a
    call, a cast — is not a value the IR can state, so it is refused here and
    the caller reports it rather than guessing at a meaning.
    """
    if not isinstance(text, str):
        return None
    token = text.strip()
    if not token:
        return None
    if token == "true":
        return LiteralValue(value=True, value_type=SemanticType.BOOLEAN)
    if token == "false":
        return LiteralValue(value=False, value_type=SemanticType.BOOLEAN)
    if re.fullmatch(_INTEGER_PATTERN, token):
        return LiteralValue(value=int(token), value_type=SemanticType.NUMBER)
    if re.fullmatch(_DECIMAL_PATTERN, token):
        return LiteralValue(value=float(token), value_type=SemanticType.NUMBER)
    if re.fullmatch(_IDENTIFIER_PATTERN, token):
        return SymbolValue(name=token)
    return None


class FieldKind(str, Enum):
    """The kind of Blockly field an operand is drawn as.

    Exactly the three `src/blockly/arduinoBlocks.js` uses today. A value slot
    (an operand filled by another block rather than typed into a field) is
    absent because no implemented block has one; the first block that does
    adds a member here, not a mechanism.

    TEXT      `Blockly.FieldTextInput` — holds any token, which is what lets a
              pin stay the NAME the firmware wrote (`START_BUTTON`) instead of
              a number nobody resolved.
    NUMBER    `Blockly.FieldNumber` — holds a numeric literal and nothing
              else. A named constant in such a slot is not representable.
    DROPDOWN  `Blockly.FieldDropdown` — holds one of a fixed list of tokens.
    """

    TEXT = "text"
    NUMBER = "number"
    DROPDOWN = "dropdown"


def _is_numeric(value: SemanticValue) -> bool:
    """True for a value a `Blockly.FieldNumber` can hold, in either direction."""
    return isinstance(value, LiteralValue) and value.value_type in (
        SemanticType.NUMBER,
        SemanticType.PIN,
    )


@dataclass(frozen=True)
class FieldBinding:
    """One operand's field: its name, its kind, and what it accepts."""

    input_name: str
    kind: FieldKind
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FieldKind):
            raise ValueError(f"{self.input_name}: invalid field kind {self.kind!r}")
        if (self.kind is FieldKind.DROPDOWN) != bool(self.options):
            raise ValueError(
                f"{self.input_name}: options exist exactly when the field is a dropdown"
            )

    def render(self, value: SemanticValue) -> str | None:
        """The text this field would hold, or None if it cannot hold this value.

        None is not an error — see the module docstring. It is how
        `digitalWrite(PIN, true)` and `delay(BUZZER_CHIRP_MS)` stay honest:
        the meaning is understood, the field cannot express it, and the
        statement is preserved verbatim rather than rewritten into one that
        happens to fit.
        """
        text = value.source_text
        if self.kind is FieldKind.TEXT:
            return text
        if self.kind is FieldKind.NUMBER:
            return text if _is_numeric(value) else None
        return text if text in self.options else None

    def parse(self, text: str) -> SemanticValue | None:
        """The value this field's text stands for, or None if it stands for none.

        The exact inverse of `render`, and refuses on exactly the same grounds:
        a name cannot come out of a `FieldNumber`, and a token outside a
        dropdown's options cannot come out of that dropdown. None is a refusal
        the caller reports — unlike the forward direction, there is nothing to
        fall back on, because a workspace holds no original source text for a
        field the way a `SemanticStatement` does.
        """
        value = _token_value(text)
        if value is None:
            return None
        if self.kind is FieldKind.DROPDOWN:
            return value if text.strip() in self.options else None
        if self.kind is FieldKind.NUMBER:
            return value if _is_numeric(value) else None
        return value


class FieldBindingTable:
    """Every operation's field bindings, indexed by operation id.

    Closed and validated at construction, like the block catalog and the
    operation registry it sits between: a duplicate operation or a duplicate
    input name is a construction error, and a miss is a clean None rather
    than an invented binding.
    """

    def __init__(self, bindings: dict[str, tuple[FieldBinding, ...]]) -> None:
        indexed: dict[str, tuple[FieldBinding, ...]] = {}
        for operation_id, fields in bindings.items():
            if operation_id in indexed:
                raise ValueError(f"duplicate operation binding: {operation_id}")
            names = [field.input_name for field in fields]
            if len(set(names)) != len(names):
                raise ValueError(f"{operation_id}: duplicate input names in its bindings")
            indexed[operation_id] = tuple(fields)
        self._index = indexed

    def fields_for(self, operation_id: str) -> tuple[FieldBinding, ...] | None:
        """This operation's bindings in declared order, or None if unbound."""
        return self._index.get(operation_id)

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(self._index)

    def __contains__(self, operation_id: object) -> bool:
        return operation_id in self._index

    def __len__(self) -> int:
        return len(self._index)


def build_default_bindings() -> FieldBindingTable:
    """The bindings for the operations that have a real block behind them.

    `program.setup` and `program.loop` are absent, and that is not an
    oversight: a container has no operands at all (its body is its section's
    statements, which `operations.py` states explicitly), so it binds no
    fields. `adapter.py` therefore looks up a binding only for a statement
    operation, and requires that one to cover every parameter.

    The declared order matches the operation's parameter order, which matches
    the catalog block's input order. `adapter.py` asserts all three agree
    rather than trusting this comment.
    """
    return FieldBindingTable(
        {
            GPIO_PIN_MODE: (
                FieldBinding("PIN", FieldKind.TEXT),
                FieldBinding("MODE", FieldKind.DROPDOWN, ("OUTPUT", "INPUT", "INPUT_PULLUP")),
            ),
            GPIO_DIGITAL_WRITE: (
                FieldBinding("PIN", FieldKind.TEXT),
                FieldBinding("VALUE", FieldKind.DROPDOWN, ("HIGH", "LOW")),
            ),
            TIME_DELAY: (FieldBinding("MS", FieldKind.NUMBER),),
        }
    )


#: Process-wide default table. Immutable, so sharing it is safe.
default_field_bindings = build_default_bindings()
