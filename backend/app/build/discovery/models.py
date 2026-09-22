"""Passive data model for structurally-discovered firmware source (Phase B1).

Position in the target architecture (see the B0 audit this phase followed):

    PanelPackage -> vulnerable .ino -> C++ structural analysis -> BuildDocument/CodeSection[]
                                        (app/build/discovery/analyzer.py)   (this module)
                                              -> semantic representation -> existing Blockly -> ...

THIS MODULE DOES NOT PARSE ANYTHING. Exactly like `app/build/models.py` (whose
module docstring makes the same point about `FileSegment`), this is shape
only — `app/build/discovery/analyzer.py` is the one thing that produces a
`BuildDocument`, and it always hands back a fully-formed, already-validated
one or raises. Nothing here reads a file, and nothing here knows a session,
a workspace, a panel, or a compiler exists.

A `CodeSection` IS NOT A `FileSegment`. `FileSegment` (`app/build/models.py`)
answers a *permission* question — locked or editable — for text a student can
already interact with in a live `BuildWorkspace`. A `CodeSection` answers a
*structural* question — what kind of C++ construct is this, and what is its
name/signature — for text nobody has decided anything about yet. Conflating
the two would force every consumer of the existing, already-tested region
model to understand fields it doesn't need. A later phase (not B1) is what
turns a `CodeSection` into a `FileSegment`; this module has no knowledge of
that conversion and imports nothing from `app.build.models`.

SECTION COVERAGE. A `BuildDocument`'s `sections` partition its `source`
into contiguous, ordered, non-overlapping spans — every character in
`source` belongs to exactly one section, in source order. Top-level text
that is not part of a recognized function body (leading includes/constants,
comments and blank lines between functions, anything trailing the last
function) is represented as its own `GLOBAL_DECLARATIONS`-kind section
wherever it occurs — there can be more than one, since "declarative,
non-function top-level text" can appear before, between, and after
functions, not only at the very start of the file. This is enforced by
`BuildDocument.__post_init__` below, not merely produced by convention: an
analyzer bug that dropped or duplicated a character would fail construction
rather than silently corrupting the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SectionKind(str, Enum):
    """What kind of C++ construct a `CodeSection` represents.

    GLOBAL_DECLARATIONS  top-level text that is not a function body: includes,
                         constants, object construction, and anything else
                         sitting between/around functions. Never carries a
                         `name` or `signature`.
    SETUP                the Arduino entry point `void setup() { ... }`.
    LOOP                 the Arduino main loop `void loop() { ... }`.
    CALLBACK             a top-level function whose bare name (not a call of
                         it) appears elsewhere in the source — the generic,
                         source-level signal for "this was registered as a
                         callback somewhere", never a hardcoded MQTT/library
                         function name. See the analyzer's module docstring.
    HELPER_FUNCTION      any other top-level function definition. The default
                         for a function B1 cannot confidently classify as a
                         callback.
    """

    GLOBAL_DECLARATIONS = "global_declarations"
    SETUP = "setup"
    LOOP = "loop"
    CALLBACK = "callback"
    HELPER_FUNCTION = "helper_function"


#: Kinds that name one real function definition, as opposed to declarative
#: filler text. `CodeSection.__post_init__` uses this to decide whether
#: `name`/`signature` are required or forbidden.
_FUNCTION_KINDS = frozenset(
    {SectionKind.SETUP, SectionKind.LOOP, SectionKind.CALLBACK, SectionKind.HELPER_FUNCTION}
)


@dataclass(frozen=True)
class CodeSection:
    """One contiguous, classified span of a `BuildDocument`'s source.

    `start_offset`/`end_offset` are the authoritative range — a half-open
    slice of `source`, i.e. `source[start_offset:end_offset] == text` always
    holds (checked below). Line numbers are deliberately not stored: they are
    a diagnostic *view* of an offset, derivable on demand from a
    `BuildDocument` via `line_at`, not a second copy of the same fact that
    could drift from the offsets — the same "one source of truth" discipline
    `app/panels/models.py` documents for why a package never repeats a value
    the executable scenario state already owns.

    `name`/`signature` are populated for every function-shaped kind (SETUP,
    LOOP, CALLBACK, HELPER_FUNCTION) and forbidden for GLOBAL_DECLARATIONS,
    which names no single construct. `signature` is the declarator text only
    (e.g. `"static void setMotorOutputs(bool run)"`) — never including the
    body's opening brace, and never semantically parsed beyond a return
    type/qualifiers + name + parameter list split; see the analyzer for what
    "lightweight" means here.
    """

    section_id: str
    kind: SectionKind
    name: str | None
    text: str
    start_offset: int
    end_offset: int
    signature: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.section_id, str) or not self.section_id.strip():
            raise ValueError(f"section_id must be a non-empty string, got {self.section_id!r}")
        if not isinstance(self.kind, SectionKind):
            raise ValueError(f"{self.section_id}: invalid kind {self.kind!r}")
        if (
            not isinstance(self.start_offset, int)
            or not isinstance(self.end_offset, int)
            or isinstance(self.start_offset, bool)
            or isinstance(self.end_offset, bool)
        ):
            raise ValueError(f"{self.section_id}: offsets must be plain integers")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError(
                f"{self.section_id}: end_offset must be greater than start_offset, "
                f"got {self.start_offset}..{self.end_offset}"
            )
        if not isinstance(self.text, str) or not self.text:
            raise ValueError(f"{self.section_id}: text must be a non-empty string")
        if len(self.text) != self.end_offset - self.start_offset:
            raise ValueError(
                f"{self.section_id}: text length {len(self.text)} does not match its "
                f"offset span {self.end_offset - self.start_offset}"
            )
        if self.kind is SectionKind.GLOBAL_DECLARATIONS:
            if self.name is not None:
                raise ValueError(f"{self.section_id}: a global_declarations section has no name")
            if self.signature is not None:
                raise ValueError(
                    f"{self.section_id}: a global_declarations section has no signature"
                )
        else:
            assert self.kind in _FUNCTION_KINDS  # exhaustive by SectionKind's own membership
            if not isinstance(self.name, str) or not self.name.strip():
                raise ValueError(f"{self.section_id}: a {self.kind.value} section needs a name")
            if not isinstance(self.signature, str) or not self.signature.strip():
                raise ValueError(
                    f"{self.section_id}: a {self.kind.value} section needs a signature"
                )


@dataclass(frozen=True)
class BuildDocument:
    """One source file's discovered structure. Produced only by the analyzer.

    Validated at construction, the same discipline `PanelPackage` and
    `BuildProject` already use: `sections` must be contiguous (each one
    starts exactly where the previous ended), start at offset 0, end at
    `len(source)`, and carry no duplicate `section_id` — together this makes
    the byte-for-byte reconstruction invariant a checked *fact* about every
    `BuildDocument` that exists, not merely something the analyzer is
    supposed to uphold.
    """

    source: str
    sections: tuple[CodeSection, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, str):
            raise ValueError("source must be a string")
        seen_ids: set[str] = set()
        cursor = 0
        for section in self.sections:
            if not isinstance(section, CodeSection):
                raise ValueError(f"not a CodeSection: {section!r}")
            if section.section_id in seen_ids:
                raise ValueError(f"duplicate section id: {section.section_id!r}")
            seen_ids.add(section.section_id)
            if section.start_offset != cursor:
                raise ValueError(
                    f"section {section.section_id!r} starts at {section.start_offset}, "
                    f"expected {cursor} — sections must be contiguous and in source order"
                )
            cursor = section.end_offset
        if cursor != len(self.source):
            raise ValueError(
                f"sections cover {cursor} of {len(self.source)} source characters; "
                "reconstruction would not be exact"
            )

    def render(self) -> str:
        """Every section's text, concatenated in order. Always equals `source`."""
        return "".join(section.text for section in self.sections)

    def section(self, section_id: str) -> CodeSection | None:
        """The section with this id, or None if this document has none."""
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    @property
    def setup(self) -> CodeSection | None:
        """The SETUP section, or None if this source declares no `setup()`."""
        return next((s for s in self.sections if s.kind is SectionKind.SETUP), None)

    @property
    def loop(self) -> CodeSection | None:
        """The LOOP section, or None if this source declares no `loop()`."""
        return next((s for s in self.sections if s.kind is SectionKind.LOOP), None)

    @property
    def functions(self) -> tuple[CodeSection, ...]:
        """Every SETUP/LOOP/CALLBACK/HELPER_FUNCTION section, in source order."""
        return tuple(s for s in self.sections if s.kind is not SectionKind.GLOBAL_DECLARATIONS)

    def line_at(self, offset: int) -> int:
        """The 1-indexed source line containing `offset` — diagnostics only.

        Derived from `source` on every call rather than cached on a section,
        so it can never disagree with the offsets that are this model's
        actual authority (see the `CodeSection` docstring).
        """
        if not 0 <= offset <= len(self.source):
            raise ValueError(f"offset {offset} is outside the source (length {len(self.source)})")
        return self.source.count("\n", 0, offset) + 1
