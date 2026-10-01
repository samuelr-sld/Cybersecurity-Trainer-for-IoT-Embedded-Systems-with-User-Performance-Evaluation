"""Build Mode WebSocket message protocol.

A separate protocol module from `app/models/messages.py` on purpose: Hack
Mode is frozen, and giving Build Mode its own frames and its own
`BUILD_PROTOCOL_VERSION` means nothing here can ever force a version bump —
or a schema change — on `/ws/hack`.

Every frame on `/ws/build` is a JSON object carrying a `type` discriminator,
validated the same strict way Hack Mode's frames are: unknown keys and lax
type coercion are rejected rather than guessed at.

Client -> server
    {"type": "section_blockly",    "path": "main.ino", "section_id": "setup"}
    {"type": "edit_section_blocks","path": "main.ino", "section_id": "setup",
     "workspace": {...}, "preserved": [...]}
    {"type": "edit_region", "path": "main.ino", "region_id": "blink_program", "source": "..."}
    {"type": "compile"}
    {"type": "flash"}
    {"type": "validate"}
    {"type": "hardware_status"}

Server -> client
    {"type": "session", "session_id": "...", "protocol_version": 2}
    {"type": "state",   "data": {}}
    {"type": "section", "data": {}}
    {"type": "event",   "event": "...", "data": {}}
    {"type": "error",   "message": "..."}

BUILD MODE IS A SECTION-BASED BLOCKLY EDITOR, AND THIS PROTOCOL SAYS SO SINCE
VERSION 6. The student's flow is: the `state` snapshot lists every discovered
section with its `InteractionPolicy`; clicking one sends `section_blockly` and
gets that section's blocks back; editing them sends `edit_section_blocks`. The
generated C++ is an OUTPUT of that — the backend writes it (B6), reconstructs
the firmware around it, and the student never types it. `edit_region`, which
carries raw C++ text, is the LEGACY path: the Blink POC uses it and the toolbox
cannot yet draw every construct, so it remains, but it is not the remediation
interface and no new client should be built on it.

Neither the `compile` nor the `flash` request carries any fields at all:
both always target the session's own current workspace and the project's own
board, entirely server-side state (see `app/build/service.py`). This is
deliberate — there is no field here a hostile client could use to name a
different board, supply raw source to build, choose a serial port, point at
a firmware binary, or pass an extra toolchain flag. Everything the Arduino
CLI is invoked with is chosen by the backend.

`hardware_status` is field-less for the same reason: it asks the backend to
re-run its own real device discovery (`app/build/flasher.py`) and refresh
the `hardware` block in the next `state` snapshot — there is nothing on this
wire a client could use to name a port or claim a board is connected.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app import config

# Bumped whenever the Build Mode wire format changes incompatibly. Tracked
# independently of Hack Mode's `PROTOCOL_VERSION` — the two channels are
# unrelated protocols that happen to share a transport style.
#
# 2 (Phase 3B): added the `compile` client message. A client written
#   against version 1 would not know this request exists, which is exactly
#   why this is a version bump rather than a silent extension.
# 3 (Phase 3C): added the `flash` client message, plus `flash_ready` and
#   `flash_output` in the `state` snapshot and two new `flash_status`
#   values (`detecting`, `no_device`) a version-2 client would not know how
#   to render.
# 4: added the `hardware_status` client message and a `hardware` block
#   (`status`/`board_name`/`port`) in the `state` snapshot — a live ESP32
#   presence check independent of `flash_status`, which only ever reflects
#   the backend's own discovery from the last flash attempt (or never, if
#   one hasn't happened). A version-3 client has no way to render this and
#   would keep showing a hardcoded/assumed connection state instead.
# 5 (Phase B7): added the `validate` client message and a `validation_output`
#   block in the `state` snapshot, and `validation_status` now actually
#   moves off `not_started`. A version-4 client has no way to request a
#   validation and no way to render its verdict — it would show only the
#   four-member status, which cannot distinguish a fix that failed its check
#   from a check that could not run.
# 6 (Phase B8 correction): added the `section_blockly` and
#   `edit_section_blocks` client messages and the `section` server frame —
#   the section -> Blockly contract Build Mode's actual interaction model
#   needs. Until version 6 the only way to change firmware on this wire was
#   `edit_region`'s raw C++ text, so a client had no way to be a Blockly
#   editor at all: B4/B5 existed but nothing could reach them. A version-5
#   client cannot request a section's blocks and cannot submit one.
# 7 (Phase 1.2): added a `remediation` block to the `state` snapshot — the
#   connected panel's own `RemediationSpec` prose (vulnerability /
#   remediation_goal / validation_requirement), verbatim, when its package
#   declared one; `null` otherwise. No new client message: the plan already
#   existed server-side (Phase B7) and was simply never sent. A version-6
#   client has no way to render what a remediation must achieve before
#   running validation.
BUILD_PROTOCOL_VERSION = 7


class _BuildFrame(BaseModel):
    """Base for every Build Mode protocol frame — see `app/models/messages.py`
    for why `extra="forbid"` and `strict=True` matter on an untrusted socket.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


