"""Passive data model for a Build Mode firmware project.

Plain dataclasses, no behaviour — mirrors the split `app/scenarios/state.py`
uses for Hack Mode: this module only describes *shape*, and
`app/build/workspace.py` is the only thing that mutates it. Keeping the model
passive is what lets a future project (a different scenario's firmware) reuse
the same shape without inheriting editing logic.

REGION MODEL. A firmware file is not one opaque blob of text; it is an
ordered sequence of `FileSegment`s, each explicitly tagged LOCKED or
EDITABLE and carrying a stable `region_id`. This is deliberately not a
line-number range: a student's edits inside one segment must never shift the
boundaries of another, and a marker/segment identity survives that in a way
line numbers cannot. Rendering a file is just concatenating its segments in
order (`FirmwareFile.render`), and editing is always addressed by
`(path, region_id)`, never by an offset into rendered text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RegionKind(str, Enum):
    """Whether a firmware segment may be modified by a student."""

    LOCKED = "locked"
    EDITABLE = "editable"


@dataclass(frozen=True)
class FileSegment:
    """One contiguous piece of a firmware file's source.

    `region_id` is a stable identifier, not a position — see the module
    docstring. Every segment has one, including LOCKED segments, so an
    attempt to edit a locked region can be rejected by name (see
    `app/build/workspace.py`) rather than silently doing nothing.
    """

    kind: RegionKind
    region_id: str
    text: str


@dataclass(frozen=True)
class FirmwareFile:
    """One firmware source file, as an ordered sequence of segments."""

    path: str
    segments: tuple[FileSegment, ...]

    def render(self) -> str:
        """The full file source, exactly as a compiler would see it."""
        return "".join(segment.text for segment in self.segments)

    def segment(self, region_id: str) -> FileSegment | None:
        """The segment with this id, or None if this file has none."""
        for segment in self.segments:
            if segment.region_id == region_id:
                return segment
        return None


@dataclass(frozen=True)
class BoardInfo:
    """The target board a project's firmware is written for.

    `fqbn` is the Arduino CLI's Fully Qualified Board Name (e.g.
    `esp32:esp32:esp32`) — added in Phase 3B so `app/build/compiler.py` has
    a trusted, project-supplied board target to compile against. It always
    comes from this backend's own project definition, never from the
    frontend or a compile request.
    """

    name: str
    mcu: str
    fqbn: str


@dataclass(frozen=True)
class BuildProject:
    """One Build Mode firmware project: identity, board, and its files.

    `security_region_id` names the one region this scenario's remediation
    lives in — the region a future Blockly workspace will target (see
    `app/build/environmental.py`). It must be the `region_id` of an EDITABLE
    segment in one of `files`; nothing here enforces that at construction
    time, but `app/build/workspace.py` and the test suite do.
    """

    project_id: str
    scenario_id: str
    module_id: str
    firmware_name: str
    board: BoardInfo
    files: tuple[FirmwareFile, ...]
    security_region_id: str

    def file(self, path: str) -> FirmwareFile | None:
        """The file at this path, or None if this project has none."""
        for firmware_file in self.files:
            if firmware_file.path == path:
                return firmware_file
        return None


class CompileStatus(str, Enum):
    """Compilation state. Phase 3A never sets anything but NOT_STARTED.

    The rest of the vocabulary exists now so the session state shape does not
    change again in Phase 3B, when `app/build/service.py` grows the ability
    to actually set them.
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FlashStatus(str, Enum):
    """Flash (upload-to-device) state, driven for real since Phase 3C.

    Two members beyond the `CompileStatus` vocabulary, because flashing has
    a failure domain compilation does not: the physical world.

    `DETECTING` covers the device-discovery step that necessarily precedes
    an upload — the student has asked to flash, but no upload has started
    and no port has been chosen yet.

    `NO_DEVICE` is a *terminal* state, not a failure of the upload: nothing
    was uploaded because nothing was plugged in. Keeping it distinct from
    `FAILED` is the whole point — "no ESP32 is connected" must never be
    presented as a compiler error, a toolchain problem, or a flash that went
    wrong. Everything that genuinely went wrong while flashing (an upload
    that exited non-zero, a timeout, an ambiguous set of connected boards, a
    missing toolchain) lands in `FAILED`, with the specific
    `FlashFailureCategory` carried alongside in `flash_output`.

    `RUNNING` means a real `arduino-cli upload` process is in flight, and
    `SUCCEEDED` means that process exited 0 — the firmware was transferred.
    It does not mean the firmware runs correctly or is secure; nothing in
    this codebase validates that yet.
    """

    NOT_STARTED = "not_started"
    DETECTING = "detecting"
    RUNNING = "running"
    NO_DEVICE = "no_device"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ValidationStatus(str, Enum):
    """Remediation validation state. Phase 3A never leaves NOT_STARTED."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class HardwareStatus(str, Enum):
    """Live ESP32 serial-device presence, independent of any flash attempt.

    This is deliberately its own vocabulary rather than reuse of
    `FlashStatus`: a hardware check never uploads anything, so it has no
    `RUNNING`/`SUCCEEDED` upload states, and it *does* need a value for "no
    check has happened yet" (`NOT_CHECKED`), which `FlashStatus.NOT_STARTED`
    would overload with the unrelated "no flash requested" meaning.

    `AMBIGUOUS` mirrors `FlashFailureCategory.AMBIGUOUS_DEVICE` for the same
    reason that one exists: several plausible boards were found and this
    backend will not guess which is the intended ESP32.
    """

    NOT_CHECKED = "not_checked"
    DETECTING = "detecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"
