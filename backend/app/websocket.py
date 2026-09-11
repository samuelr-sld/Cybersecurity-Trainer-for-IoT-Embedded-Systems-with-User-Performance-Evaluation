"""Hack Mode WebSocket endpoint (`/ws/hack`).

Student terminal input remains a closed, non-shell command interface. Hack
Mode additionally requires one real ESP32 candidate to be present over USB
before scenario commands are accepted. Physical detection reuses Build Mode's
Arduino CLI detector; this endpoint never flashes or opens a serial console.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError

from app import config
from app.commands import CommandContext, CommandResult, default_router
from app.hack_hardware import HackHardwareStatus, detect_hack_hardware
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
    "[backend] physical ESP32 USB connection required\r\n"
    "[backend] waiting for one ESP32 device...\r\n"
)
LINE_ENDING = "\r\n"
HARDWARE_POLL_SECONDS = 2.0


async def send(websocket: WebSocket, message: ServerMessage) -> None:
    await websocket.send_text(message.model_dump_json())


def _parse(raw: str) -> ClientMessage:
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


async def _send_hardware_status(websocket: WebSocket, session: HackSession) -> None:
    """Refresh and announce the real USB ESP32 state.

    Detection is deliberately the same Arduino CLI implementation Build Mode
    uses. One candidate is required; zero candidates blocks Hack Mode and more
    than one candidate is ambiguous, so the backend never guesses a board.
    """
    result = await detect_hack_hardware()
    session.set_hardware(result.status, result.device.port if result.device else None)

    if result.status is HackHardwareStatus.CONNECTED:
        text = f"[hardware] ESP32 connected over USB: {result.device.port}\r\n"
    elif result.status is HackHardwareStatus.DISCONNECTED:
        text = "[hardware] NO ESP32 DEVICE DETECTED — connect the target over USB.\r\n"
    elif result.status is HackHardwareStatus.AMBIGUOUS:
        text = "[hardware] MULTIPLE ESP32 CANDIDATES DETECTED — disconnect unintended devices.\r\n"
    else:
        text = f"[hardware] DEVICE DETECTION ERROR: {result.detail}\r\n"
    await send(websocket, OutputMessage(data=text))


async def _handle_message(
    websocket: WebSocket, session: HackSession, message: ClientMessage
) -> None:
    if isinstance(message, InputMessage):
        if not session.hardware_ready:
            await send(
                websocket,
                OutputMessage(
                    data="[hardware] Hack Mode is locked until one ESP32 is connected over USB.\r\n"
                ),
            )
            return
        context = CommandContext(session=session)
        result = await default_router.dispatch(message.data, context)
        for frame in _render(result, context.scenario):
            await send(websocket, frame)
        return

    if isinstance(message, ResizeMessage):
        session.resize(message.cols, message.rows)
        await send(
            websocket,
            OutputMessage(
                data=f"[backend] resize accepted ({message.cols}x{message.rows})\r\n"
            ),
        )
        return

    await send(websocket, ErrorMessage(message="unsupported message type"))


@router.websocket("/ws/hack")
async def hack_websocket(websocket: WebSocket) -> None:
    """Serve one Hack Mode session and wait for a real ESP32 USB device."""
    await websocket.accept()
    session = await session_manager.create()
    logger.info("hack session opened: %s", session.session_id)

    try:
        await send(websocket, SessionMessage(session_id=session.session_id))
        await send(websocket, OutputMessage(data=BANNER))
        await _send_hardware_status(websocket, session)

        while True:
            try:
                frame = await asyncio.wait_for(
                    websocket.receive(), timeout=HARDWARE_POLL_SECONDS
                )
            except asyncio.TimeoutError:
                # Keep waiting without requiring the student to reconnect.
                # Polling also catches an ESP32 being unplugged mid-session.
                previous = session.hardware_ready
                await _send_hardware_status(websocket, session)
                if previous and not session.hardware_ready:
                    await send(
                        websocket,
                        OutputMessage(
                            data="[hardware] ESP32 connection lost — Hack Mode is locked.\r\n"
                        ),
                    )
                continue

            if frame["type"] == "websocket.disconnect":
                break

            raw = frame.get("text")
            if raw is None:
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