# --- client -> server -------------------------------------------------------


class EditRegionMessage(_BuildFrame):
    """A request to replace one editable region's source.

    `path` and `region_id` are identifiers, not content, so they are capped
    far below `source`. `source` is untrusted student C++ text: it is never
    parsed or executed by this backend, only stored — see
    `app/build/workspace.py`, which is what actually enforces that only an
    EDITABLE region can be named here.
    """

    type: Literal["edit_region"] = "edit_region"
    path: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)
    region_id: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)
    source: str = Field(max_length=config.MAX_BUILD_REGION_SOURCE_CHARS)


class SectionBlocklyMessage(_BuildFrame):
    """A request for one discovered section's Blockly representation.

    The read half of the section -> Blockly contract (B8). `section_id` is the
    stable id B1 derived from the construct and B2/B3/B4 all reuse — never a
    display name and never a C++ function name — so a client selects a section
    by the same identity every backend layer uses.

    Read-only, and carrying no `source`, no `workspace` and no policy field: it
    cannot change anything, cannot assert what a section contains, and cannot
    claim a permission. A section of ANY policy may be requested, which is what
    EXPLORE exists for; whether it may be WRITTEN is decided when a write
    arrives, against the project's own policy.
    """

    type: Literal["section_blockly"] = "section_blockly"
    path: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)
    section_id: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)


class EditSectionBlocksMessage(_BuildFrame):
    """A request to rewrite one section from the blocks a student arranged.

    THE INTENDED EDITING FRAME. `workspace` is the Blockly serialization state
    the editor produced for this one section, and `preserved` is the fragment
    list that came with it (a Blockly workspace cannot hold a node that is not
    a block, so source the toolbox has no vocabulary for travels beside it —
    see `app/build/blockly_bridge/models.py`).

    Both are untrusted structure: the backend reads them with
    `app/build/blockly_bridge/workspace_state.py`, which validates every node
    against the block catalog and repairs nothing. Neither field is C++ the
    backend executes or compiles as given — the firmware that reaches the
    compiler is REGENERATED from what these blocks mean (B5 -> B6), and
    `app/build/workspace.py` refuses the whole request before parsing if
    `section_id` names a region this project's policy does not open.

    There is no `source` field, deliberately: a client cannot submit C++
    through this frame, which is the whole point of it existing beside
    `edit_region` rather than replacing it with one more text channel.
    """

    type: Literal["edit_section_blocks"] = "edit_section_blocks"
    path: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)
    section_id: str = Field(min_length=1, max_length=config.MAX_BUILD_IDENTIFIER_CHARS)
    workspace: dict[str, Any] = Field(default_factory=dict)
    preserved: list[dict[str, Any]] = Field(
        default_factory=list, max_length=config.MAX_BUILD_PRESERVED_FRAGMENTS
    )


class CompileMessage(_BuildFrame):
    """A request to compile the session's current workspace.

    Deliberately field-less — see the module docstring. `path`/`region_id`/
    `source` have no place here: a compile always targets whatever
    `BuildWorkspace` the session already holds, materialized fresh by
    `BuildService.compile_workspace` (see `app/build/workspace.py`
    `materialize`), never anything the client names or supplies inline.
    """

    type: Literal["compile"] = "compile"


class FlashMessage(_BuildFrame):
    """A request to upload the session's last successful build to a device.

    Field-less for exactly the same reasons `CompileMessage` is, and for one
    more that matters more: a `port`, an `input_dir`, or an `fqbn` here
    would hand a client control over which physical device this backend
    writes firmware to and which binary it writes. The port is discovered
    server-side (`app/build/flasher.py`), the firmware is the artifact the
    session's own last successful compile produced, and the board comes from
    the project's `BoardInfo` — none of it is nameable from the wire.
    """

    type: Literal["flash"] = "flash"


