"""Discovered structure -> the existing Build workspace model (Phase B2).

This module is GAP A of B2, and nothing else:

    BuildDocument / CodeSection[]        (app/build/discovery/ — B1)
              |
              v
    firmware_file_from_document()        (this module)
              |
              v
    FirmwareFile / FileSegment           (app/build/models.py — unchanged)
              |
              v
    BuildProject -> BuildWorkspace -> BuildSession -> compile / flash

TWO ORTHOGONAL MODELS, JOINED HERE AND ONLY HERE. `app/build/discovery/
models.py` states the split this module respects: a `CodeSection` answers a
*structural* question ("what kind of C++ construct is this, and what is it
called?"), a `FileSegment` answers a *permission* question ("may a student
edit this span?"). Neither was extended to learn the other's field — no
`kind`/`signature`/offset reaches `FileSegment`, no locked/editable reaches
`CodeSection` — because the moment one of them carries both, every existing
consumer of the already-tested region model has to understand fields it does
not need. The conversion is explicit, lives in exactly one function, and is
the only place the two vocabularies meet.

REGION ID == SECTION ID. The stable mapping between the two models is not a
side table: a `CodeSection.section_id` (`setup`, `loop`, `callback_onMessage`,
`helper_applyCommand`, `global_3`) is already unique within its document, is
already derived from the construct's final kind and name rather than from its
position, and is already what B1's own tests pin. Reusing it verbatim as the
`FileSegment.region_id` means an edit addressed as `(path, region_id)` — the
only addressing the Build workspace has ever accepted — names the discovered
construct directly, with no translation layer to drift.

THIS MODULE INVENTS NO PERMISSION MODEL. Every section becomes a LOCKED
segment unless its id appears in the caller's explicit `editable_section_ids`.
That default is not a policy about C++; it is the absence of one. B1 discovers
structure and cannot know what a student is supposed to change, and B2 is
integration rather than editing capability, so the conservative existing
`RegionKind.LOCKED` is what an undecided span gets. `editable_section_ids` is
the seam the later remediation phase passes a decision through — it changes no
type here and adds no new vocabulary.

RECONSTRUCTION SURVIVES THE CONVERSION. B1 guarantees
`BuildDocument.render() == source`; `FirmwareFile.render()` concatenates
segment text in order exactly as `BuildDocument.render()` concatenates section
text in order, so the rendered file a compiler sees is byte-for-byte the
source that was discovered. `firmware_file_from_document` re-checks that
rather than assuming it, so a future change on either side fails loudly here
instead of silently materializing corrupted firmware.

NO FILESYSTEM, NO PANELS, NO SESSIONS. This module reads nothing and knows
nothing about a panel package, a MAC, a sketch directory or a session — see
`app/build/sketch_source.py` for the one step that touches disk and
`app/build_project_selection.py` for the one that knows a panel exists.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.build.discovery.models import BuildDocument
from app.build.models import BoardInfo, BuildProject, FileSegment, FirmwareFile, RegionKind


class DocumentProjectError(ValueError):
    """A `BuildDocument` could not be expressed as a `FirmwareFile`."""


#: The `region_id` a whole-file segment carries — see `locked_file` below.
#: Region ids are unique per *file*, not per project, so one constant is
#: enough and it can never collide with a discovered section id (every one of
#: those carries a kind prefix: `setup`, `loop`, `global`, `helper_…`,
#: `callback_…`).
WHOLE_FILE_REGION_ID = "file"


def section_region_id(section_id: str) -> str:
    """The `FileSegment.region_id` a discovered section maps to.

    Identity today — see REGION ID == SECTION ID above. It exists as a named
    function anyway so that every caller goes through one place if that ever
    has to become a real translation, rather than each one hardcoding the
    assumption that the two ids are spelled the same.
    """
    return section_id


def firmware_file_from_document(
    document: BuildDocument,
    path: str,
    *,
    editable_section_ids: Iterable[str] = (),
) -> FirmwareFile:
    """One discovered document as one ordered, region-addressed file.

    Section order is preserved exactly; every section becomes exactly one
    segment; nothing is merged, split, reordered or reformatted.

    Raises `DocumentProjectError` if `editable_section_ids` names a section
    this document does not have — a caller asking for a region that does not
    exist is a mistake worth failing on, not a silently-ignored request, the
    same discipline `BuildWorkspace.update_region` already applies to an
    unknown region id.
    """
    if not isinstance(path, str) or not path.strip():
        raise DocumentProjectError(f"firmware file path must be a non-empty string, got {path!r}")
    if not document.sections:
        raise DocumentProjectError(f"{path}: document has no sections to materialize")

    editable = set(editable_section_ids)
    known = {section.section_id for section in document.sections}
    unknown = sorted(editable - known)
    if unknown:
        raise DocumentProjectError(
            f"{path}: no such section(s) to make editable: {', '.join(unknown)}"
        )

    segments = tuple(
        FileSegment(
            kind=RegionKind.EDITABLE if section.section_id in editable else RegionKind.LOCKED,
            region_id=section_region_id(section.section_id),
            text=section.text,
        )
        for section in document.sections
    )
    firmware_file = FirmwareFile(path=path, segments=segments)
    if firmware_file.render() != document.source:
        # Unreachable while both models concatenate in order; kept because
        # the alternative to failing here is compiling firmware that is not
        # the source anybody discovered or reviewed.
        raise DocumentProjectError(
            f"{path}: converted segments do not reconstruct the discovered source"
        )
    return firmware_file


def locked_file(path: str, source: str) -> FirmwareFile:
    """A supporting sketch file as one whole-file LOCKED segment.

    B1 analyses one C++ translation unit — the sketch's primary `.ino`. A
    sketch may legitimately ship other sources beside it (a `.h`, a `.cpp`),
    and those still have to reach the compiler verbatim or the build is not
    the panel's firmware. Representing them as a single locked segment is the
    honest minimum: undiscovered, uneditable, unmodified, and carried through
    `materialize` like every other file.
    """
    if not isinstance(source, str) or not source:
        raise DocumentProjectError(f"{path}: supporting file source must be non-empty text")
    return FirmwareFile(
        path=path,
        segments=(FileSegment(RegionKind.LOCKED, WHOLE_FILE_REGION_ID, source),),
    )


def board_info_from_fqbn(fqbn: str) -> BoardInfo:
    """A `BoardInfo` derived from an Arduino CLI board target.

    `FirmwareConfiguration.board` (`app/hardware/firmware.py`) carries only
    the FQBN, because for arduino-cli that one string *is* the complete board
    configuration — options included. `BoardInfo` additionally carries a
    `name` and an `mcu`, which are display/fallback labels only (the header's
    board fallback and the project card). Those are derived from the FQBN's
    own `vendor:arch:board` segments rather than invented or looked up in a
    table: `esp32:esp32:esp32` gives name `ESP32`, mcu `ESP32`, and the
    authoritative `fqbn` is passed through untouched — `app/build/compiler.py`
    and `flasher.py` read only that field.
    """
    parts = fqbn.split(":")
    if len(parts) < 3 or not all(parts[:3]):
        raise DocumentProjectError(f"fqbn must be vendor:arch:board[:options], got {fqbn!r}")
    return BoardInfo(name=parts[2].upper(), mcu=parts[1].upper(), fqbn=fqbn)


def build_project_from_document(
    document: BuildDocument,
    *,
    path: str,
    project_id: str,
    scenario_id: str,
    module_id: str,
    firmware_name: str,
    board: BoardInfo,
    editable_section_ids: Iterable[str] = (),
    security_region_id: str | None = None,
    supporting_files: Iterable[FirmwareFile] = (),
) -> BuildProject:
    """One discovered document as a complete, compilable `BuildProject`.

    The primary `.ino` is always `files[0]`, which is what
    `BuildWorkspace.materialize` derives the Arduino sketch folder name from;
    `supporting_files` follow it in the order given.

    Identity (`project_id`, `scenario_id`, `module_id`, `firmware_name`) and
    the board target are parameters rather than anything this module derives:
    they belong to whoever owns the source — see
    `app/build_project_selection.py`, which reads all of them off the resolved
    `PanelPackage` — and inventing them here would put a second source of
    truth next to the manifest.
    """
    primary = firmware_file_from_document(
        document, path, editable_section_ids=editable_section_ids
    )
    files = (primary, *supporting_files)
    paths = [f.path for f in files]
    if len(set(paths)) != len(paths):
        raise DocumentProjectError(f"duplicate file path in project {project_id!r}: {paths}")
    return BuildProject(
        project_id=project_id,
        scenario_id=scenario_id,
        module_id=module_id,
        firmware_name=firmware_name,
        board=board,
        files=files,
        security_region_id=security_region_id,
    )
