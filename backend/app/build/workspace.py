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

THREE WAYS IN, ONE RULE, AND ONLY ONE OF THEM IS THE INTENDED INTERFACE:

    update_region(path, region_id, source)          C++ text  (Phase 3A)
    apply_program(path, program)                    a file's IR (B7)
    apply_section_blockly(path, section_id, …)      BLOCKS    (B8) <- intended

Build Mode is a section-based BLOCKLY editor: a student clicks a discovered
section and that section opens as blocks. `apply_section_blockly` is therefore
the path the interface is built on, and generated C++ is an OUTPUT of it, never
the thing the student types. `update_region` remains — the Blink POC uses it
and the toolbox cannot yet draw every construct — but it is the legacy text
path, not the remediation interface. All three are addressed by a stable id and
all three are refused the same way when they reach a region the student may not
write.

PHASE B7 ADDS A SECOND WAY IN, AND IT OBEYS THE SAME RULE. `apply_program`
accepts an edit expressed as a `SemanticProgram` (what B5 builds from a
Blockly workspace) instead of as one region's text, writes B6's generated
C++ back into the project, and is refused — loudly, as an exception, exactly
like `update_region` — if the result would change a LOCKED region or the set
of regions the file has. The conversion itself is `app/build/
program_source.py`; what lives here is still only the decision to mutate.
THE PROJECT REMAINS THE SINGLE SOURCE OF TRUTH: an applied program becomes
the project's own file text immediately, so `fingerprint()` moves with it,
`materialize()` writes it, and the compiler cannot be handed pre-edit source
that no longer exists anywhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from app.build.blockly_bridge import BlocklySection
from app.build.models import BuildProject, FileSegment, FirmwareFile, RegionKind
from app.build.program_source import (
    ProgramSourceError,
    apply_program_to_file,
    program_for_source,
)
from app.build.section_blockly import (
    SectionBlocklyError,
    program_with_section,
    section_from_state,
    section_representation,
)
from app.build.semantic import SemanticProgram


class BuildWorkspaceError(ValueError):
    """Base for every rejected Build Workspace operation."""


class ProjectFileNotFoundError(BuildWorkspaceError):
    """The requested file path does not exist in this project."""


class RegionNotFoundError(BuildWorkspaceError):
    """No segment in the file carries this region id."""


class RegionNotEditableError(BuildWorkspaceError):
    """The region id names a real segment, but it is LOCKED."""


class ProgramApplyError(BuildWorkspaceError):
    """A semantic program could not become this workspace's source.

    Every `ProgramSourceError` from `app/build/program_source.py` — source
    that cannot be read or written, a changed set of sections, a changed
    LOCKED region — surfaces as this one workspace-level type, so
    `BuildService` keeps catching exactly `BuildWorkspaceError` and a
    rejected Blockly edit reaches the student the same way a rejected region
    edit does.
    """


class SecurityRegionOwnershipError(BuildWorkspaceError):
    """A Blockly edit to the security region still carries opaque C++.

    `BuildProject.security_region_id` names the one region a scenario's whole
    remediation lives in. A construct B3 never understood at all (an
    `UnsupportedStatement`, carried as a `PreservedSource` whose
    `understood_by_the_ir` is False — see `app/build/blockly_bridge/models.py`)
    can hide ANYTHING, including the very vulnerability the section exists to
    fix: the analyzer represents only the first `if` of an `if`/`else if`
    chain as a block (see `app/build/semantic/analyzer.py`'s
    `ConditionalStatement` docstring), so a firmware's original unauthenticated
    branch can sit right beside a student's newly authored, fully-authorized
    one and never appear on the canvas at all.

    So `apply_section_blockly` refuses ANY such fragment the moment the
    security region is edited as blocks — see
    `_opaque_preserved_records` — rather than silently splicing it back in
    beside whatever the student drew. A fragment the IR DID understand and
    only Blockly could not draw (`understood_by_the_ir` is True — a named
    constant in a numeric field, for instance) is unaffected: the IR knows
    exactly what it means, so keeping it is not the same risk.

    This is full ownership, not a ban on preserved material in general: every
    OTHER editable section keeps carrying arbitrary unsupported C++ through a
    Blockly edit exactly as before (see `test_build_pipeline_b8.py`'s "every
    other section is carried through untouched" guarantee) — only a project's
    designated `security_region_id` takes on this stricter rule, and it does
    so for whichever scenario names one, not only Panel 1's.

    `update_region`'s text path is untouched: it always replaces a region's
    WHOLE text, so there is no partial submission for anything to reattach to.
    """


