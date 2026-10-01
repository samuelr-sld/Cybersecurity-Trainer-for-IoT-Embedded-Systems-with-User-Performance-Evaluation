"""The semantic-IR -> BuildProject source boundary (Phase B7).

    SemanticProgram                  (B3/B5 — app/build/semantic/)
              |
              v
    generate_cpp(program)            B6 — app/build/semantic/generator.py
              |
              v
    analyze_source(generated)        B1 — app/build/discovery/
              |
              v
    firmware_file_from_document()    B2 — app/build/document_project.py
              |
              v
    FirmwareFile -> BuildProject -> BuildWorkspace.materialize -> compiler.py

WHY THIS EXISTS. B6 produces a whole *file* of C++; the Build workspace holds
a file as an ordered list of region-addressed segments. Something has to turn
one into the other, and B7's rule is that it happens HERE — at the
project/source boundary — and not inside the compiler. `app/build/compiler.py`
stays a generic Arduino compiler that is handed a materialized sketch
directory: it has never heard of Blockly, a `SemanticProgram`, or this module,
and B7 does not introduce that knowledge.

ONE SOURCE OF TRUTH, AND IT IS THE PROJECT. An applied program is written
back into the `BuildProject` immediately (see
`app/build/workspace.py::BuildWorkspace.apply_program`), not stashed beside it
to be rendered later. There is therefore no second, hidden representation of
"the real source" that a compile could disagree with: after an apply, the
project's own files ARE the generated source, `BuildWorkspace.fingerprint()`
moves with them, and the existing compile-to-flash integrity check keeps
working unchanged. Compiling stale pre-edit source is structurally impossible
because that text no longer exists anywhere.

REGENERATION MUST NOT MOVE A LOCKED REGION. Applying a program replaces a
whole file, which is a wider mutation than `update_region`'s one-segment
replacement, so the region model is re-proved here rather than bypassed:
`apply_program_to_file` refuses unless the submitted program's regenerated
LOCKED sections are byte-identical to the current source's own regenerated
LOCKED sections, and unless the set of section ids is exactly unchanged. An
edit reaching a locked span — or adding, removing or renaming a function — is
a rejection, never a silent success.

THE COMPARISON IS LIKE WITH LIKE, DELIBERATELY. The locked check compares
against a REGENERATION of the current source, not against the current source
itself. B6's layout is its own (a conventional two-space body indent, one
blank line between sections), so a first pass over shipped firmware can differ
from the original in whitespace while carrying exactly the same statements —
see `app/build/semantic/generator.py`, and note that a second pass is a
fixpoint. Comparing raw original text to generated text would report that
formatting difference as "a locked region changed", which is false; comparing
two generations of the same subset reports only a real change. What the check
therefore guarantees is that a submitted program says the same thing about
every locked section as the file it is derived from.

AN EDIT MOVES ONLY WHAT IT EDITED. B6 lays out a whole file its own way, so
the regenerated file is never handed back as-is: `_preserving_untouched_layout`
keeps a section's ORIGINAL text byte for byte whenever B6 writes it exactly as
it writes the unedited file, and gives a changed section its original leading
and trailing gap. Only the interior of a section a student actually changed is
B6's layout.

PURE AND STDLIB-ONLY. Functions in, values out. No filesystem, no process, no
`arduino-cli`, no session, no panel, no hardware, no clock, no randomness —
the same discipline B1/B3/B6 hold themselves to. The one thing this module
does is convert; deciding to convert belongs to the workspace, and deciding to
compile belongs to `app/build/service.py`.
"""

from __future__ import annotations

from dataclasses import replace

from app.build.discovery import DiscoveryError, analyze_source
from app.build.document_project import (
    DocumentProjectError,
    firmware_file_from_document,
    section_region_id,
)
from app.build.models import FirmwareFile, RegionKind
from app.build.semantic import (
    SemanticError,
    SemanticProgram,
    analyze_document,
    generate_cpp,
)


