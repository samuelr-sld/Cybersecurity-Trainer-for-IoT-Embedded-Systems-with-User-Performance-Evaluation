"""The Build Workspace — enforces locked vs. editable firmware regions.

This is the boundary the whole phase is built around:

    Build WebSocket -> Build Service -> Build Workspace -> BuildProject

A `BuildWorkspace` wraps one `BuildProject` and is the *only* thing allowed
to change its files. Its entire public surface for mutation is
`update_region(path, region_id, source)`, which is addressed by region id,
never by a raw file replacement — a caller cannot submit a whole file and
have it accepted, so there is no locked text for a hostile payload to smuggle
changes into. Submitting an id that does not exist, or that names a LOCKED
segment, raises rather than silently doing nothing: the protection is a
rejection, not a no-op that would look like success to the frontend.

This module deliberately does not parse or understand C++. It only ever
replaces one segment's `text` wholesale with whatever string the student
submitted — syntax errors, logic errors, and empty regions are all valid
values here, because a student must be able to make real mistakes inside
the security region (see `app/build/environmental.py`). Region protection
does not require a compiler; it only requires knowing which named span of
text a caller is allowed to touch.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from app.build.models import BuildProject, FileSegment, FirmwareFile, RegionKind


class BuildWorkspaceError(ValueError):
    """Base for every rejected Build Workspace operation."""


class ProjectFileNotFoundError(BuildWorkspaceError):
    """The requested file path does not exist in this project."""


class RegionNotFoundError(BuildWorkspaceError):
    """No segment in the file carries this region id."""


class RegionNotEditableError(BuildWorkspaceError):
    """The region id names a real segment, but it is LOCKED."""


class BuildWorkspace:
    """The live, per-session state of one Build Mode firmware project."""

    def __init__(self, project: BuildProject) -> None:
        self._project = project

    @property
    def project(self) -> BuildProject:
        """The current project — files reflect every edit applied so far."""
        return self._project

    def _file(self, path: str) -> FirmwareFile:
        firmware_file = self._project.file(path)
        if firmware_file is None:
            raise ProjectFileNotFoundError(f"no such file in this project: {path}")
        return firmware_file

    def full_source(self, path: str) -> str:
        """The complete current source of one file, locked and editable."""
        return self._file(path).render()

    def region_source(self, path: str, region_id: str) -> str:
        """The current source of one region — for the Code View editor pane."""
        return self._segment(path, region_id).text

    def _segment(self, path: str, region_id: str) -> FileSegment:
        segment = self._file(path).segment(region_id)
        if segment is None:
            raise RegionNotFoundError(
                f"no such region in {path}: {region_id}"
            )
        return segment

    def update_region(self, path: str, region_id: str, source: str) -> None:
        """Replace one EDITABLE region's source. Mutates this workspace.

        Raises `ProjectFileNotFoundError`, `RegionNotFoundError`, or
        `RegionNotEditableError` — never silently ignores a bad request —
        so a caller (see `app/build/service.py`) always knows whether the
        edit actually happened.
        """
        firmware_file = self._file(path)
        segment = self._segment(path, region_id)
        if segment.kind is not RegionKind.EDITABLE:
            raise RegionNotEditableError(
                f"region is locked and cannot be edited: {path}#{region_id}"
            )

        new_segments = tuple(
            replace(existing, text=source) if existing is segment else existing
            for existing in firmware_file.segments
        )
        new_file = replace(firmware_file, segments=new_segments)
        new_files = tuple(
            new_file if existing.path == path else existing
            for existing in self._project.files
        )
        self._project = replace(self._project, files=new_files)

    def materialize(self, root: Path) -> Path:
        """Write this project's current files into a fresh sketch directory.

        Phase 3B's compile step (`app/build/compiler.py`) never reads this
        workspace's live `BuildProject` directly — it only ever sees a
        filesystem copy this method just wrote. That is what keeps a
        compile from being able to mutate the canonical workspace: there is
        no path from the compiler process back into `self._project`.

        The Arduino toolchain requires a sketch's containing folder to share
        its primary `.ino` file's base name, so that folder name is derived
        from whichever file in this project ends in `.ino` — never
        hardcoded to `"main"` — and every other file (locked or not) is
        written alongside it, verbatim, exactly as this workspace currently
        renders it.
        """
        ino_files = [f for f in self._project.files if f.path.endswith(".ino")]
        if not ino_files:
            raise BuildWorkspaceError("project has no .ino entry file to materialize")
        sketch_name = ino_files[0].path[: -len(".ino")]
        sketch_dir = root / sketch_name
        sketch_dir.mkdir(parents=True, exist_ok=True)
        for firmware_file in self._project.files:
            (sketch_dir / firmware_file.path).write_text(
                firmware_file.render(), encoding="utf-8"
            )
        return sketch_dir

    def fingerprint(self) -> str:
        """A content hash of every file this workspace currently renders.

        Phase 3C's compile-to-flash integrity check (see
        `app/build/service.py` and `CompiledArtifact`): a successful compile
        records this value, and a flash is refused unless the workspace
        still hashes to the same thing. That makes "you are flashing what
        you compiled" a verified fact.

        Content-addressed on purpose. `BuildSession.dirty` answers a
        different question — "has the student changed anything from the
        original firmware?" — and would wrongly forbid flashing a
        legitimately remediated build, while a plain "edited since compile"
        flag would wrongly forbid flashing after an edit that was typed and
        then undone. Hashing the rendered text answers exactly the question
        that matters, and answers it the same way twice.
        """
        digest = hashlib.sha256()
        for firmware_file in sorted(self._project.files, key=lambda f: f.path):
            digest.update(firmware_file.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(firmware_file.render().encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def snapshot(self) -> dict:
        """A JSON-serialisable view of the project for the `state` frame.

        Every file is rendered as its ordered segment list — kind, region
        id, and current text — so the frontend can draw locked vs. editable
        code without reimplementing this module's region logic, and can
        reconstruct the full file by concatenation for a read-only full view.
        """
        project = self._project
        return {
            "project": {
                "project_id": project.project_id,
                "scenario_id": project.scenario_id,
                "module_id": project.module_id,
                "firmware_name": project.firmware_name,
                "board": {
                    "name": project.board.name,
                    "mcu": project.board.mcu,
                    "fqbn": project.board.fqbn,
                },
                "security_region_id": project.security_region_id,
            },
            "files": {
                firmware_file.path: {
                    "segments": [
                        {
                            "kind": segment.kind.value,
                            "region_id": segment.region_id,
                            "text": segment.text,
                        }
                        for segment in firmware_file.segments
                    ]
                }
                for firmware_file in project.files
            },
        }
