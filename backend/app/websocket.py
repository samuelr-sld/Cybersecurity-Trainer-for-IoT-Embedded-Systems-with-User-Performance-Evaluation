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

Phase 1 (shared device layer) behaviour: also handle `hardware_status`
requests, which report whether a physical ESP32 is attached to the machine.
Three things about this are load-bearing:

1. IT IS SHARED, NOT OWNED. The answer comes from `app/hardware/`'s
   process-wide `device_monitor` — the very same state Build Mode reads (see
   app/build/service.py::detect_hardware). Hack Mode does not enumerate
   serial ports, does not build an Arduino CLI command line, and does not
   keep a device state of its own; there is one detected board and both
   modes read it. Nothing here caches, second-guesses, or reformats it.

2. IT IS SILENT. A hardware check produces exactly one `hardware` frame and
   nothing else: no terminal output, no `event`, no `state`, no scenario
   mutation, no command dispatch. Device presence is infrastructure state,
   not student activity, so it must never appear in the terminal or the
   Activity Log no matter how often a client polls. See the handler in
   `_handle_message`, and `tests/test_websocket_hardware.py`, which asserts
   this frame-by-frame rather than trusting the comment.

3. THE HACK ENGINE IS UNTOUCHED. The command router, the scenario engine,
   and every simulated tool behave exactly as before.

Phase 2A behaviour: the terminal can now talk to the physical ESP32 over USB
serial. Three things about *that* are load-bearing:

1. IT IS STILL NOT A SHELL. Serial input reaches a UART and nothing else.
   The commands (`serial-status`, `serial-monitor`, `serial-send`,
   `serial-close`) are entries in the same closed allowlist as every
   simulated tool, reached through the same parser that rejects shell
   metacharacters and control characters. Nothing on this path spawns a
   process, evaluates a string, or touches the filesystem — the repo-wide
   static guard in `tests/test_hack_backend.py` still scans every module
   involved, with no new exemption.

2. THE PORT COMES FROM THE SHARED STATE. A student may name the canonical
   `/dev/ttyUSB0` training path, but what gets opened is always the real
   `DeviceState.port` that detection found — resolved by
   `app/hardware/serial_alias.py` before the transport ever sees it.

3. STREAMING IS A SEPARATE TASK, AND SO IS ITS FAILURE. `_pump_serial`
   forwards the board's output as ordinary `output` frames; a disconnected
   board becomes one controlled notice line, never an exception that would
   drop the session. Both the pump and the port are released on teardown.
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
from app.hardware import (
    SerialEventKind,
    SerialTextDecoder,
    device_monitor,
)
from app.models.messages import (
    CLIENT_MESSAGE_ADAPTER,
    ActionMessage,
    ClientMessage,
    ErrorMessage,
    EventMessage,
    HardwareMessage,
    HardwareStatusMessage,
    InputMessage,
    OutputMessage,
    ResizeMessage,
    ServerMessage,
    SessionMessage,
    StateMessage,
)
from app.sessions import HackSession, session_manager

if TYPE_CHECKING:  # pragma: no cover
    from app.hardware import DeviceState
    from app.scenarios import Scenario

logger = logging.getLogger(__name__)

router = APIRouter()

BANNER = (
    "[backend] hack mode channel established\r\n"
    "[backend] controlled command set - nothing runs on the host shell\r\n"
    "[backend] type 'help' to list the available commands\r\n"
)

#: Line terminator written to the terminal. xterm.js is in raw mode, so a
#: bare LF moves down without returning to column 0; CRLF is what a
#: terminal expects. This is a transport-layer concern, which is exactly
#: why `CommandResult.lines` carries lines without terminators.
LINE_ENDING = "\r\n"


#: Dim colour for backend-authored serial *notices* ("connection lost"), so
#: they are visibly distinct from the board's own output, which is always
#: written verbatim and never recoloured.
ANSI_NOTICE = "\x1b[38;2;150;150;150m"
ANSI_RESET = "\x1b[0m"


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


