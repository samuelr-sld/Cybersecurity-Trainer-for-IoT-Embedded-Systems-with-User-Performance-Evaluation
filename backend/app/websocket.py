"""Hack Mode WebSocket endpoint (`/ws/hack`).

SECURITY BOUNDARY — READ BEFORE EXTENDING THIS MODULE
-----------------------------------------------------
Terminal input arriving on this socket is untrusted. This module transports
and validates messages; it does not interpret them and it never executes
them. There is intentionally no import of `os`, `subprocess`, `pty`, or any
other process-spawning facility anywhere under `app/`, and none may be added
to serve terminal input: no `os.system`, no `subprocess.run/Popen`, no
`shell=True`, no `eval`/`exec`, no CMD/PowerShell/shell bridge.

Command handling goes through the controlled command router in
`app/commands/`, which matches input against a closed set of simulated tools.
A student's keystrokes must never reach a host shell.

TERMINAL RESPONSIBILITY BOUNDARY
--------------------------------
The browser owns line editing. xterm.js handles character input, local echo,
Backspace, Ctrl+C, and the cursor, and sends one completed command line per
`input` frame. This endpoint does not echo keystrokes, does not maintain a
line buffer, and must not grow one.

Phase 2B behaviour: accept the connection, create an isolated session,
announce the session id, route each completed `input` line through the
command router and render the structured result back as `output`/`action`
frames, record `resize` geometry, and drop the session on disconnect.

Phase 2D-A behaviour: a command whose `CommandResult` carries scenario events
(forwarded from `Scenario`/`ScenarioOutcome` via `CommandResult.events`, see
app/commands/base.py and app/commands/scenario_adapter.py) additionally gets
one `event` frame per event and one trailing `state` frame carrying
`Scenario.snapshot()`, so the frontend can render the target device panel
without polling or scraping terminal text. This module still only transports
that structure — it has no scenario-specific knowledge of its own.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError

from app import config
from app.commands import CommandContext, CommandResult, default_router
from app.models.messages import (
    CLIENT_MESSAGE_ADAPTER,
    ActionMessage,
    ClientMessage,
    ErrorMessage,
    EventMessage,
    InputMessage,
    OutputMessage,
    ResizeMessage,
    ServerMessage,
    SessionMessage,
    StateMessage,
)
from app.sessions import HackSession, session_manager

if TYPE_CHECKING:  # pragma: no cover
    from app.scenarios import Scenario

logger = logging.getLogger(__name__)

router = APIRouter()

BANNER = (
    "[backend] hack mode channel established\r\n"
    "[backend] phase 2B: simulated commands only - nothing runs on the host\r\n"
    "[backend] type 'help' to list the available commands\r\n"
)

#: Line terminator written to the terminal. xterm.js is in raw mode, so a
#: bare LF moves down without returning to column 0; CRLF is what a
#: terminal expects. This is a transport-layer concern, which is exactly
#: why `CommandResult.lines` carries lines without terminators.
LINE_ENDING = "\r\n"


async def send(websocket: WebSocket, message: ServerMessage) -> None:
    """Serialise and send one server frame."""
    await websocket.send_text(message.model_dump_json())


def _parse(raw: str) -> ClientMessage:
    """Validate one raw text frame into a typed client message.

    Raises ValueError with a short, non-reflecting reason on bad input — the
    reason is never built from the client's own payload, so a malformed frame
    cannot smuggle content back through the error channel.
    """
    if len(raw) > config.MAX_MESSAGE_CHARS:
        raise ValueError("message exceeds maximum size")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    try:
        return CLIENT_MESSAGE_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValueError("message does not match the protocol schema") from exc


def _render(result: CommandResult, scenario: "Scenario") -> list[ServerMessage]:
    """Turn a CommandResult into the frames that express it.

    This is the only place that knows both the command vocabulary and the
    wire protocol; the router deliberately knows neither.

    Frame order is: actions, then coalesced output, then one `event` frame
    per scenario domain event the command caused, then — only when the
    command caused at least one event — a single `state` snapshot.

    Actions are sent before output, so a command that both clears the screen
    and prints something lands in the order a terminal user expects. Output
    is coalesced into a single frame so that one command causes one write,
    rather than the terminal painting a result line by line. Events follow
    output because they are structured signals about what the terminal text
    just described, not the text itself. The state snapshot comes last and is
    gated on `result.events` rather than sent unconditionally: the scenario
    only records an event when something about its state actually changed
    (see app/scenarios/environmental.py), so an empty `events` tuple means
    the snapshot the client already has is still current and resending it
    would be pure noise.
    """
    frames: list[ServerMessage] = [
        ActionMessage(action=action.value) for action in result.actions
    ]
    if result.lines:
        frames.append(
            OutputMessage(data="".join(line + LINE_ENDING for line in result.lines))
        )
    for event in result.events:
        frames.append(EventMessage(event=event.type.value, data=dict(event.data)))
    if result.events:
        frames.append(StateMessage(data=scenario.snapshot()))
    return frames


async def _handle_message(
    websocket: WebSocket, session: HackSession, message: ClientMessage
) -> None:
    """Dispatch one validated client message."""
    if isinstance(message, InputMessage):
        # One completed command line. The router resolves it against a closed
        # command table and returns a structured result; it never touches
        # this socket, and nothing in it is executed.
        #
        # `dispatch` contains its own failures, so an unsupported or broken
        # command yields terminal text rather than an exception that would
        # drop the student's session.
        context = CommandContext(session=session)
        result = await default_router.dispatch(message.data, context)
        for frame in _render(result, context.scenario):
            await send(websocket, frame)
        return

    if isinstance(message, ResizeMessage):
        # Bounds already enforced by the model; record and acknowledge.
        # No PTY exists yet, so there is nothing further to resize.
        session.resize(message.cols, message.rows)
        await send(
            websocket,
            OutputMessage(
                data=f"[backend] resize accepted ({message.cols}x{message.rows})\r\n"
            ),
        )
        return

    # Unreachable while ClientMessage is a closed union; kept so that adding a
    # member without a handler fails loudly instead of silently doing nothing.
    await send(websocket, ErrorMessage(message="unsupported message type"))


@router.websocket("/ws/hack")
async def hack_websocket(websocket: WebSocket) -> None:
    """Serve one Hack Mode terminal session."""
    await websocket.accept()
    session = await session_manager.create()
    logger.info("hack session opened: %s", session.session_id)

    try:
        await send(websocket, SessionMessage(session_id=session.session_id))
        await send(websocket, OutputMessage(data=BANNER))

        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                break

            raw = frame.get("text")
            if raw is None:
                # Binary frames are not part of the protocol; refuse rather
                # than guessing at an encoding.
                await send(
                    websocket, ErrorMessage(message="binary frames are not supported")
                )
                continue

            try:
                message = _parse(raw)
            except ValueError as exc:
                await send(websocket, ErrorMessage(message=str(exc)))
                continue

            await _handle_message(websocket, session, message)

    except WebSocketDisconnect:
        pass
    finally:
        await session_manager.remove(session.session_id)
        logger.info("hack session closed: %s", session.session_id)