class ProgramSourceError(ValueError):
    """A semantic program could not become this file's source."""


class LockedRegionChangedError(ProgramSourceError):
    """The submitted program would change a region the student may not edit.

    Deliberately its own type, and deliberately a refusal rather than a
    filtered-out change: a caller that asked to rewrite locked firmware gets
    told so, exactly as `RegionNotEditableError` tells an `update_region`
    caller. Silently keeping the old locked text and applying the rest would
    produce firmware that is neither what was submitted nor what was there.
    """


class ProgramStructureChangedError(ProgramSourceError):
    """The submitted program adds, removes or renames a top-level section."""


class UnstructuredFileError(ProgramSourceError):
    """This file's regions are not the ones B1 discovers in its own source.

    A semantic program addresses a file by its DISCOVERED structure — the
    `section_id`s B1 derives from the C++ itself, which B2 reuses verbatim as
    region ids (`app/build/document_project.py`). The two hand-authored
    projects (`app/build/blink.py`, `app/build/environmental.py`) predate B1
    and name their regions by hand (`blink_program`, `security_logic`), so
    there is no correspondence for a program to write back through.

    This is a refusal rather than a conversion on purpose: re-sectioning such
    a project would silently redraw its locked/editable boundaries, which is
    a permission decision no phase has made for it. Projects materialized
    from a panel package's real firmware — the ones a student actually
    remediates — are discovered structurally and are unaffected.
    """


def program_for_source(source: str) -> SemanticProgram:
    """Read one file's current C++ as the semantic IR.

    The B1 -> B3 half of the loop, in one call, so a caller that holds a
    rendered file does not have to know which module does which step. Raises
    `ProgramSourceError` for source neither layer can represent — which for
    B3 means only unbalanced/undiscoverable structure, since an unrecognized
    construct is carried verbatim rather than refused.
    """
    if not isinstance(source, str):
        raise ProgramSourceError(f"source must be text, got {type(source).__name__}")
    try:
        return analyze_document(analyze_source(source))
    except (DiscoveryError, SemanticError) as error:
        raise ProgramSourceError(f"source could not be read as a program: {error}") from error


def source_for_program(program: SemanticProgram) -> str:
    """Write one semantic program as Arduino C++ — B6, named at this seam."""
    try:
        return generate_cpp(program)
    except SemanticError as error:
        raise ProgramSourceError(f"program could not be written as C++: {error}") from error


def _sectioned(source: str, path: str, editable_ids: frozenset[str]) -> FirmwareFile:
    """Generated source, re-discovered and re-addressed as regions."""
    try:
        document = analyze_source(source)
    except DiscoveryError as error:
        raise ProgramSourceError(
            f"{path}: generated source could not be structurally analysed: {error}"
        ) from error
    known = {section_region_id(section.section_id) for section in document.sections}
    try:
        return firmware_file_from_document(
            document, path, editable_section_ids=editable_ids & known
        )
    except DocumentProjectError as error:
        raise ProgramSourceError(f"{path}: {error}") from error


def _editable_ids(firmware_file: FirmwareFile) -> frozenset[str]:
    return frozenset(
        segment.region_id
        for segment in firmware_file.segments
        if segment.kind is RegionKind.EDITABLE
    )


def _locked_text(firmware_file: FirmwareFile) -> dict[str, str]:
    return {
        segment.region_id: segment.text
        for segment in firmware_file.segments
        if segment.kind is RegionKind.LOCKED
    }


def regenerated(firmware_file: FirmwareFile) -> FirmwareFile:
    """This file, round-tripped through the IR and back, changing nothing else.

    The baseline the locked-region check compares against (see the module
    docstring), and useful on its own as the answer to "what does this file
    look like once B6 owns its layout?". Region kinds and ids are carried
    over; only the text can move, and only by B6's own conventional layout.
    """
    return apply_program_to_file(
        firmware_file, program_for_source(firmware_file.render()), _skip_locked_check=True
    )


