"""Hack Mode WebSocket message protocol.

Every frame on `/ws/hack` is a JSON object carrying a `type` discriminator.
Models are validated with Pydantic rather than passing raw dictionaries
around, so malformed or hostile client frames are rejected at the edge and
the rest of the application only ever sees well-typed values.

Client -> server
    {"type": "input",  "data": "nmap -p 1883 192.168.4.0/24\r"}
    {"type": "resize", "cols": 100, "rows": 30}
    {"type": "hardware_status"}

Server -> client
    {"type": "session",  "session_id": "..."}
    {"type": "output",   "data": "..."}
    {"type": "action",   "action": "clear"}
    {"type": "error",    "message": "..."}
    {"type": "event",    "event": "...", "data": {}}
    {"type": "state",    "data": {}}
    {"type": "hardware", "data": {}}

`hardware` IS ITS OWN FRAME, AND THAT IS THE POINT. Physical ESP32 presence
is infrastructure state, not something a student did: a board being plugged
in is not terminal output, not a command, not a scenario event, and not
scenario state. Reusing `output` would print it into xterm.js, reusing
`event` would put it in the Activity Log, and reusing `state` would conflate
it with `Scenario.snapshot()`. A separate frame is what lets the frontend
poll device presence continuously while changing nothing but a small
hardware-status indicator — see `app/websocket.py`'s handler, which touches
no scenario at all.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app import config

# Bumped whenever the wire format changes incompatibly. The frontend can read
# it from GET /health to detect a backend it does not understand.
#
# 2 (Phase 2B): added the `action` server frame. A client written against
#   version 1 would not know what to do with one, so this is a version bump
#   rather than a silent extension.
# 3 (Phase 2D-A): `event` frames start being emitted (previously reserved and
#   never sent) and the `state` server frame is added. A client written
#   against version 2 would not expect either frame to arrive unsolicited.
# 4 (Phase 1, shared device layer): added the `hardware_status` client message
#   and the `hardware` server frame, which report the platform-level ESP32
#   presence from `app/hardware/` — the same shared state Build Mode's own
#   `hardware` block reports. A version-3 client has no way to render this
#   and would have to show a hardcoded or assumed connection state instead.
#   Nothing about the terminal, command, scenario or event protocol changed.
PROTOCOL_VERSION = 4


class _Frame(BaseModel):
    """Base for every protocol frame.

    Two settings harden the client side of the protocol:

    - `extra="forbid"`: unknown keys signal a confused or hostile client, and
      rejecting them keeps the accepted input surface exactly as wide as it is
      documented to be.
    - `strict=True`: disables Pydantic's lax coercion, so `{"cols": "80"}` is
      an error rather than a silently-accepted 80. Off-protocol frames should
      fail visibly instead of being guessed at.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


# --- client -> server -----------------------------------------------------


class InputMessage(_Frame):
    """One completed command line submitted from the terminal.

    The browser owns line editing: xterm.js handles echo, Backspace, Ctrl+C,
    and cursor movement, and sends the finished line when the student presses
    Enter. `data` is therefore one command, optionally with its trailing
    CR/LF; the backend has no line discipline and does not want one.

    `data` is untrusted. It is interpreted by the controlled command router
    in `app/commands/`, which matches it against a closed set of simulated
    tools — it is never passed to an operating-system shell.
    """

    type: Literal["input"] = "input"
    data: str = Field(max_length=config.MAX_INPUT_CHARS)


class ResizeMessage(_Frame):
    """Terminal geometry change reported by the xterm fit addon."""

    type: Literal["resize"] = "resize"
    cols: int = Field(ge=config.MIN_TERMINAL_COLS, le=config.MAX_TERMINAL_COLS)
    rows: int = Field(ge=config.MIN_TERMINAL_ROWS, le=config.MAX_TERMINAL_ROWS)