def _hardware_payload(state: "DeviceState") -> dict:
    """The shared device state, with Hack Mode's own board-naming fallback.

    The Arduino CLI often cannot name a classic ESP32 behind a generic
    CP2102/CH340 USB-UART bridge, so the shared layer truthfully reports
    `board=None` rather than inventing one (see `app/hardware/state.py`).
    Each mode then supplies the best name *it* knows: Build Mode uses its
    session's project board (`app/build/service.py::_mirror_device_state`),
    and Hack Mode — which has no project — uses the platform's configured
    label.

    That label is backend configuration (`config.HARDWARE_BOARD_LABEL`),
    never a string the frontend invents, and it is only ever applied to a
    device detection has already *found*: it names a connected board, it
    never claims one is connected.
    """
    payload = state.snapshot()
    if state.connected and not payload["board_name"]:
        payload["board_name"] = config.HARDWARE_BOARD_LABEL
    return payload


class _Channel:
    """One connection's serialised writer.

    WHY A LOCK. Since Phase 2A two independent tasks write to this socket:
    the message loop (answering commands) and the serial pump (forwarding
    whatever the ESP32 says, whenever it says it). Starlette does not
    guarantee that concurrent `send_text` calls stay whole, and interleaved
    fragments would corrupt the JSON frames. One lock per connection makes
    every frame atomic with respect to the other writer.

    Closed sockets are absorbed rather than raised: the pump may still be
    draining a last chunk when a student navigates away, and that must end
    the pump quietly instead of logging a failure for an ordinary disconnect.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: ServerMessage) -> bool:
        """Send one frame. Returns False once the socket can no longer take one."""
        async with self._lock:
            try:
                await self._websocket.send_text(message.model_dump_json())
                return True
            except (WebSocketDisconnect, RuntimeError):
                return False


async def _pump_serial(channel: _Channel, session: HackSession) -> None:
    """Forward the ESP32's serial output to the terminal, for one session.

    ONE LONG-LIVED TASK, STARTED AT CONNECT, PARKED ON A QUEUE. It does not
    open a port and does not poll: until a serial command opens the link the
    queue is simply empty and this task is suspended, costing nothing. That
    is what satisfies "do not open serial unless required" while still
    having a reader ready the instant a student runs `serial-monitor`.

    Data is decoded through one `SerialTextDecoder` for the life of the
    connection, so a UTF-8 character or a CRLF split across two serial reads
    is reassembled rather than mangled.

    Lifecycle events become one short, controlled line each. An unplugged
    board therefore prints "serial connection to COM3 lost" and the terminal
    stays usable — the WebSocket is untouched, every other command still
    works, and re-running `serial-monitor` after plugging the board back in
    reconnects.

    Cancelled on disconnect (see the endpoint's teardown). It deliberately
    catches nothing but cancellation around the loop body's own send: any
    other failure here is a bug worth surfacing in the log rather than
    silently swallowing.
    """
    decoder = SerialTextDecoder()
    while True:
        event = await session.serial.events.get()

        if event.kind is SerialEventKind.DATA:
            text = decoder.feed(event.data)
            if text and not await channel.send(OutputMessage(data=text)):
                return
            continue

        # ERROR / CLOSED — infrastructure facts about the link, rendered as
        # one terminal line. Deliberately not scenario events: a board being
        # unplugged is not something the student did in the exercise.
        if event.message:
            line = f"{ANSI_NOTICE}[serial] {event.message}{ANSI_RESET}{LINE_ENDING}"
            if not await channel.send(OutputMessage(data=line)):
                return


async def _handle_message(
    channel: _Channel, session: HackSession, message: ClientMessage
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
            await channel.send(frame)
        return

    if isinstance(message, ResizeMessage):
        # Bounds already enforced by the model; record and acknowledge.
        # No PTY exists yet, so there is nothing further to resize.
        session.resize(message.cols, message.rows)
        await channel.send(
            OutputMessage(
                data=f"[backend] resize accepted ({message.cols}x{message.rows})\r\n"
            )
        )
        return

    if isinstance(message, HardwareStatusMessage):
        # SILENT BY CONSTRUCTION — Phase 1's hard requirement, and the reason
        # this branch is four lines with nothing else in them.
        #
        # It asks the SHARED device layer (`app/hardware/`) — the same
        # `device_monitor` Build Mode reads, so both modes report one board —
        # and replies with one `hardware` frame. That is the complete list of
        # effects. In particular this handler deliberately does NOT:
        #
        #   * write to the terminal (no `OutputMessage`, so nothing reaches
        #     xterm.js: no "ESP32 CONNECTED", no "NO ESP32 DETECTED", no
        #     line of any kind)
        #   * emit an `EventMessage` (nothing appears in the Activity Log)
        #   * emit a `StateMessage` or touch `session.scenario` in any way
        #     (no scenario state changes, no exploit/discovery flags move)
        #   * reach `default_router` (this is not a command; it can never be
        #     mistaken for something the student typed)
        #
        # A frontend polling this every few seconds therefore changes exactly
        # one thing on screen: the small hardware-status indicator. Plugging
        # a board in is not something a student *did*, so it must never be
        # logged as activity or narrated into their terminal.
        state = await device_monitor.refresh()
        await channel.send(HardwareMessage(data=_hardware_payload(state)))
        return

    # Unreachable while ClientMessage is a closed union; kept so that adding a
    # member without a handler fails loudly instead of silently doing nothing.
    await channel.send(ErrorMessage(message="unsupported message type"))


@router.websocket("/ws/hack")
async def hack_websocket(websocket: WebSocket) -> None:
    """Serve one Hack Mode terminal session."""
    await websocket.accept()
    session = await session_manager.create()
    channel = _Channel(websocket)
    # Started here, but it opens nothing: it parks on an empty queue until a
    # serial command connects the board. See `_pump_serial`.
    pump = asyncio.create_task(
        _pump_serial(channel, session), name=f"serial-pump-{session.session_id}"
    )
    logger.info("hack session opened: %s", session.session_id)

    try:
        await channel.send(SessionMessage(session_id=session.session_id))
        await channel.send(OutputMessage(data=BANNER))

        while True:
            frame = await websocket.receive()
            if frame["type"] == "websocket.disconnect":
                break

            raw = frame.get("text")
            if raw is None:
                # Binary frames are not part of the protocol; refuse rather
                # than guessing at an encoding.
                await channel.send(
                    ErrorMessage(message="binary frames are not supported")
                )
                continue

            try:
                message = _parse(raw)
            except ValueError as exc:
                await channel.send(ErrorMessage(message=str(exc)))
                continue

            await _handle_message(channel, session, message)

    except WebSocketDisconnect:
        pass
    finally:
        # NOTHING HERE MAY AWAIT. The server cancels this task when the
        # student disconnects, so by the time this `finally` runs there is a
        # pending cancellation and the *first* `await` — any await, even one
        # acquiring an uncontended lock — raises `CancelledError` and
        # abandons the rest of the cleanup. An earlier version awaited here
        # and, as a result, never closed the serial port or removed the
        # session at all: both leaked on every real disconnect, and the held
        # port then blocked Build Mode from flashing that board.
        #
        # So teardown is three synchronous calls that cannot be interrupted.
        # `pump.cancel()` only requests cancellation (the task ends on the
        # next loop pass, writing nothing — `_Channel.send` absorbs a closed
        # socket), `release()` closes the port inline, and `discard()`
        # drops the registry entry without taking the async lock. See
        # `SerialTransport.release` and `SessionManager.discard`.
        pump.cancel()
        session.serial.release()
        session_manager.discard(session.session_id)
        logger.info("hack session closed: %s", session.session_id)
