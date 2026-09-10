"""Build Mode WebSocket endpoint (`/ws/build`).

Its own channel, session manager, and message protocol — not Hack Mode
commands. Position in the pipeline:

    Student -> Build Mode UI -> Build WebSocket (this module) -> Build Service
                                                                    -> Build Workspace

SECURITY BOUNDARY — same standing rule as `app/websocket.py`. Submitted
region source is untrusted text. This module itself transports and
validates messages; it never parses, compiles, or executes anything —
`app/build/compiler.py` is the one place two layers down that runs a real
compiler, and only ever against a backend-materialized copy of the
session's own workspace, never against a client-supplied string. There is
no `subprocess`, `eval`/`exec`, or shell invocation anywhere in *this*
module, and a submitted edit can only ever replace one named EDITABLE
region — see `app/build/workspace.py` for the enforcement.

Phase 3A behaviour: accept the connection, create an isolated session with
its default workspace already loaded (the LED Blink pipeline-proof project
as of Phase 1 — see `app/build/blink.py`), announce the session id, emit
the session's bootstrap events, send one `state` snapshot, then handle
`edit_region` requests — validating the region via the Build Service,
emitting the resulting event(s), and sending a refreshed `state` snapshot.

Phase 3B behaviour: also handle `compile` requests. This module still has no
scenario- or compiler-specific knowledge of its own — it hands the request
to `BuildService.compile_workspace` exactly like any other action and
renders whatever `BuildActionResult` comes back. The one difference from a
short action is timing, not knowledge: a real compile takes ~60s, so this
module also passes a progress sink (`_progress_sink`) that the service
awaits to announce `compile_started` and a `running` snapshot immediately,
instead of the client seeing an idle socket until the compiler exits. The
real subprocess lives two layers down, in `app/build/compiler.py`; this
module never touches it.

Phase 3C behaviour: also handle `flash` requests, the same way — one call to
`BuildService.flash_workspace`, no knowledge here of serial ports, boards,
or upload commands. The `flash` frame carries no fields at all, so there is
nothing on this wire that could name a device to write firmware to, a binary
to write, or an argument to pass; the backend chooses every one of those
(see `app/build/flasher.py`). Validate/security-test requests are still not
part of this protocol; the frontend's controls for them remain disabled
placeholders.

Protocol version 4 behaviour: also handle `hardware_status` requests — a
read-only re-run of the same real device discovery `flash` already performs
before uploading, via `BuildService.detect_hardware`. It never uploads
anything and never emits a `BuildEvent`, so a client polling it (as the
frontend does, to keep the Build Mode header's BOARD/PORT/LINK truthful
without the student pressing FLASH) only ever produces a fresh `state`
frame, never Activity Log noise.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError

from app import config
from app.build.events import BuildEvent
from app.build.service import BuildActionResult, default_service
from app.build_sessions import BuildSession, build_session_manager
from app.models.build_messages import (
    BUILD_CLIENT_MESSAGE_ADAPTER,
    BuildClientMessage,
    BuildErrorMessage,
    BuildEventMessage,
    BuildServerMessage,
    BuildSessionMessage,
    BuildStateMessage,
    CompileMessage,
    EditRegionMessage,
    FlashMessage,
    HardwareStatusMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def send(websocket: WebSocket, message: BuildServerMessage) -> None:
    """Serialise and send one server frame."""
    await websocket.send_text(message.model_dump_json())


def _parse(raw: str) -> BuildClientMessage:
    """Validate one raw text frame into a typed client message.

    Raises ValueError with a short, non-reflecting reason on bad input, the
    same discipline `app/websocket.py::_parse` follows for Hack Mode.
    """
    if len(raw) > config.MAX_BUILD_MESSAGE_CHARS:
        raise ValueError("message exceeds maximum size")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    try:
        return BUILD_CLIENT_MESSAGE_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValueError("message does not match the protocol schema") from exc


async def _render_result(websocket: WebSocket, session: BuildSession, result: BuildActionResult) -> None:
    """Turn one `BuildActionResult` into the frames that express it.

    A failed action gets a single `error` frame and nothing else — no state
    changed, so there is nothing to resend. A successful action gets one
    `event` frame per event it caused, followed by a fresh `state` snapshot;
    every Phase 3A action that can succeed changes at least one thing worth
    a client re-render, so — unlike Hack Mode's `state`, which is gated on
    non-empty events — this is unconditional on success.
    """
    if not result.success:
        await send(websocket, BuildErrorMessage(message=result.error or "request rejected"))
        return
    for event in result.events:
        await send(websocket, BuildEventMessage(event=event.type.value, data=dict(event.data)))
    await send(websocket, BuildStateMessage(data=session.snapshot()))


def _progress_sink(websocket: WebSocket, session: BuildSession):
    """Render one event, plus a refreshed snapshot, the instant it happens.

    `_render_result` below can only speak once the whole action has
    returned. That is fine for an edit, but a real compile spends ~60s
    inside `arduino-cli`, and for that whole time the socket carried
    nothing — so the UI could not show `compile_status: running`, could not
    disable its Compile button, and looked hung. This sink is what
    `BuildService.compile_workspace` awaits to announce `compile_started`
    up front; the service stays transport-agnostic and simply does not
    repeat an event it already handed over (see its `emit` parameter).

    The state frame matters as much as the event: the frontend derives
    "compiling" from `state.compile_status`, not from the event log.
    """

    async def emit(event: BuildEvent) -> None:
        await send(websocket, BuildEventMessage(event=event.type.value, data=dict(event.data)))
        await send(websocket, BuildStateMessage(data=session.snapshot()))

    return emit


@router.websocket("/ws/build")
async def build_websocket(websocket: WebSocket) -> None:
    """Serve one Build Mode session."""
    await websocket.accept()
    session = await build_session_manager.create()
    logger.info("build session opened: %s", session.session_id)

    try:
        await send(websocket, BuildSessionMessage(session_id=session.session_id))
        start_result = await default_service.start_session(session)
        await _render_result(websocket, session, start_result)

        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                break

            raw = frame.get("text")
            if raw is None:
                await send(
                    websocket, BuildErrorMessage(message="binary frames are not supported")
                )
                continue

            try:
                message = _parse(raw)
            except ValueError as exc:
                await send(websocket, BuildErrorMessage(message=str(exc)))
                continue

            if isinstance(message, EditRegionMessage):
                result = await default_service.edit_region(
                    session, message.path, message.region_id, message.source
                )
            elif isinstance(message, CompileMessage):
                result = await default_service.compile_workspace(
                    session, emit=_progress_sink(websocket, session)
                )
            elif isinstance(message, FlashMessage):
                result = await default_service.flash_workspace(session)
            elif isinstance(message, HardwareStatusMessage):
                result = await default_service.detect_hardware(session)
            else:
                # Unreachable while BuildClientMessage is a closed union;
                # kept so that adding a member without a handler fails
                # loudly instead of silently doing nothing — the same
                # discipline app/websocket.py follows for Hack Mode.
                await send(websocket, BuildErrorMessage(message="unsupported message type"))
                continue

            await _render_result(websocket, session, result)

    except WebSocketDisconnect:
        pass
    finally:
        await default_service.end_session(session)
        await build_session_manager.remove(session.session_id)
        logger.info("build session closed: %s", session.session_id)
