"""C++ structural discovery — Arduino `.ino` text in, `BuildDocument` out.

    raw .ino text -> analyze_source(text) -> BuildDocument -> CodeSection[]

THIS IS NOT A C++ COMPILER FRONTEND. It is a controlled lexical scanner:
brace-depth tracking plus comment/string-literal awareness, enough to find
top-level function definitions and split the source around them without
attempting semantic interpretation of arbitrary C++. That scope is
deliberate — see the B1 audit this module implements.

PURE, ISOLATED, STDLIB-ONLY. `analyze_source` takes a string and returns a
`BuildDocument`; nothing else. No filesystem access, no subprocess, no
`eval`/`exec`, and no import of `app.build.workspace`, `app.build_sessions`,
`app.panels`, `app.hardware`, or any Blockly module — this module has no
idea any of those exist, on purpose (see `tests/test_build_discovery.py`'s
static import-boundary check). It is a pure function of its input: the same
source string always produces the same `BuildDocument`.

ALGORITHM. One forward pass over the source tracks brace depth while
uniformly skipping line comments, block comments, string literals, and
character literals wherever they occur (so a `{`/`}` inside any of those
never affects depth). Whenever depth returns to 0 after a `{` opened at
depth 0, the analyzer looks at the run of text between wherever the
previous section ended and that `{`: if backward-scanning from the `)`
immediately preceding it finds a plausible `<declarator> <name>(<params>)`
shape, the whole span from the declarator's start to the closing `}` becomes
one function-kind `CodeSection` (SETUP/LOOP by name, otherwise a
HELPER_FUNCTION provisionally); the gap text before it, if any, becomes its
own GLOBAL_DECLARATIONS section. A `{` that does NOT look like a function
signature (an array/struct initializer, an anonymous block) is still
depth-tracked correctly so nested content inside it cannot confuse later
scanning, but it creates no section of its own — it simply stays part of
whatever GLOBAL_DECLARATIONS gap eventually gets emitted around it. This is
what satisfies the "arrays where relevant" requirement without the analyzer
ever needing to understand what an array is.

A second pass reclassifies HELPER_FUNCTION sections whose bare name (a
reference, never a call — i.e. not immediately followed by `(`) appears
anywhere else in the source as CALLBACK. See `_is_referenced_as_callback`
for why this is a source-level relationship and not a hardcoded MQTT/library
name — the exact seam the B1 brief asked for.

WHAT COUNTS AS AN ERROR. Only genuine structural breakage raises:
`EmptySourceError` (nothing to analyze) and `UnterminatedLiteralError`/
`UnbalancedBracesError` (a string, comment, or brace never closes, so no
honest split can be produced at all). A missing `setup()`, a missing
`loop()`, source with no functions at all, or a top-level construct the
analyzer cannot confidently classify as a function are NOT errors — they
produce an honest, smaller `BuildDocument` (no SETUP/LOOP section, a single
GLOBAL_DECLARATIONS section, or that construct folded into surrounding
declarative text) rather than a fabricated placeholder or a raised
exception. "Do not silently fabricate sections" cuts both ways: never invent
one that isn't there, but also never refuse to describe real source just
because part of it isn't a function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.build.discovery.models import BuildDocument, CodeSection, SectionKind


class DiscoveryError(ValueError):
    """Base for every error raised while analyzing source text."""


class EmptySourceError(DiscoveryError):
    """The source is empty or contains only whitespace."""


class UnterminatedLiteralError(DiscoveryError):
    """A string literal, character literal, or block comment never closes."""


class UnbalancedBracesError(DiscoveryError):
    """Brace nesting never returns to zero, or a stray `}` has no opener."""


#: C/C++ keywords that can be immediately followed by `(...) {` at what a
#: naive scan might mistake for top level (an `if`/`for`/`while`/`switch`
#: inside a function we failed to detect, for instance). Excluding these by
#: name is not "hardcoding a panel" — it is the language's own fixed control
#: -flow vocabulary, the same kind of thing `_CONTROL_LITERALS` style guards
#: appear for elsewhere in this codebase's parsers.
_CONTROL_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch"})

#: Characters a function's qualifiers/return-type/name run may contain,
#: walking backward from its parameter list's opening `(`. Deliberately
#: narrow: hitting anything outside this set (a `;`, `}`, `/`, `=`, quote,
#: etc.) means we have left the declarator and found the true start of the
#: gap text that precedes it.
#:
#: Kept as a plain pattern STRING, not a pre-compiled `re.Pattern` — the
#: build layer's own `test_build_layer_has_no_execution_primitives` bans the
#: bare token "compile" anywhere under `app/build/` (it does not distinguish
#: `re.compile` from the `compile()` builtin), so every match below goes
#: through the module-level `re.match`/`re.search` functions instead, which
#: Python's `re` module already caches internally — no repeated-compilation
#: cost for a tool operating on single small files.
_DECLARATOR_CHAR_PATTERN = r"[A-Za-z0-9_:*&\s]"

#: The function name is the trailing identifier of a declarator run, e.g.
#: "static void setMotorOutputs" -> "setMotorOutputs".
_TRAILING_IDENTIFIER_PATTERN = r"([A-Za-z_]\w*)\s*\Z"

_KIND_ID_PREFIX = {
    SectionKind.GLOBAL_DECLARATIONS: "global",
    SectionKind.SETUP: "setup",
    SectionKind.LOOP: "loop",
    SectionKind.CALLBACK: "callback",
    SectionKind.HELPER_FUNCTION: "helper",
}


@dataclass
class _RawSection:
    """A section before it has an id. `CodeSection` requires a non-empty,
    unique `section_id` at construction — see its `__post_init__` — but the
    id is only decidable once callback classification (pass 2) has settled
    each section's final `kind`. This mutable, unvalidated intermediate
    keeps the scan/classify passes from ever needing to construct an invalid
    `CodeSection` as a placeholder."""

    kind: SectionKind
    name: str | None
    text: str
    start_offset: int
    end_offset: int
    signature: str | None


def analyze_source(source: str) -> BuildDocument:
    """Discover `source`'s top-level structure. Raises on genuine breakage.

    Deterministic and side-effect free: called twice with the same string,
    this returns two `BuildDocument`s that compare equal in every field.
    """
    if not isinstance(source, str):
        raise TypeError(f"source must be a string, got {type(source).__name__}")
    if not source.strip():
        raise EmptySourceError("source is empty or contains only whitespace")

    raw_sections = _scan_top_level(source)
    classified = _classify_callbacks(source, raw_sections)
    finalized = _finalize_ids(classified)
    return BuildDocument(source=source, sections=finalized)


# --- pass 1: top-level scan -------------------------------------------------


def _scan_top_level(source: str) -> list[_RawSection]:
    n = len(source)
    i = 0
    depth = 0
    pending_start = 0
    # The pending function's (signature_start, name, signature_text), set the
    # moment a depth-0 '{' is recognised as a function body's opener, and
    # consumed when depth returns to 0 at the matching '}'. None means the
    # brace pair currently open at depth 1 was not a recognised function (an
    # array/struct initializer, an anonymous block) — still depth-tracked,
    # just not split into its own section.
    pending_function: tuple[int, str, str] | None = None
    sections: list[_RawSection] = []

    while i < n:
        skip_to = _skip_non_code(source, i)
        if skip_to is not None:
            i = skip_to
            continue

        ch = source[i]
        if ch == "{":
            if depth == 0:
                pending_function = _match_function_signature(source, pending_start, i)
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth < 0:
                raise UnbalancedBracesError(f"unmatched closing brace at offset {i}")
            i += 1
            if depth == 0:
                body_end = i
                if pending_function is not None:
                    sig_start, name, signature = pending_function
                    if sig_start > pending_start:
                        sections.append(_gap_section(source, pending_start, sig_start))
                    sections.append(
                        _function_section(source, sig_start, body_end, name, signature)
                    )
                    pending_start = body_end
                pending_function = None
            continue
        i += 1

    if depth != 0:
        raise UnbalancedBracesError(f"{depth} brace(s) still open at end of source")
    if pending_start < n:
        sections.append(_gap_section(source, pending_start, n))
    return sections


def _skip_non_code(source: str, i: int) -> int | None:
    """If `i` starts a comment or string/char literal, the index past it.

    None if `source[i]` is ordinary code — the caller handles it literally.
    Shared by the top-level scan and the callback-reference sanitizer so
    comment/string boundary logic exists exactly once.
    """
    if source.startswith("//", i):
        newline = source.find("\n", i)
        return len(source) if newline == -1 else newline + 1
    if source.startswith("/*", i):
        end = source.find("*/", i + 2)
        if end == -1:
            raise UnterminatedLiteralError(f"unterminated block comment starting at offset {i}")
        return end + 2
    ch = source[i]
    if ch in ("\"", "'"):
        return _skip_quoted(source, i, ch)
    return None


def _skip_quoted(source: str, i: int, quote: str) -> int:
    n = len(source)
    j = i + 1
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        j += 1
    raise UnterminatedLiteralError(f"unterminated {quote!r} literal starting at offset {i}")


def _match_function_signature(
    source: str, pending_start: int, brace_index: int
) -> tuple[int, str, str] | None:
    """Is the text since the last section a function signature ending here?

    Returns `(absolute_signature_start, name, signature_text)` or None. A
    None here is not a failure of the analyzer — it means this particular
    `{` opens something other than a function definition (an initializer, a
    bare block), which the caller correctly leaves unsplit.
    """
    text = source[pending_start:brace_index]
    stripped = text.rstrip()
    if not stripped.endswith(")"):
        return None

    open_idx = _find_matching_open_paren(stripped, len(stripped) - 1)
    if open_idx is None:
        return None

    decl_start = _find_declarator_start(stripped, open_idx)
    declarator = stripped[decl_start:open_idx]
    name_match = re.search(_TRAILING_IDENTIFIER_PATTERN, declarator)
    if name_match is None:
        return None
    name = name_match.group(1)
    if name in _CONTROL_KEYWORDS:
        return None
    return_type = declarator[: name_match.start()].rstrip()
    if not return_type:
        return None

    signature = stripped[decl_start:].strip()
    return pending_start + decl_start, name, signature


def _find_matching_open_paren(text: str, close_idx: int) -> int | None:
    """The index of the `(` balancing `text[close_idx] == ')'`, or None."""
    depth = 0
    i = close_idx
    while i >= 0:
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return None


def _find_declarator_start(text: str, before: int) -> int:
    """Walk `text` backward from `before` while chars look declarator-safe."""
    i = before - 1
    while i >= 0 and re.match(_DECLARATOR_CHAR_PATTERN, text[i]):
        i -= 1
    return i + 1


def _gap_section(source: str, start: int, end: int) -> _RawSection:
    return _RawSection(
        kind=SectionKind.GLOBAL_DECLARATIONS,
        name=None,
        text=source[start:end],
        start_offset=start,
        end_offset=end,
        signature=None,
    )


def _function_section(
    source: str, start: int, end: int, name: str, signature: str
) -> _RawSection:
    kind = {"setup": SectionKind.SETUP, "loop": SectionKind.LOOP}.get(
        name, SectionKind.HELPER_FUNCTION
    )
    return _RawSection(
        kind=kind,
        name=name,
        text=source[start:end],
        start_offset=start,
        end_offset=end,
        signature=signature,
    )


# --- pass 2: generic callback classification --------------------------------


def _classify_callbacks(source: str, sections: list[_RawSection]) -> list[_RawSection]:
    """Promote a HELPER_FUNCTION to CALLBACK if it is referenced, not called.

    GENERIC BY CONSTRUCTION — NO MQTT/LIBRARY-SPECIFIC NAME ANYWHERE HERE.
    The signal is purely structural: does this function's bare name occur
    anywhere outside its own section, in real code (comments and string
    contents are blanked out first — see `_sanitize_for_reference_scan` —
    so a mention in prose can never trigger this), in a position NOT
    immediately followed by `(`? That is exactly the shape of a callback
    registration (`client.setCallback(onMessage)`, a function pointer stored
    in a struct, ...): the name is passed BY REFERENCE rather than invoked.
    SETUP/LOOP are never candidates — Arduino's own two fixed entry points,
    already classified by name in `_function_section`, are not "callbacks"
    in this sense even though `setup`/`loop` are themselves called by the
    runtime outside this file.
    """
    sanitized = _sanitize_for_reference_scan(source)
    for section in sections:
        if section.kind is SectionKind.HELPER_FUNCTION and _is_referenced_as_callback(
            sanitized, section
        ):
            section.kind = SectionKind.CALLBACK
    return sections


def _sanitize_for_reference_scan(source: str) -> str:
    """`source` with every comment/string/char literal blanked to spaces.

    Length-preserving (so offsets used elsewhere stay meaningful) but that
    is incidental here — `_is_referenced_as_callback` only needs the result
    to be free of any text a human wrote as prose rather than code, reusing
    the exact same boundary logic `_skip_non_code` already uses for the
    structural scan rather than a second, possibly-divergent implementation.
    """
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        skip_to = _skip_non_code(source, i)
        if skip_to is not None:
            out.append(" " * (skip_to - i))
            i = skip_to
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


def code_mask(source: str) -> str:
    """`source` with every comment and string/char literal blanked to spaces.

    The public name for `_sanitize_for_reference_scan`, added in Phase B3 for
    one reason: the semantic layer (`app/build/semantic/analyzer.py`) has to
    find statement boundaries inside a function body, which means it needs
    exactly the same "is this character real code?" answer this module's two
    passes already needed. Exposing the existing helper is the alternative to
    a second, possibly-divergent implementation of comment/string boundaries
    living one package away — the same argument `_sanitize_for_reference_scan`
    itself makes for reusing `_skip_non_code` rather than re-deriving it.

    Length-preserving, so an index into the result is an index into `source`.
    Adds no behaviour and changes none: B1's structural analysis is untouched.
    """
    return _sanitize_for_reference_scan(source)


def _is_referenced_as_callback(sanitized_source: str, section: _RawSection) -> bool:
    assert section.name is not None
    outside = sanitized_source[: section.start_offset] + sanitized_source[section.end_offset :]
    pattern = rf"\b{re.escape(section.name)}\b(?!\s*\()"
    return re.search(pattern, outside) is not None


# --- finalize: stable, readable ids -----------------------------------------


def _finalize_ids(sections: list[_RawSection]) -> tuple[CodeSection, ...]:
    """Turn each `_RawSection` into a real, id-bearing `CodeSection`.

    Deferred to this final pass (rather than assigned during scanning)
    because callback reclassification can change a section's kind, and the
    id's prefix follows the FINAL kind (`callback_onMessage`, never
    `helper_onMessage`).
    """
    counts: dict[str, int] = {}
    finalized: list[CodeSection] = []
    for section in sections:
        prefix = _KIND_ID_PREFIX[section.kind]
        # SETUP/LOOP: `name` is always "setup"/"loop", identical to the
        # prefix itself — suffixing it would give the redundant
        # "setup_setup"/"loop_loop" rather than the plain "setup"/"loop".
        if section.name is None or section.kind in (SectionKind.SETUP, SectionKind.LOOP):
            base = prefix
        else:
            base = f"{prefix}_{section.name}"
        counts[base] = counts.get(base, 0) + 1
        section_id = base if counts[base] == 1 else f"{base}_{counts[base]}"
        finalized.append(
            CodeSection(
                section_id=section_id,
                kind=section.kind,
                name=section.name,
                text=section.text,
                start_offset=section.start_offset,
                end_offset=section.end_offset,
                signature=section.signature,
            )
        )
    return tuple(finalized)
