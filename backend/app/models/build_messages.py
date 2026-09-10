"""Build Mode WebSocket message protocol.

A separate protocol module from `app/models/messages.py` on purpose: Hack
Mode is frozen, and giving Build Mode its own frames and its own
`BUILD_PROTOCOL_VERSION` means nothing here can ever force a version bump —
or a schema change — on `/ws/hack`.

Every frame on `/ws/build` is a JSON object carrying a `type` discriminator,
validated the same strict way Hack Mode's frames are: unknown keys and lax
type coercion are rejected rather than guessed at.

Client -> server
    {"type": "edit_region", "path": "main.ino", "region_id": "blink_program", "source": "..."}
    {"type": "compile"}
    {"type": "flash"}
    {"type": "hardware_status"}

Server -> client
    {"type": "session", "session_id": "...", "protocol_version": 2}
    {"type": "state",   "data": {}}
    {"type": "event",   "event": "...", "data": {}}
    {"type": "error",   "message": "..."}

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
BUILD_PROTOCOL_VERSION = 4


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
#: `flash`, and protocol version 4 adds `hardware_status` — each a proper
#: discriminated Union member, exactly as anticipated when this was a bare
#: alias for `EditRegionMessage`.
BuildClientMessage = Annotated[
    Union[EditRegionMessage, CompileMessage, FlashMessage, HardwareStatusMessage],
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


class BuildStateMessage(_BuildFrame):
    """A full session snapshot: project identity, files, regions, statuses.

    `data` is exactly `BuildSession.snapshot()` — no reshaping at this layer,
    the same discipline Hack Mode's `StateMessage` follows for
    `Scenario.snapshot()`.
    """

    type: Literal["state"] = "state"
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
    BuildEventMessage,
    BuildErrorMessage,
]
