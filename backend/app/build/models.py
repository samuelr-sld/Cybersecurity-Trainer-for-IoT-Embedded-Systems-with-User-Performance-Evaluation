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

from app.build.policy import InteractionPolicy, ProjectPolicy
from app.hardware.state import DeviceStatus


class RegionKind(str, Enum):
    """Whether a firmware segment may be modified by a student.

    THE ENFORCEMENT VOCABULARY, AND STILL BINARY AFTER PHASE B8. A student
    may write here, or may not; `BuildWorkspace.update_region` and
    `app/build/program_source.py`'s locked-region integrity check are the two
    places that read it, and both ask exactly that question. B8's richer
    `InteractionPolicy` (LOCKED / EXPLORE / EDITABLE, see
    `app/build/policy.py`) is a *classification* carried alongside on the
    project, not a third permission: an EXPLORE section is a LOCKED segment,
    protected by the same code with no new branch.
    """

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
    `app/build/environmental.py`). When set, it must be the `region_id` of an
    EDITABLE segment in one of `files`; nothing here enforces that at
    construction time, but `app/build/workspace.py` and the test suite do.

    PHASE B2 made it OPTIONAL. A project materialized from a panel package's
    real firmware (`app/build/sketch_source.py`) is discovered structurally —
    B1 says which spans are `setup`, `loop`, a callback or a helper, and says
    nothing whatever about which of them a student may edit or where that
    panel's remediation belongs. Declaring one anyway would mean inventing a
    permission decision that only the panel's own remediation activity (a
    later phase) can make, and an empty-string sentinel would be the same
    invention with worse ergonomics. `None` is the honest "no remediation
    region has been declared for this project yet": `BuildService.edit_region`
    compares a submitted region id against it, and `None` simply never
    matches, so no `SECURITY_REGION_EDITED` event is claimed for a project
    that has not named one. The two hand-authored projects (`blink.py`,
    `environmental.py`) still declare theirs as a string and are unaffected.

    PHASE B8 ADDS `policy`, AND IT DOES NOT MOVE THE SECURITY REGION. A
    `ProjectPolicy` (`app/build/policy.py`) classifies each discovered section
    as LOCKED / EXPLORE / EDITABLE for the UI; `security_region_id` remains
    the one field that names the remediation region, so there are never two
    fields a caller could set to different answers. The only rule joining them
    is checked below: when both are present, the security region must be one
    the policy calls EDITABLE. `None` stays the honest default for a project
    whose panel has declared no remediation activity.
    """

    project_id: str
    scenario_id: str
    module_id: str
    firmware_name: str
    board: BoardInfo
    files: tuple[FirmwareFile, ...]
    security_region_id: str | None
    policy: ProjectPolicy | None = None

    def __post_init__(self) -> None:
        if self.policy is None:
            return
        if not isinstance(self.policy, ProjectPolicy):
            raise ValueError(f"project policy must be a ProjectPolicy: {self.policy!r}")
        if self.security_region_id is None:
            return
        declared = self.policy.policy_for(self.security_region_id)
        if declared is not InteractionPolicy.EDITABLE:
            # A remediation region a student cannot write to is a contract
            # that could never be satisfied, so it is refused at construction
            # rather than discovered when the first edit is rejected.
            raise ValueError(
                f"project {self.project_id!r} names {self.security_region_id!r} as its "
                f"security region, but its policy calls that section {declared.value}"
            )

    def file(self, path: str) -> FirmwareFile | None:
        """The file at this path, or None if this project has none."""
        for firmware_file in self.files:
            if firmware_file.path == path:
                return firmware_file
        return None

    def section_policy(self, region_id: str) -> InteractionPolicy:
        """This region's interaction policy — LOCKED when none was declared.

        The one place a caller asks "what is this section FOR?", so nothing
        downstream re-derives it from `RegionKind` (which cannot tell EXPLORE
        from LOCKED) or, worse, from a C++ function name.
        """
        if self.policy is None:
            return InteractionPolicy.LOCKED
        return self.policy.policy_for(region_id)


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


#: Live ESP32 serial-device presence, as Build Mode has always spelled it.
#:
#: PHASE 1: this is no longer a vocabulary of its own — it is *the same enum
#: object* as the shared layer's `DeviceStatus` (`app/hardware/state.py`),
#: re-exported under the name Build Mode's session state, snapshot, service
#: and tests already use. Aliasing rather than redeclaring is the point:
#: there is exactly one hardware-presence vocabulary in this backend, so a
#: Build Mode `state` frame and a Hack Mode `hardware` frame cannot drift
#: into describing the same board with different words, and
#: `HardwareStatus.CONNECTED is DeviceStatus.CONNECTED` is True rather than
#: merely equal.
#:
#: It stays distinct from `FlashStatus` for the reasons that enum documents:
#: a presence check uploads nothing, so it has no RUNNING/SUCCEEDED upload
#: states, and it needs a "nothing has looked yet" value that
#: `FlashStatus.NOT_STARTED` would overload.
HardwareStatus = DeviceStatus
