"""Hack Mode WebSocket message protocol.

Every frame on `/ws/hack` is a JSON object carrying a `type` discriminator.
Models are validated with Pydantic rather than passing raw dictionaries
around, so malformed or hostile client frames are rejected at the edge and
the rest of the application only ever sees well-typed values.

Client -> server
    {"type": "input",  "data": "nmap -p 1883 192.168.4.0/24\r"}
    {"type": "resize", "cols": 100, "rows": 30}

Server -> client
    {"type": "session", "session_id": "..."}
    {"type": "output",  "data": "..."}
    {"type": "action",  "action": "clear"}
    {"type": "error",   "message": "..."}
    {"type": "event",   "event": "...", "data": {}}
    {"type": "state",   "data": {}}
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
PROTOCOL_VERSION = 3


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


ClientMessage = Annotated[
    Union[InputMessage, ResizeMessage],
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


ServerMessage = Union[
    SessionMessage,
    OutputMessage,
    ActionMessage,
    ErrorMessage,
    EventMessage,
    StateMessage,
]
