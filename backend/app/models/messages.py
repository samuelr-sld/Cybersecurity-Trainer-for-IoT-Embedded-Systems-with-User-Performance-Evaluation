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
    {"type": "error",   "message": "..."}
    {"type": "event",   "event": "...", "data": {}}   # reserved, see below
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app import config

# Bumped whenever the wire format changes incompatibly. The frontend can read
# it from GET /health to detect a backend it does not understand.
PROTOCOL_VERSION = 1


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
    """Raw keystrokes from xterm.js.

    `data` is untrusted terminal input. It is transported and (in later
    phases) interpreted by a controlled command router — it is never passed
    to an operating-system shell.
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


class ErrorMessage(_Frame):
    """A protocol-level failure the client should surface, not a crash."""

    type: Literal["error"] = "error"
    message: str


class EventMessage(_Frame):
    """Structured, non-terminal signal about session progress.

    RESERVED FOR A LATER PHASE. The shape is fixed now so the frontend can be
    written against a stable envelope, but Phase 2A never emits one: the event
    logger and evaluation pipeline that will produce these do not exist yet.
    """

    type: Literal["event"] = "event"
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


ServerMessage = Union[SessionMessage, OutputMessage, ErrorMessage, EventMessage]
