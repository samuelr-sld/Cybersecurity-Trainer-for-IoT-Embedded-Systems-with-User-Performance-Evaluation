"""Mode Session Preparation WebSocket endpoint (`/ws/prepare`).

The transport for `app/mode_preparation.py`, shared by both modes: the
frontend opens this socket when a student taps HACK or BUILD, sends one
`prepare` frame naming the mode, watches one `stage` frame per real stage
transition, and receives exactly one `result`. Only a successful result lets
the frontend open the requested mode; `/ws/hack` and `/ws/build` are
untouched and know nothing about this socket.

ONE PREPARATION PER CONNECTION. Retry is a new connection, so every attempt
starts from a clean state with no leftover task or partial result.

IT KEEPS RECEIVING WHILE IT PREPARES. A baseline compile takes about a
minute. `/ws/hack` learned that a socket nobody calls `receive()` on stops
answering uvicorn's keepalive pings and is closed with 1011; so, like that
endpoint, the preparation runs as its own task while this loop keeps
receiving, which is also how a disconnect is noticed mid-compile.

A DISCONNECT CANCELS THE RUN, AND TEARDOWN DOES NOT AWAIT. A student pressing
BACK closes the socket; the `finally` below cancels the preparation task
synchronously (an `await` there would itself be cancelled — see the teardown
note in `app/websocket.py`). Cancellation reaches `run_capture`, which kills
the toolchain's process tree, and the run's own `finally` removes its build
directory and releases the identity-probe hold.

NOT A SHELL, AND NOTHING FROM THE CLIENT REACHES A TOOL. The one client field
is a two-member `mode` enum; this module spawns nothing and names no port,
firmware or board.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app import mode_preparation
from app.mode_preparation import PreparationProgress, PreparationResult, SessionMode
from app.models.preparation_messages import (
    MAX_PREPARATION_MESSAGE_CHARS,
    PREPARATION_CLIENT_MESSAGE_ADAPTER,
    PrepareMessage,
    PreparationErrorMessage,
    PreparationResultMessage,
    PreparationStageMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class _Sender:
    """Serialised, disconnect-tolerant writer — the preparation task and the
    receive loop may both write, and a closed socket must not turn a
    finished stage into an exception inside the lifecycle."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: BaseModel) -> bool:
        async with self._lock:
            try:
                await self._websocket.send_text(message.model_dump_json())
                return True
            except (WebSocketDisconnect, RuntimeError):
                return False


def _parse(raw: str | None) -> PrepareMessage:
    """Validate the one client frame. Short, non-reflecting reasons only."""
    if raw is None:
        raise ValueError("binary frames are not supported")
    if len(raw) > MAX_PREPARATION_MESSAGE_CHARS:
        raise ValueError("message exceeds maximum size")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    try:
        return PREPARATION_CLIENT_MESSAGE_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValueError("message does not match the protocol schema") from exc


def _stage_frame(progress: PreparationProgress) -> PreparationStageMessage:
    return PreparationStageMessage(
        stage=progress.stage.value,
        status=progress.status.value,
        message=progress.message,
        data=dict(progress.data),
    )


def _result_frame(result: PreparationResult) -> PreparationResultMessage:
    return PreparationResultMessage(
        success=result.success,
        mode=result.mode.value,
        message=result.message,
        failed_stage=None if result.failed_stage is None else result.failed_stage.value,
        detail=result.detail,
        data=dict(result.data),
    )


@router.websocket("/ws/prepare")
async def preparation_websocket(websocket: WebSocket) -> None:
    """Prepare the attached ESP32 for one mode session, then close."""
    await websocket.accept()
    sender = _Sender(websocket)
    task: asyncio.Task | None = None
    receiving: asyncio.Future | None = None

    try:
        first = await websocket.receive()
        if first["type"] == "websocket.disconnect":
            return
        try:
            request = _parse(first.get("text"))
        except ValueError as exc:
            await sender.send(PreparationErrorMessage(message=str(exc)))
            await websocket.close()
            return

        async def emit(progress: PreparationProgress) -> None:
            await sender.send(_stage_frame(progress))

        mode = SessionMode(request.mode)
        task = asyncio.create_task(
            mode_preparation.default_preparation_service.prepare(mode, emit=emit),
            name=f"mode-preparation-{mode.value}",
        )
        logger.info("mode preparation requested: %s", mode.value)

        while True:
            receiving = asyncio.ensure_future(websocket.receive())
            await asyncio.wait({receiving, task}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                receiving.cancel()
                await sender.send(_result_frame(task.result()))
                await websocket.close()
                return
            frame = receiving.result()
            if frame["type"] == "websocket.disconnect":
                logger.info("mode preparation abandoned by client: %s", mode.value)
                return
            await sender.send(
                PreparationErrorMessage(message="a preparation is already running on this connection")
            )

    except WebSocketDisconnect:
        pass
    finally:
        if receiving is not None:
            receiving.cancel()
        if task is not None and not task.done():
            task.cancel()
