"""A real sketch directory as a `BuildProject` (Phase B2).

    <sketch dir>/<name>.ino      on disk, shipped inside a panel package
              |
              v
    analyze_source()             B1 structural discovery
              |
              v
    build_project_from_document()  app/build/document_project.py — GAP A
              |
              v
    BuildProject                 the existing model, unchanged

THE ONE STEP THAT READS. `app/build/document_project.py` is pure conversion
and `app/build/discovery/` parses a string it is handed; this module is the
only piece of the B2 chain that opens a file. It reads text and nothing else
— it does not compile, flash, execute, import, or evaluate what it reads, and
it spawns no process (`app/build/process.py` remains the one module in the
backend allowed to do that, and neither it nor `compiler.py`/`flasher.py` is
imported here).

IT DOES NOT KNOW WHAT A PANEL IS. The argument is a `Path` to a directory and
a set of identity/board values, never a `PanelPackage`, a panel id, a MAC or a
resource root — so `app/build/` takes no dependency on `app/panels/`, and this
function is equally usable for any sketch a later phase wants to materialize.
`app/build_project_selection.py` is the composition layer that knows a panel
exists and supplies these arguments off a package the panel-resolution chain
already loaded and already validated.

THE PATH IS NEVER CLIENT-SUPPLIED. The only caller derives it from
`PanelPackage.firmware_sketch_path`, which `app/panels/loader.py` has already
proved is a relative, non-traversing reference contained inside the trusted
package root, and whose directory it has already confirmed exists and holds a
`.ino`. This module re-checks containment-independent facts it needs (a
readable directory, a primary sketch, decodable UTF-8) and reports them as
`SketchSourceError` rather than assuming them.

LINE ENDINGS. Files are read through Python's universal-newline translation,
so a CRLF file becomes `\\n`-delimited text. The B1 reconstruction invariant is
between the discovered document and the segments built from it — the workspace
holds exactly the text that was analysed, and `BuildWorkspace.materialize`
writes it back out through the platform's own convention, exactly as it
already does for the hand-authored projects.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.build.discovery import DiscoveryError, analyze_source
from app.build.document_project import (
    DocumentProjectError,
    build_project_from_document,
    locked_file,
)
from app.build.models import BoardInfo, BuildProject

#: Source files carried into the materialized sketch alongside the primary
#: `.ino`. Deliberately a closed list of C/C++ translation-unit and header
#: extensions: everything here is compiler input, and a sketch folder's other
#: contents (build leftovers, notes, editor droppings) are not this project's
#: source and must not silently become part of a student's firmware.
SUPPORTING_SUFFIXES = (".h", ".hpp", ".hh", ".c", ".cpp", ".cc")


class SketchSourceError(ValueError):
    """A sketch directory could not be read as a `BuildProject`."""


def find_primary_sketch(sketch_dir: Path) -> Path:
    """The `.ino` that is this sketch's main translation unit.

    The Arduino toolchain requires a sketch folder to share its primary
    `.ino`'s base name, so that rule — not alphabetical order, not "the first
    one found" — is what selects it. A folder holding exactly one `.ino` is
    accepted whatever it is called, since that case is unambiguous; anything
    else is refused rather than guessed, because picking the wrong entry file
    would compile a different program than the panel ships.
    """
    if not sketch_dir.is_dir():
        raise SketchSourceError(f"sketch source is not a directory: {sketch_dir}")
    sketches = sorted(p for p in sketch_dir.iterdir() if p.is_file() and p.suffix == ".ino")
    if not sketches:
        raise SketchSourceError(f"sketch directory contains no .ino file: {sketch_dir}")
    for sketch in sketches:
        if sketch.stem == sketch_dir.name:
            return sketch
    if len(sketches) == 1:
        return sketches[0]
    names = ", ".join(p.name for p in sketches)
    raise SketchSourceError(
        f"sketch directory {sketch_dir} has several .ino files ({names}) and none is "
        f"named after the folder; refusing to guess the entry file"
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SketchSourceError(f"{path} is not valid UTF-8 text: {error}") from error
    except OSError as error:
        raise SketchSourceError(f"{path} could not be read: {error}") from error


def load_sketch_project(
    sketch_dir: Path,
    *,
    project_id: str,
    scenario_id: str,
    module_id: str,
    firmware_name: str,
    board: BoardInfo,
    editable_section_ids: Iterable[str] = (),
    explore_section_ids: Iterable[str] = (),
    security_region_id: str | None = None,
) -> BuildProject:
    """Read one sketch directory into a fresh, independent `BuildProject`.

    One call, one project — the same per-session isolation guarantee
    `create_blink_project()` documents: two Build Mode sessions on the same
    panel get two independent projects and cannot observe each other's edits.

    Raises `SketchSourceError` for anything wrong with the source on disk
    (missing directory, no entry sketch, undecodable text, C++ the structural
    analyzer cannot balance). The caller decides whether that is fatal or a
    reason to fall back; see `app/build_project_selection.py`, which treats it
    as a selection rather than an exception.
    """
    primary = find_primary_sketch(sketch_dir)
    source = _read_text(primary)
    try:
        document = analyze_source(source)
    except DiscoveryError as error:
        raise SketchSourceError(f"{primary} could not be structurally analysed: {error}") from error

    supporting = tuple(
        locked_file(path.name, _read_text(path))
        for path in sorted(sketch_dir.iterdir())
        if path.is_file() and path.suffix in SUPPORTING_SUFFIXES
    )

    try:
        return build_project_from_document(
            document,
            path=primary.name,
            project_id=project_id,
            scenario_id=scenario_id,
            module_id=module_id,
            firmware_name=firmware_name,
            board=board,
            editable_section_ids=editable_section_ids,
            explore_section_ids=explore_section_ids,
            security_region_id=security_region_id,
            supporting_files=supporting,
        )
    except DocumentProjectError as error:
        raise SketchSourceError(f"{primary}: {error}") from error