def _opaque_preserved_texts(section: BlocklySection) -> tuple[str, ...]:
    """The section's preserved fragments the semantic layer never understood.

    `BlocklySection.records` locates every preserved item wherever it sits -
    in the container's body, inside a nested block's body at any depth (an
    `if`, a `for`, an `else`), or, for an unrepresentable section, beside it -
    so this needs no knowledge of which shape `section` is in, and a statement
    cannot hide from this rule by being one block deep.
    `understood_by_the_ir` is the one distinction that matters here (see
    `SecurityRegionOwnershipError`): False means B3 itself never parsed the
    fragment, so nothing downstream knows what it does.
    """
    return tuple(
        record.source.text
        for record in section.records
        if not record.source.understood_by_the_ir and not _is_comment_only(record.source.text)
    )


def _is_comment_only(text: str) -> bool:
    """True when `text` holds nothing but comments and whitespace.

    A comment is preserved source like any other the IR never read, but it is
    not C++: it has no behavior to hide. Counting it as opaque would make a
    security region containing ANY explanatory comment un-editable as blocks
    (a firmware's guard function often ends with one), which protects nothing - the rule
    exists to stop a statement the student cannot see from surviving beside
    their blocks. Only comments are removed before looking (a bare string
    literal is not a comment and stays opaque).
    """
    return _without_comments(text).strip() == ""