def apply_program_to_file(
    firmware_file: FirmwareFile,
    program: SemanticProgram,
    *,
    _skip_locked_check: bool = False,
) -> FirmwareFile:
    """One file, rewritten from a semantic program it must still agree with.

    Returns a NEW `FirmwareFile` — nothing here mutates the one it is given,
    so a rejected apply leaves the caller's workspace exactly as it was.

    Raises `ProgramStructureChangedError` if the program's sections are not
    the same set as the file's, and `LockedRegionChangedError` if any LOCKED
    region would end up saying something different from what the current
    source says. `_skip_locked_check` exists for `regenerated()` alone, which
    IS the baseline and so cannot be compared against itself; it is private
    and no other caller may pass it.
    """
    if not isinstance(firmware_file, FirmwareFile):
        raise ProgramSourceError(
            f"expected a FirmwareFile, got {type(firmware_file).__name__}"
        )
    path = firmware_file.path
    current_ids = [segment.region_id for segment in firmware_file.segments]
    discovered = _sectioned(firmware_file.render(), path, frozenset())
    discovered_ids = [segment.region_id for segment in discovered.segments]
    if current_ids != discovered_ids:
        raise UnstructuredFileError(
            f"{path}: this project's regions are not the ones discovered in its own "
            f"source ({current_ids} vs {discovered_ids}), so a program cannot address them"
        )

    written = _sectioned(source_for_program(program), path, _editable_ids(firmware_file))
    written_ids = [segment.region_id for segment in written.segments]
    if current_ids != written_ids:
        raise ProgramStructureChangedError(
            f"{path}: a program may change what a region says, not which regions exist "
            f"(expected {current_ids}, got {written_ids})"
        )
    if _skip_locked_check:
        return written

    baseline_file = regenerated(firmware_file)
    baseline = _locked_text(baseline_file)
    changed = sorted(
        region_id
        for region_id, text in _locked_text(written).items()
        if baseline.get(region_id) != text
    )
    if changed:
        raise LockedRegionChangedError(
            f"{path}: region(s) are locked and cannot be changed: {', '.join(changed)}"
        )
    return _preserving_untouched_layout(firmware_file, baseline_file, written)


def _gaps(text: str) -> tuple[str, str]:
    """The whitespace a segment starts and ends with (its gap to its neighbours)."""
    body = text.strip()
    if not body:
        return text, ""
    start = text.index(body)
    return text[:start], text[start + len(body):]


def _preserving_untouched_layout(
    original: FirmwareFile, baseline: FirmwareFile, written: FirmwareFile
) -> FirmwareFile:
    """`written`, with every section the program did not change left as it was.

    B6 lays a whole file out in its own conventional style, so taking `written`
    as-is re-lays-out every section — losing interior blank lines and
    normalising the gaps between sections — even when a student edited one.
    A section is UNTOUCHED when B6 writes it exactly as it writes the
    unedited file (`baseline`, the same like-with-like comparison the locked
    check uses); such a section keeps its ORIGINAL text byte for byte. A
    section that did change takes B6's new body but keeps its original leading
    and trailing gap, so an edit moves nothing but the edited statements.

    If the composed file would not re-discover as the same regions, this falls
    back to `written` rather than hand back a file whose sections moved.
    """
    composed: list = []
    for old, base, new in zip(original.segments, baseline.segments, written.segments):
        if new.text == base.text:
            composed.append(old)
            continue
        lead, trail = _gaps(old.text)
        composed.append(replace(new, text=lead + new.text.strip() + trail))
    candidate = replace(written, segments=tuple(composed))
    try:
        rediscovered = _sectioned(candidate.render(), written.path, frozenset())
    except ProgramSourceError:
        return written
    if [s.region_id for s in rediscovered.segments] != [s.region_id for s in written.segments]:
        return written
    return candidate
