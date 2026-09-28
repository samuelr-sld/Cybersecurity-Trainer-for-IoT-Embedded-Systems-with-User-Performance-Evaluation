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
its workspace already loaded — the attached panel's own firmware when one
resolves (`app/build_project_selection.py`), and otherwise an inert
no-activity placeholder (`app/build/no_device.py`; see the no-device
correction below) — announce the session id, emit the session's bootstrap
events, send one `state` snapshot, then handle `edit_region` requests —
validating the region via the Build Service, emitting the resulting event(s),
and sending a refreshed `state` snapshot.

NO-DEVICE CORRECTION. A connection whose panel does not resolve to a real
package — no board, an unidentified/unregistered board, a registered panel
with no courseware, or firmware B2 could not materialize — no longer loads
the LED Blink pipeline-proof project. `select_build_project()` returns a
`BuildProjectSelection` whose `has_active_project` is False for every one of
those cases, carried onto `BuildSession.has_active_project` here; the session
still exists (hardware polling keeps working) but `BuildService` refuses every
edit/compile/flash/validate request against it, and the `state` frame's
`has_active_project: false` is what `BuildMode.jsx` reads to render its
no-device empty state instead of an IDE. LED Blink remains reachable only by
a caller that constructs a `BuildSession`/`BuildWorkspace` directly (tests,
`create_default_workspace()`'s own dataclass default) — never through this
connection path.

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
(see `app/build/flasher.py`).

Phase B7 behaviour: also handle `validate` requests, the same way again —
one call to `BuildService.validate_workspace`, no knowledge here of what any
panel's remediation is or how it would be checked. The `validate` frame is
field-less too, which matters more here than anywhere else on this socket: a
client cannot assert its own verdict, name what to validate, or supply
evidence. The service refuses the request unless this session's own
workspace was successfully compiled AND successfully flashed, and the
verdict comes from a backend validator. No panel shipped today has an
executable check, so an ordinary `validate` is refused with that reason —
see `app/build/validation/`.

Phase B8 behaviour (protocol version 6): also handle `section_blockly` and
`edit_section_blocks` — the section -> Blockly contract Build Mode's actual
interaction model runs on. `section_blockly` is a READ: it returns one
discovered section's blocks in a `section` frame, emits no event, changes no
status and sends no new `state`, because opening a section to look at it is not
a remediation attempt. `edit_section_blocks` is the INTENDED editing path and
is rendered exactly like an `edit_region`: the same events, the same refreshed
snapshot. This module gains no knowledge of Blockly from either — it transports
a workspace it never interprets, and `app/build/workspace.py` refuses the write
outright if the named section is not one the project's policy opens.

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
from app.build.service import BuildActionResult, default_service, end_session_sync
from app.build_project_selection import select_build_project
from app.build_provisioning_selection import select_build_provisioning
from app.build_sessions import BuildSession, build_session_manager
from app.build_validation_selection import select_build_validation
from app.participants import resolve_participant
from app.models.build_messages import (
    BUILD_CLIENT_MESSAGE_ADAPTER,
    BuildClientMessage,
    BuildErrorMessage,
    BuildEventMessage,
    BuildSectionMessage,
    BuildServerMessage,
    BuildSessionMessage,
    BuildStateMessage,
    CompileMessage,
    EditRegionMessage,
    EditSectionBlocksMessage,
    FlashMessage,
    HardwareStatusMessage,
    SectionBlocklyMessage,
    ValidateMessage,
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
    if result.data is not None:
        # A READ (B8's `section_blockly`). It changed nothing, so there is no
        # event to render and nothing in the snapshot moved — resending one
        # would tell a client to re-render its whole workspace because a
        # student clicked a section to look at it.
        await send(websocket, BuildSectionMessage(data=result.data))
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
    # PHASE 2E.3 / B2 — a passive read of the shared panel-resolution layer
    # (`app/build_panel_resolution.py`), mirroring Hack Mode's
    # `select_session_scenario()` call in `app/websocket.py`. It reads the
    # device monitor's current state (no `arduino-cli`, no MAC probe, no
    # port opened) and never fails the connection.
    #
    # B2 extends what that one resolution is used for: as well as the
    # `panel_id` recorded on the session, it now selects the PROJECT the
    # session loads — the attached panel's real firmware, materialized
    # through B1 structural discovery, or the default LED Blink project when
    # no panel resolves. Both come off the single `BuildProjectSelection`, so
    # the chain is resolved once per connection, and an unresolved panel
    # still simply leaves `panel_id` as the honest `None` it already
    # defaults to. The wire protocol is unchanged — this sends no frame of
    # its own, and the `state` snapshot keeps exactly its existing shape.
    # B7 takes a third thing off that same single resolution: the session's
    # validator. `select_build_validation` reads the already-loaded package
    # and builds a `ValidationPlan` — no device call, no file read, no second
    # walk of the MAC -> panel -> package chain, and choosing a validator
    # never runs one. Option A takes a fourth: who provisions this session's
    # compile-time firmware copy with real, non-committed credentials
    # (`app/build_provisioning_selection.py`), off the same resolution and
    # with the same "choosing never runs one" guarantee.
    selection = select_build_project()
    session = await build_session_manager.create(
        panel_id=selection.panel_id,
        workspace=selection.workspace,
        validation=select_build_validation(selection),
        provisioning=select_build_provisioning(selection),
        has_active_project=selection.has_active_project,
        # Evaluation: the registered participant this session belongs to, or
        # None — see `app/participants.py`. Never refuses the connection.
        participant_id=resolve_participant(websocket.query_params.get("participant")),
    )
    logger.info("build session opened: %s [%s]", session.session_id, selection.describe())

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

            if isinstance(message, SectionBlocklyMessage):
                # B8's read leg. No knowledge here of Blockly, sections or
                # C++ — one service call, exactly like every other action.
                result = await default_service.read_section_blockly(
                    session, message.path, message.section_id
                )
            elif isinstance(message, EditSectionBlocksMessage):
                # B8's write leg, and Build Mode's INTENDED editing path. The
                # frame carries blocks, not source: this module transports them
                # and nothing more, and the backend regenerates the firmware
                # from what they mean (see `app/build/workspace.py`).
                result = await default_service.edit_section_blocks(
                    session,
                    message.path,
                    message.section_id,
                    message.workspace,
                    message.preserved,
                )
            elif isinstance(message, EditRegionMessage):
                result = await default_service.edit_region(
                    session, message.path, message.region_id, message.source
                )
            elif isinstance(message, CompileMessage):
                result = await default_service.compile_workspace(
                    session, emit=_progress_sink(websocket, session)
                )
            elif isinstance(message, FlashMessage):
                result = await default_service.flash_workspace(session)
            elif isinstance(message, ValidateMessage):
                # Same shape as `compile`: one service call, a progress sink
                # so a check with real duration announces itself when it
                # starts, and no knowledge here of what validating means.
                # The service refuses outright unless this session's own
                # workspace was successfully flashed, so nothing on this
                # socket can turn a compile or a flash into a verdict.
                result = await default_service.validate_workspace(
                    session, emit=_progress_sink(websocket, session)
                )
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
        # NOTHING HERE MAY AWAIT. Mirrors `app/websocket.py`'s Hack Mode
        # teardown / `SessionManager.discard`: the server can cancel this
        # task on a hard disconnect, and this `finally` then runs with a
        # cancellation already delivered — the *first* `await` is not safe
        # to depend on completing, even one with nothing to suspend on
        # today, because that could change without anyone noticing the
        # teardown had become unsafe. `end_session_sync` and `discard` are
        # therefore plain synchronous calls, not the awaited
        # `end_session`/`remove` this used to call.
        end_session_sync(session)
        build_session_manager.discard(session.session_id)
        logger.info("build session closed: %s", session.session_id)