class HardwareStatusMessage(_Frame):
    """A request to refresh the shared ESP32 presence check.

    Field-less, exactly like Build Mode's `hardware_status` request (see
    `app/models/build_messages.py`) and for the same reason: this asks the
    shared device layer to re-run its own real `arduino-cli board list`
    against the backend's own configured board target. There is no field
    here a client could use to name a serial port, claim a board is
    connected, or point this backend at a device — and, unlike Build Mode's,
    this request cannot reach an upload path at all.

    Read-only and idle: it runs no command, touches no `Scenario`, and
    produces exactly one `hardware` frame back — never terminal `output`,
    never an `event`, never a scenario `state`. A client may poll it for the
    whole life of a session without adding a single line to the terminal or
    a single row to the Activity Log.
    """

    type: Literal["hardware_status"] = "hardware_status"


ClientMessage = Annotated[
    Union[InputMessage, ResizeMessage, HardwareStatusMessage],
    Field(discriminator="type"),
]

#: Validates an already-decoded JSON object into a `ClientMessage`.
CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- server -> client -----------------------------------------------------


class SessionMessage(_Frame):
    """Sent once, immediately after the connection is accepted."""

    type: Literal["session"] = "session"
    session_id: str
    protocol_version: int = PROTOCOL_VERSION


class OutputMessage(_Frame):
    """Bytes destined for the terminal, written verbatim by the client."""

    type: Literal["output"] = "output"
    data: str


class ActionMessage(_Frame):
    """An instruction to the terminal itself, rather than text to display.

    `clear` is the current member: the backend says the screen should be
    reset and the front end decides how, which keeps terminal-control
    knowledge in the terminal (see `TerminalAction` in app/commands/base.py —
    the value strings must stay in step with this Literal).

    Emitted only in response to a command that asks for it. Phase 2D is what
    makes the React terminal act on it; no client consumes it yet.
    """

    type: Literal["action"] = "action"
    action: Literal["clear"]


class ErrorMessage(_Frame):
    """A protocol-level failure the client should surface, not a crash."""

    type: Literal["error"] = "error"
    message: str


class EventMessage(_Frame):
    """One structured scenario domain event caused by a command.

    `event` is a `ScenarioEventType` value (e.g. `"firmware_extracted"`,
    `"spoof_succeeded"`) and `data` is that event's small detail mapping —
    both taken verbatim from the `ScenarioEvent` the scenario engine emitted;
    this frame adds no semantics of its own. A single `input` frame can cause
    zero, one, or several `event` frames, one per domain event the command
    produced, emitted in the order the scenario recorded them. See
    `app/scenarios/events.py` for the event vocabulary and
    `app/websocket.py::_render` for where these are built.
    """

    type: Literal["event"] = "event"
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class StateMessage(_Frame):
    """A full scenario state snapshot, for the frontend's target device panel.

    `data` is exactly `Scenario.snapshot()` — target identity, environmental
    readings, discovery/attack/completion flags — with no reshaping at this
    layer. Sent once, after any `event` frames, whenever the command that just
    ran caused at least one scenario event (i.e. the scenario state may have
    changed); a command with no scenario events sends no `state` frame, so the
    frontend is not asked to re-render on every keystroke's command.
    """

    type: Literal["state"] = "state"
    data: dict[str, Any] = Field(default_factory=dict)


class HardwareMessage(_Frame):
    """The shared device layer's current view of the attached ESP32.

    `data` is exactly `DeviceState.snapshot()` (see `app/hardware/state.py`)
    — the same process-wide state Build Mode reads, with no reshaping at
    this layer, so the two modes cannot report different boards or ports for
    one physical device. Its `status`/`board_name`/`port` keys are spelled
    identically to Build Mode's `hardware` block, which is what lets one
    frontend formatter render both.

    DELIBERATELY NOT `state`, `event`, OR `output`. See the module docstring:
    hardware presence is infrastructure, and the whole reason it has its own
    frame is that a client can poll it without anything appearing in the
    terminal, in the Activity Log, or in the scenario.

    Sent only in reply to a `hardware_status` request — this backend never
    pushes one unsolicited, so no frame can arrive mid-command and interleave
    with a command's own output.
    """

    type: Literal["hardware"] = "hardware"
    data: dict[str, Any] = Field(default_factory=dict)


ServerMessage = Union[
    SessionMessage,
    OutputMessage,
    ActionMessage,
    ErrorMessage,
    EventMessage,
    StateMessage,
    HardwareMessage,
]
