"""Hack Mode WebSocket endpoint (`/ws/hack`).

SECURITY BOUNDARY — READ BEFORE EXTENDING THIS MODULE
-----------------------------------------------------
Terminal input arriving on this socket is untrusted. This module transports
and validates messages; it does not interpret them and it never executes
them. There is intentionally no import of `os`, `subprocess`, `pty`, or any
other process-spawning facility anywhere under `app/`, and none may be added
to serve terminal input: no `os.system`, no `subprocess.run/Popen`, no
`shell=True`, no `eval`/`exec`, no CMD/PowerShell/shell bridge.

When command handling arrives (Phase 2B+), it goes through a controlled
command router and scenario engine that match input against a closed set of
simulated tools. A student's keystrokes must never reach a host shell.

Phase 2A behaviour: accept the connection, create an isolated session,
announce the session id, acknowledge `input` and `resize` frames with
structured messages, and drop the session on disconnect.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError

from app import config
from app.models.messages import (
    CLIENT_MESSAGE_ADAPTER,
    ClientMessage,
    ErrorMessage,
    InputMessage,
    OutputMessage,
    ResizeMessage,
    ServerMessage,
    SessionMessage,
)
from app.sessions import HackSession, session_manager

logger = logging.getLogger(__name__)

router = APIRouter()

BANNER = (
    "[backend] hack mode channel established\r\n"
    "[backend] phase 2A: transport only - commands are not executed\r\n"
)


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


async def _handle_message(
    websocket: WebSocket, session: HackSession, message: ClientMessage
) -> None:
    """Dispatch one validated client message."""
    if isinstance(message, InputMessage):
        # Phase 2A stub. This is the seam where the command router will be
        # called; until then the input is acknowledged and discarded. It is
        # never executed, and never echoed back verbatim.
        await send(
            websocket,
            OutputMessage(data=f"[backend] input received ({len(message.data)} chars)\r\n"),
        )
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