class ValidateMessage(_BuildFrame):
    """A request to validate the remediation now running on the device.

    Field-less, for the same reason `CompileMessage` and `FlashMessage` are,
    and for one more specific to this operation: a `success`, `outcome`,
    `result` or `evidence` field here would let a client ASSERT that its own
    firmware passed. The verdict comes from a backend validator
    (`app/build/validation/`) judging the firmware the backend itself
    compiled and uploaded; there is nothing on this wire but the request.

    What it validates is never named either: it is always this session's own
    workspace, and `BuildService.validate_workspace` refuses unless that
    exact workspace was successfully flashed.
    """

    type: Literal["validate"] = "validate"


class HardwareStatusMessage(_BuildFrame):
    """A request to refresh the session's live ESP32 presence check.

    Field-less for the same reason `CompileMessage`/`FlashMessage` are: this
    always re-runs the backend's own real `arduino-cli board list` discovery
    (`app/build/flasher.py`) against the session's own project board, never
    anything a client names. Read-only — this never uploads anything, and a
    client may send it as often as it likes (the frontend polls it on a
    lightweight interval) without any risk of triggering a flash.
    """

    type: Literal["hardware_status"] = "hardware_status"


#: Phase 3A had only `edit_region`; Phase 3B added `compile`, Phase 3C added
#: `flash`, protocol version 4 added `hardware_status`, and version 5 (Phase
#: B7) adds `validate` — each a proper discriminated Union member, exactly as
#: anticipated when this was a bare alias for `EditRegionMessage`.
BuildClientMessage = Annotated[
    Union[
        SectionBlocklyMessage,
        EditSectionBlocksMessage,
        EditRegionMessage,
        CompileMessage,
        FlashMessage,
        ValidateMessage,
        HardwareStatusMessage,
    ],
    Field(discriminator="type"),
]

#: Validates an already-decoded JSON object into a `BuildClientMessage`.
BUILD_CLIENT_MESSAGE_ADAPTER: TypeAdapter[BuildClientMessage] = TypeAdapter(
    BuildClientMessage
)


# --- server -> client --------------------------------------------------------


class BuildSessionMessage(_BuildFrame):
    """Sent once, immediately after the connection is accepted."""

    type: Literal["session"] = "session"
    session_id: str
    protocol_version: int = BUILD_PROTOCOL_VERSION
    #: True when this connection re-attached to a session that was already
    #: running (`?session=<id>` after a reload); see `SessionMessage.resumed`.
    resumed: bool = False
    #: Whole seconds since the session began, measured by the server (so a
    #: client with a skewed clock still continues from the true start). Every
    #: connection carries it; a new session reports ~0.
    elapsed_seconds: int = 0
    #: Only on a resume: the events the session already recorded, in order,
    #: each with its own `elapsed_seconds`, so a reloaded page rebuilds the
    #: Activity Log it had. Taken from `BuildSession.events`; nothing is
    #: generated for it. Empty for a new session, whose bootstrap events
    #: still arrive as ordinary `event` frames.
    history: list[dict[str, Any]] = Field(default_factory=list)


class BuildStateMessage(_BuildFrame):
    """A full session snapshot: project identity, files, regions, statuses.

    `data` is exactly `BuildSession.snapshot()` — no reshaping at this layer,
    the same discipline Hack Mode's `StateMessage` follows for
    `Scenario.snapshot()`.
    """

    type: Literal["state"] = "state"
    data: dict[str, Any] = Field(default_factory=dict)


class BuildSectionMessage(_BuildFrame):
    """One section's Blockly representation, answering a `section_blockly`.

    Its own frame rather than a block inside `state` (B8): a student opens one
    section at a time, and putting sixteen sections' workspaces in every
    snapshot would make each compile, flash and hardware poll carry the whole
    firmware as blocks. It is also not an `event` — nothing happened.

    `data` is `{path, sectionId, representable, workspace, preserved}` exactly
    as `BlocklySection.to_representation()` builds it, with no reshaping at
    this layer. `representable` is the field that keeps a client honest: false
    means the toolbox has no vocabulary for this construct yet, so it must be
    shown read-only rather than as an empty canvas.
    """

    type: Literal["section"] = "section"
    data: dict[str, Any] = Field(default_factory=dict)


class BuildEventMessage(_BuildFrame):
    """One structured Build Mode domain event.

    `event` is a `BuildEventType` value and `data` is that event's detail
    mapping, taken verbatim from the `BuildEvent` the service emitted.
    """

    type: Literal["event"] = "event"
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class BuildErrorMessage(_BuildFrame):
    """A protocol-level or rejected-edit failure the client should surface."""

    type: Literal["error"] = "error"
    message: str


BuildServerMessage = Union[
    BuildSessionMessage,
    BuildStateMessage,
    BuildSectionMessage,
    BuildEventMessage,
    BuildErrorMessage,
]
