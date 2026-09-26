"""The SECTION -> Blockly -> section boundary (Phase B8 correction).

    BuildProject file source
          |
          v  B1 discovery + B3 analysis         (app/build/program_source.py)
    SemanticProgram
          |
          v  section_blockly_for(program, id)   (this module)
    BlocklySection -> to_representation()       one section, one workspace
          |
          v  STUDENT EDITS BLOCKLY
          |
          v  blockly_section_from_state()       (blockly_bridge/workspace_state.py)
    BlocklySection
          |
          v  program_with_section(program, ...) (this module)
    SemanticProgram
          |
          v  B6 + reconstruction                (app/build/program_source.py)
    FirmwareFile -> compile -> flash -> validate

WHY THIS MODULE EXISTS. `program_source.py` is the whole-FILE boundary: a
program in, a file out. Build Mode's interaction unit is not a file, it is a
SECTION — a student clicks one discovered construct and that construct opens in
Blockly. Something has to narrow a file's program to one section on the way out
and widen one section back into the file's program on the way in, and doing it
here keeps `program_source.py` unaware that an editor exists and keeps the
bridge unaware that a file does.

THE SECTION ID IS THE IDENTITY, ON BOTH LEGS. The same string throughout:
B1's `CodeSection.section_id`, B2's `FileSegment.region_id`, B3's
`SemanticSection.section_id`, B4's `BlocklySection.section_id`. A caller names
a section by that id and never by a display name, a C++ function name, or a
position — see `app/build/policy.py`, which addresses permissions the same way.

EVERY OTHER SECTION IS CARRIED THROUGH UNTOUCHED, BY CONSTRUCTION.
`program_with_section` replaces exactly one element of `program.sections` and
copies the rest by reference. It cannot reorder, add or drop a section, so the
locked-region and structure checks `apply_program_to_file` then applies have
nothing to catch from this module — they stay as the independent second proof
they were, rather than as this module's only safety net.

A SECTION BLOCKLY CANNOT DRAW IS NOT AN EDITABLE SECTION. `program_with_section`
REFUSES a submission for a section with no container form — today every helper
function and every run of global declarations, including Panel 1's own
`helper_applyCommand` (see `SectionNotRepresentableError`). Such a section's
whole body is one preserved fragment, so "editing it as blocks" could only mean
returning changed C++ text through a Blockly-shaped frame, which is the C++
text editor this architecture does not want, wearing the wrong hat. The honest
answer is that the toolbox has no vocabulary for the construct YET, and saying
so is what makes the missing vocabulary visible instead of silently routing
around it. The existing `edit_region` text path is untouched and still works;
what is refused here is only the pretence that a text edit is a block edit.

THE BLOCK CATALOG IS NOT IMPORTED HERE, AND THAT IS A RULE RATHER THAN AN
OMISSION. `app/build/blockly_bridge/` is the ONE package allowed to import both
`app.blockly` and `app.build.semantic` (`tests/test_build_blockly_bridge.py`
asserts it over the whole tree), because the bridge is exactly the place those
two vocabularies are allowed to meet. This module composes the bridge with a
file's program; it never needs to name a catalog, so it takes no `catalog`
argument and lets the bridge's own defaults apply. A test wanting a different
catalog injects it into the bridge functions directly, where the parameter
already exists.

PURE AND STDLIB-ONLY. Functions in, values out. No filesystem, no process, no
session, no panel, no hardware, no clock — the same discipline B1/B3/B6 and
`program_source.py` hold. Nothing here decides whether a student MAY edit a
section: that is the `InteractionPolicy` on the project, checked by
`app/build/workspace.py` before this module is ever called.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from app.build.blockly_bridge import (
    BlocklyBridgeError,
    BlocklyProgram,
    BlocklySection,
    blockly_section_from_state,
    blockly_to_semantic,
    program_to_blockly,
)
from app.build.semantic import SemanticError, SemanticProgram, SemanticSection


class SectionBlocklyError(ValueError):
    """A section could not be represented in Blockly, or read back from it."""


class SectionNotFoundError(SectionBlocklyError):
    """No section of this program carries the requested id."""


class SectionNotRepresentableError(SectionBlocklyError):
    """Blockly has no vocabulary for this construct yet — see the module docstring.

    Deliberately its own type and deliberately a REFUSAL. The alternative —
    accepting the submission and writing its preserved text back — would make
    the Blockly frame a channel for hand-written C++, which is precisely the
    editing model this phase exists to keep out. A caller shows the section
    read-only and reports that the toolbox cannot yet express it.
    """


def section_blockly_for(
    program: SemanticProgram,
    section_id: str,
) -> BlocklySection:
    """One section of `program`, represented in Blockly.

    The whole program is converted and one section taken, rather than the
    section being converted alone, so a section's representation is
    byte-identical whether it was asked for on its own or read out of a full
    `program_to_blockly` result. B4 is a pure function of its input and this
    keeps exactly one path through it.

    Raises `SectionNotFoundError` for an id this program does not have, and
    `SectionBlocklyError` if the platform's own tables disagree about how to
    draw it (see `blockly_bridge/errors.py` — that is drift, not firmware).
    """
    if program.section(section_id) is None:
        raise SectionNotFoundError(
            f"no such section in this program: {section_id}"
        )
    try:
        represented = program_to_blockly(program)
    except BlocklyBridgeError as error:
        raise SectionBlocklyError(f"{section_id}: {error}") from error
    section = represented.section(section_id)
    assert section is not None  # B4 preserves section ids one for one
    return section


def section_representation(
    program: SemanticProgram,
    section_id: str,
) -> dict[str, Any]:
    """One section as the JSON-safe representation an editor loads.

    `{"sectionId", "representable", "workspace", "preserved"}` — see
    `BlocklySection.to_representation`. It carries no permission field: what a
    student may DO with the section is the `InteractionPolicy` the `state`
    frame already reports per segment, and duplicating it here would create a
    second answer to one question.
    """
    return section_blockly_for(program, section_id).to_representation()


def section_from_state(
    section_id: str,
    workspace: Mapping[str, Any],
    preserved: Iterable[Mapping[str, Any]] = (),
) -> BlocklySection:
    """An editor's Blockly JSON for one section, read back as a `BlocklySection`.

    A thin, named re-export of `blockly_section_from_state` so a caller on this
    seam imports one module for both legs of the round trip. Raises
    `SectionBlocklyError` for a malformed workspace.
    """
    try:
        return blockly_section_from_state(section_id, workspace, preserved)
    except BlocklyBridgeError as error:
        raise SectionBlocklyError(str(error)) from error


def program_with_section(
    program: SemanticProgram,
    section: BlocklySection,
) -> SemanticProgram:
    """`program`, with one section replaced by what the editor sent back.

    The section is identified by `section.section_id`, which must already be
    one this program has — a Blockly submission may change what a section SAYS,
    never which sections exist. Every other section is the same object it was.

    Raises `SectionNotFoundError` for an unknown id,
    `SectionNotRepresentableError` for a construct Blockly cannot draw (see the
    module docstring), and `SectionBlocklyError` for a workspace whose blocks
    contradict the catalog or whose fields cannot hold the values they carry.
    """
    if not isinstance(program, SemanticProgram):
        raise SectionBlocklyError(f"expected a SemanticProgram, got {type(program).__name__}")
    if not isinstance(section, BlocklySection):
        raise SectionBlocklyError(f"expected a BlocklySection, got {type(section).__name__}")

    section_id = section.section_id
    current = program.section(section_id)
    if current is None:
        raise SectionNotFoundError(f"no such section in this program: {section_id}")
    if current.operation is None:
        # The construct has no container form, so there is no block to have
        # edited — see the module docstring. Checked against the CURRENT
        # program rather than against the submission, so an empty workspace
        # cannot be used to claim the section was representable and then
        # emptied.
        raise SectionNotRepresentableError(
            f"{section_id}: this construct has no Blockly representation yet, so it "
            "cannot be edited as blocks"
        )
    if section.block is None:
        raise SectionNotRepresentableError(
            f"{section_id}: the submitted workspace has no block for a section that "
            "has one; a section's construct cannot be removed by editing it"
        )

    replacement = _semantic_section(section)
    if replacement.operation_id != current.operation_id:
        # The container operation IS the construct: `program.setup` becoming
        # `program.loop` would rename the function, which `apply_program_to_file`
        # would then reject as a changed structure. Refusing here says why.
        raise SectionBlocklyError(
            f"{section_id}: a section's construct is fixed — this workspace turns "
            f"{current.operation_id} into {replacement.operation_id}"
        )
    if current.signature is not None:
        # A generic named-function container's PRESERVED C++ declarator
        # (`SemanticSection.signature`) is fixed scaffolding, exactly like the
        # container operation itself just above — Blockly never carries it
        # (B4/B5 know nothing of it), so `_semantic_section` always rebuilds
        # the replacement with `signature=None`. Copying the CURRENT section's
        # signature across is what keeps a function's own declarator from
        # silently disappearing the moment a student edits that section.
        replacement = replace(replacement, signature=current.signature)
    return SemanticProgram(
        sections=tuple(
            replacement if existing.section_id == section_id else existing
            for existing in program.sections
        )
    )


def _semantic_section(section: BlocklySection) -> SemanticSection:
    """What one submitted section MEANS, through B5 and nothing else.

    B5 consumes a `BlocklyProgram`, so the section is wrapped in a one-section
    program and unwrapped again. Reusing `blockly_to_semantic` rather than
    reaching into its internals is what keeps one implementation of "what does
    this workspace mean", shared by the whole-file and section-scoped paths.
    """
    try:
        converted = blockly_to_semantic(BlocklyProgram(sections=(section,)))
    except (BlocklyBridgeError, SemanticError) as error:
        raise SectionBlocklyError(f"{section.section_id}: {error}") from error
    return converted.sections[0]
