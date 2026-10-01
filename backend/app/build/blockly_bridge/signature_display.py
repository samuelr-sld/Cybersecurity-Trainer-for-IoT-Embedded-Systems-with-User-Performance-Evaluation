"""A function's preserved C++ declarator, described for DISPLAY only (P4.2).

    SemanticSection.signature          "static void applyCommand(const String &message)"
            |                           (authoritative - B6 writes it back verbatim)
            v  adapter.py
    BlocklyBlock.container_signature   the same string, provenance only
            |
            v  BlocklyBlock.to_state() -> signature_extra_state()   (this module)
    Blockly `extraState`               {"signature": {...}} - what the header draws

WHY. A student opening a helper saw a container labelled "function body" and no
hint that `message` is a parameter they can read. The declarator already
reached the bridge (`container_signature`) and was dropped at `to_state()`;
this module is the smallest thing that turns it into something an editor can
draw.

DISPLAY, NEVER A SOURCE OF TRUTH. Nothing here is consulted by B5 or B6.
`workspace_state.py` never reads `extraState`, and
`section_blockly.py::program_with_section` copies the CURRENT section's own
`signature` onto every submission - so a forged or mutated `extraState` has no
path into generated C++. The parsed structure exists only to be looked at.

CONSERVATIVE. `parse_signature` understands the declarator shapes this
platform's firmware actually has: specifiers, a return type, a name, and a
parameter list whose entries are `<type> <name>` with `const`, `&`, `*` and a
trailing `[]`. Anything else - a default argument, a function-pointer
parameter, a variadic `...`, an unnamed parameter, a template - returns None,
and `signature_extra_state` then offers only the raw text, which the editor
draws as-is. A declarator that cannot be described is never guessed at and
never makes otherwise valid firmware fail.

PURE AND STDLIB-ONLY. Text in, data out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Declaration specifiers that are not part of the return type proper.
STORAGE_SPECIFIERS = ("static", "inline", "extern", "constexpr", "virtual")

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_NAME_AT_END = rf"({_IDENTIFIER})\s*$"
_PARAMETER = (
    rf"^(?P<type>.*?[\s*&])(?P<name>{_IDENTIFIER})\s*(?P<array>(?:\[\s*[A-Za-z0-9_]*\s*\]\s*)*)$"
)
#: Characters a displayed type may consist of. Anything else (`(`, `=`, `.`)
#: means a shape this reader does not describe.
_TYPE_TEXT = r"^[A-Za-z0-9_\s:<>,*&\[\]]+$"
_KEYWORDS = frozenset({"const", "volatile", "unsigned", "signed", "struct", "enum", "class"})


@dataclass(frozen=True)
class ParameterDisplay:
    """One parameter as the header shows it: `message : const String &`."""

    type_text: str
    name: str
    const: bool
    reference: bool
    pointer: bool

    def to_state(self) -> dict[str, Any]:
        return {
            "type": self.type_text,
            "name": self.name,
            "const": self.const,
            "reference": self.reference,
            "pointer": self.pointer,
        }


@dataclass(frozen=True)
class SignatureDisplay:
    """A declarator split into the parts a read-only header draws."""

    text: str
    specifiers: tuple[str, ...]
    return_type: str
    name: str
    parameters: tuple[ParameterDisplay, ...]

    def to_state(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "specifiers": list(self.specifiers),
            "returnType": self.return_type,
            "name": self.name,
            "parameters": [parameter.to_state() for parameter in self.parameters],
        }


def display_text(signature: str) -> str:
    """The declarator on one line, whitespace runs collapsed - exactly its tokens."""
    return " ".join(signature.split())


def parse_signature(signature: str) -> SignatureDisplay | None:
    """Describe `signature` for display, or None if its shape is not one this reads."""
    if not isinstance(signature, str):
        return None
    text = display_text(signature)
    if not text.endswith(")"):
        return None  # a trailing qualifier (`const`, `override`, `noexcept`) or not a declarator
    open_index = _matching_open(text)
    if open_index is None:
        return None
    head, inner = text[:open_index].rstrip(), text[open_index + 1 : -1].strip()
    name_match = re.search(_NAME_AT_END, head)
    if name_match is None or name_match.group(1) in _KEYWORDS:
        return None
    name = name_match.group(1)
    words = head[: name_match.start()].split()
    specifiers = tuple(word for word in words if word in STORAGE_SPECIFIERS)
    return_type = " ".join(word for word in words if word not in STORAGE_SPECIFIERS)
    if not return_type or not re.match(_TYPE_TEXT, return_type):
        return None
    parameters = _parameters(inner)
    if parameters is None:
        return None
    return SignatureDisplay(
        text=text,
        specifiers=specifiers,
        return_type=_tidy(return_type),
        name=name,
        parameters=parameters,
    )


def signature_extra_state(signature: str | None) -> dict[str, Any] | None:
    """The `extraState` a container block carries for its header, or None.

    A declarator this module can describe yields every part; one it cannot
    yields only its raw text, which the editor shows verbatim. No declarator
    at all (`setup`/`loop`) yields None, and the block keeps its plain label.
    """
    if signature is None or not signature.strip():
        return None
    parsed = parse_signature(signature)
    return {"signature": parsed.to_state() if parsed is not None else {"text": display_text(signature)}}


# --- internals --------------------------------------------------------------


def _matching_open(text: str) -> int | None:
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_top_level(inner: str) -> list[str] | None:
    """Comma-separated entries, ignoring commas inside `<>`, `()` and `[]`."""
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(inner):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
            if depth < 0:
                return None
        elif char == "," and depth == 0:
            parts.append(inner[start:index])
            start = index + 1
    if depth != 0:
        return None
    parts.append(inner[start:])
    return parts


def _parameters(inner: str) -> tuple[ParameterDisplay, ...] | None:
    if inner in ("", "void"):
        return ()
    entries = _split_top_level(inner)
    if entries is None:
        return None
    found: list[ParameterDisplay] = []
    for entry in entries:
        entry = entry.strip()
        if not entry or "(" in entry or "=" in entry or "..." in entry:
            return None  # empty, function pointer, default argument, variadic
        match = re.match(_PARAMETER, entry, re.DOTALL)
        if match is None:
            return None  # unnamed, or not `<type> <name>`
        type_text = match.group("type").strip()
        words = re.findall(_IDENTIFIER, type_text)
        if (
            not re.match(_TYPE_TEXT, type_text)
            or match.group("name") in _KEYWORDS
            or all(word in _KEYWORDS for word in words)  # `const x`: no type named
        ):
            return None
        array = match.group("array").replace(" ", "")
        type_text = _tidy(type_text) + (" " + array if array else "")
        found.append(
            ParameterDisplay(
                type_text=type_text,
                name=match.group("name"),
                const="const" in re.findall(_IDENTIFIER, type_text),
                reference="&" in type_text,
                pointer="*" in type_text or bool(array),
            )
        )
    return tuple(found)


def _tidy(type_text: str) -> str:
    """`const String&` / `const String &` -> `const String &`; `char*` -> `char *`."""
    tidy = re.sub(r"\s*([*&])", r" \1", type_text)
    tidy = re.sub(r"([*&])\s+(?=[*&])", r"\1", tidy)
    return " ".join(tidy.split())