def _without_comments(text: str) -> str:
    """`text` with `//` and `/* */` comments removed, strings left intact."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("//", i):
            newline = text.find(chr(10), i)
            i = n if newline == -1 else newline
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif text[i] in "\"'":
            quote = text[i]
            j = i + 1
            while j < n and text[j] != quote:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _require_full_ownership(path: str, submitted: BlocklySection) -> None:
    """Refuse a security-region submission that still hides opaque C++.

    Raises `SecurityRegionOwnershipError` naming every offending fragment, so
    a rejection tells a student exactly what still needs to become blocks —
    rather than a bare "no" or, worse, silently keeping the fragment in place.
    """
    opaque = _opaque_preserved_texts(submitted)
    if not opaque:
        return
    listed = "; ".join(opaque)
    raise SecurityRegionOwnershipError(
        f"{path}#{submitted.section_id}: this is the security region, so editing it as "
        "blocks must take ownership of the whole function — it still carries source "
        f"Blockly cannot represent and cannot silently keep: {listed}"
    )


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

    def program(self, path: str) -> SemanticProgram:
        """This file's CURRENT source, read as the semantic IR (B1 -> B3).

        Read-only and derived on demand — the program is not cached, stored
        on the workspace, or kept in sync with the files, because a cached
        copy would be the second source of truth this phase exists to avoid.
        Every caller reads the project, which is the only place the source
        lives.
        """
        try:
            return program_for_source(self.full_source(path))
        except ProgramSourceError as error:
            raise ProgramApplyError(f"{path}: {error}") from error

    def section_blockly(self, path: str, section_id: str) -> dict:
        """ONE section's Blockly representation, for the editor that opens it.

        THE READ LEG OF THE SECTION -> BLOCKLY CONTRACT (B8). A student clicks a
        section; this is what that section opens as. Derived on demand from the
        project's own current source for the same reason `program()` is — a
        cached workspace would be a second source of truth — and narrowed to
        one section so the caller never reconstructs the firmware to find it.

        Read-only. Asking for a section changes nothing, so a section of any
        policy may be asked for — which is exactly what EXPLORE is for: reading
        the surrounding code to understand how the part under remediation is
        reached before changing it. The answer carries `representable`, so a consumer
        can tell "Blockly draws this" from "the toolbox has no vocabulary for
        this construct yet" instead of rendering an empty canvas either way.

        The policy that decides whether it may be WRITTEN is not repeated here:
        it lives on the project and is enforced by `apply_section_blockly`.

        Raises `RegionNotFoundError` for a section this file does not have, and
        `ProgramApplyError` for source this project's regions do not correspond
        to (see `UnstructuredFileError`).
        """
        if self._file(path).segment(section_id) is None:
            raise RegionNotFoundError(f"no such region in {path}: {section_id}")
        try:
            return section_representation(self.program(path), section_id)
        except SectionBlocklyError as error:
            raise ProgramApplyError(f"{path}: {error}") from error

    def apply_section_blockly(
        self, path: str, section_id: str, workspace: dict, preserved: Iterable[dict] = ()
    ) -> None:
        """Rewrite ONE section from the Blockly workspace a student edited.

        THE WRITE LEG, AND THE ONE BUILD MODE INTENDS STUDENTS TO USE (B8).
        `update_region` takes C++ text and `apply_program` takes a whole file's
        IR; this takes a single section's blocks, which is the unit the
        interface actually presents. The chain is B5 -> splice -> B6 ->
        re-discover -> replace, and every step of it already existed: what was
        missing was a way in that was addressed by section and shaped like
        Blockly.

        PERMISSION IS CHECKED FIRST, AGAINST THE SAME BINARY `RegionKind`
        `update_region` checks. A LOCKED segment — which is every EXPLORE
        section too, by design (see `app/build/policy.py`) — is refused by name
        before a single block is read, so an unreadable or hostile workspace
        for a section nobody may write never gets parsed at all.

        Raises `RegionNotEditableError` for a section the student may not write,
        `RegionNotFoundError` for one this file does not have,
        `SecurityRegionOwnershipError` for a submission to
        `BuildProject.security_region_id` that still carries opaque C++ (see
        that error's docstring), and `ProgramApplyError` for a malformed
        workspace, a construct Blockly cannot draw yet, or a result that would
        move a locked region or change which sections exist — the last two
        re-proved independently by `apply_program_to_file`, exactly as for
        `apply_program`.
        """
        segment = self._segment(path, section_id)
        if segment.kind is not RegionKind.EDITABLE:
            raise RegionNotEditableError(
                f"region is locked and cannot be edited: {path}#{section_id}"
            )
        try:
            submitted = section_from_state(section_id, workspace, preserved)
        except SectionBlocklyError as error:
            raise ProgramApplyError(f"{path}#{section_id}: {error}") from error

        if section_id == self._project.security_region_id:
            _require_full_ownership(path, submitted)

        try:
            program = program_with_section(self.program(path), submitted)
        except SectionBlocklyError as error:
            raise ProgramApplyError(f"{path}#{section_id}: {error}") from error
        self.apply_program(path, program)

    def apply_program(self, path: str, program: SemanticProgram) -> None:
        """Rewrite one file from a semantic program. Mutates this workspace.

        The B5/B6 counterpart of `update_region`: an edit made as blocks
        rather than as text. B6 generates the C++, B1 re-discovers its
        sections, and the result replaces this file in the project — so the
        very next `materialize()` hands the compiler the generated source and
        `fingerprint()` already reflects it. There is no deferred rendering
        step and no stale copy left behind.

        Raises `ProgramApplyError` — and changes nothing — if the program
        would alter a LOCKED region or the set of regions this file has. The
        locked comparison is made against a regeneration of the current
        source rather than against its raw text, so B6's own layout is not
        mistaken for a student's edit; see `app/build/program_source.py`.
        """
        firmware_file = self._file(path)
        try:
            new_file = apply_program_to_file(firmware_file, program)
        except ProgramSourceError as error:
            raise ProgramApplyError(str(error)) from error
        self._project = replace(
            self._project,
            files=tuple(
                new_file if existing.path == path else existing
                for existing in self._project.files
            ),
        )

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

        PHASE B8 ADDS `policy`, BESIDE `kind` AND NEVER INSTEAD OF IT. `kind`
        stays the permission the backend enforces; `policy` is the richer
        LOCKED/EXPLORE/EDITABLE classification the UI renders (see
        `app/build/policy.py`). Both are emitted per segment so a client never
        has to infer one from the other — and, critically, never has to decide
        editability by matching a C++ function name.
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
                "policy": (
                    None if project.policy is None else project.policy.snapshot()
                ),
            },
            "files": {
                firmware_file.path: {
                    "segments": [
                        {
                            "kind": segment.kind.value,
                            "region_id": segment.region_id,
                            "policy": project.section_policy(segment.region_id).value,
                            "text": segment.text,
                        }
                        for segment in firmware_file.segments
                    ]
                }
                for firmware_file in project.files
            },
        }
