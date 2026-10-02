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

The one deliberate exception is while a pager is active (see "GENERIC OUTPUT
PAGER" below): there the frontend forwards one raw keystroke per `input`
frame instead of a buffered line, because a pager must react to Space/q the
instant they're pressed, not after Enter. Even then this endpoint does not
maintain a line buffer of its own — a keystroke is classified and consumed
immediately by `_advance_pager`/`_exit_pager`, never accumulated.

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

Phase 2D.4 behaviour: the session created at connect now runs the experiment
the ATTACHED PANEL declares, instead of always the default one. The decision
is made in `app/scenario_selection.py` (MAC -> panel -> package ->
`scenario_id` -> `Scenario`) and injected into `SessionManager.create`, so
this endpoint gained one call and one argument and nothing else:

1. THE PROTOCOL IS UNCHANGED. Selection sends no frame, adds no field, emits
   no `event` or `state`, prints nothing to the terminal, and dispatches no
   command. A client cannot tell selection happened; the banner, the session
   frame and every subsequent frame are byte-for-byte what they were.

2. IT IS A LOOKUP, NOT AN ACTION. It reads the state the shared
   `device_monitor` already holds — it does not detect, so no `arduino-cli`
   runs and no MAC is probed (probing resets the board, which must never be
   a side effect of opening a terminal). It opens no serial port, connects to
   no broker, compiles, flashes and provisions nothing, and starts no
   experiment: the scenario is constructed, exactly as it always was, and
   sits waiting for the student's first command.

3. IT CANNOT FAIL THE CONNECTION. No board, an unidentified board, an
   unregistered board, a panel without courseware, a broken package, or a
   package naming an unimplemented scenario id all yield the long-standing
   default scenario. The reason goes to the log, never to the student.

GENERIC OUTPUT PAGER (protocol v6, `app/pager.py`). A command whose
`CommandResult.pageable` is set (today, only `strings` — see
`app/commands/handlers/strings.py`) has its `lines` paged when they don't
fit the student's terminal in one screen, instead of dumping the whole
result in a single `output` frame. Three things about this are load-bearing:

1. IT IS A TRANSPORT CONCERN, NOT A COMMAND-ROUTER ONE. Whether paging
   happens at all depends on the session's terminal height, which only this
   module and `app/sessions.py` know about — `CommandRouter.dispatch` still
   returns every line of a command's real output, unpaginated, exactly as
   before (`tests/test_strings_min_length.py` calls it directly and asserts
   that). `_render`, below, is the one place that decides whether a pageable
   result needs more than one page and, if so, builds the first page and
   parks a `Pager` on `session.pager` instead of sending the rest.

2. AN ACTIVE PAGER OWNS THE SESSION'S NEXT `input` FRAMES. While
   `session.pager` is set, `_handle_message` routes every `InputMessage` to
   `_advance_pager`/`_exit_pager` instead of `default_router.dispatch` — a
   keystroke typed to page through `strings` output can never be mistaken
   for a command line, and the command router never sees one. This is why
   paging cannot weaken the "not a shell" boundary: nothing new reaches
   `default_router`, and the pager itself runs no command and touches no
   scenario.

3. THE PAGE SIZE TRACKS THE REAL TERMINAL. `session.rows` already came from
   the existing `resize` message (see `HackSession.resize`) — no new client
   message was needed for terminal geometry. A `resize` that arrives while a
   pager is active updates that pager's height too, so a page taken after a
   resize reflects the new size; a page already sent is not reflowed.

4. STARTING A PAGER IS ANNOUNCED BEFORE ITS FIRST PAGE. `_render` sends the
   `pager_start` action *before* the `output` frame carrying the first page,
   not after — see that function's docstring for why the order matters to
   the frontend's own (unsent) shell prompt. `_advance_pager`/`_exit_pager`
   keep the opposite order (output, then `pager_end`) on purpose: by the
   time either of those runs, the frontend has already decided not to prompt
   after that output (`session.pager` was still set when it arrived), so
   `pager_end` alone is what tells it to prompt again — sending `pager_end`
   first would make both that action *and* the output's own "not paging
   anymore" check fire the prompt, drawing it twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Sequence

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError

from app import config
from app.commands import CommandContext, CommandResult, TerminalAction, default_router
from app.events import to_iso
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
from app.hack_briefing import briefing_for
from app.hack_live_mqtt import configure_live_mqtt
from app.pager import PAGER_PROMPT, Pager, PagerAction
from app.participants import resolve_participant
from app.scenario_selection import select_session_scenario
from app.session_panel_guard import resume_panel_matches
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


def _join_lines(lines: "Sequence[str]") -> str:
    """Join terminal lines the one way this module ever writes them."""
    return "".join(line + LINE_ENDING for line in lines)


def _render(
    result: CommandResult, scenario: "Scenario", session: HackSession
) -> list[ServerMessage]:
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

    PAGING. `result.pageable` results are only actually paged when they
    don't fit the session's current terminal in one page — see the module
    docstring's "GENERIC OUTPUT PAGER" section. When they don't, this
    function sends a `pager_start` action *before* the first page's `output`
    frame — the one exception to "actions before output" being about
    `result.actions` specifically — and parks a fresh `Pager` on
    `session.pager` (the one side effect this function has beyond building
    frames) so the next `input` frame reaches it instead of the command
    router. A pageable result that already fits one page takes the plain
    branch below and produces a frame byte-for-byte identical to what a
    non-pageable result would.

    That action-before-output order is load-bearing, not cosmetic: the
    terminal's own shell prompt (drawn by the frontend, never sent over the
    wire — see src/screens/HackMode.jsx) is shown after any `output` frame
    unless a pager is active. Frames from one WebSocket message are handled
    one at a time, in order, so sending `pager_start` first is what lets the
    frontend learn "a pager just started" *before* it decides whether that
    first page's `output` frame should be followed by a prompt — no client
    timing guess needed. Sending it after, as this used to, left a window
    where the frontend couldn't yet tell the two cases apart.
    """
    frames: list[ServerMessage] = [
        ActionMessage(action=action.value) for action in result.actions
    ]
    if result.lines:
        if result.pageable:
            pager = Pager(lines=result.lines, terminal_rows=session.rows)
            first_page = pager.next_page()
            if pager.has_more:
                session.pager = pager
                frames.append(ActionMessage(action=TerminalAction.PAGER_START.value))
                frames.append(OutputMessage(data=_join_lines(first_page) + PAGER_PROMPT))
            else:
                # Fits one page: identical to the non-pageable branch below —
                # `first_page` is every line, taken in one slice.
                frames.append(OutputMessage(data=_join_lines(first_page)))
        else:
            frames.append(OutputMessage(data=_join_lines(result.lines)))
    # PHASE 2B: built from `result.records` — the rows the recorder just
    # wrote — rather than from the raw `result.events`. Same events in the
    # same order, but each now carries the server timestamp and sequence
    # number the durable log holds, so the Activity Log on screen and the
    # evidence in the database are literally the same rows. `records` is
    # empty only for a result that never went through the router, so
    # `events` remains the fallback and no event can be dropped.
    for record in result.records:
        frames.append(
            EventMessage(
                event=record.event_type,
                data=dict(record.data),
                occurred_at=to_iso(record.occurred_at),
                sequence=record.sequence,
            )
        )
    if not result.records:
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


async def _advance_pager(channel: _Channel, session: HackSession) -> None:
    """Handle an Enter/Space keystroke that reached an active pager.

    Sends the next page. A leading `LINE_ENDING` moves the cursor off the
    previous prompt line (which was deliberately sent with no trailing
    newline of its own — see `_render`), so page transitions read as the
    terminal scrolling forward rather than text piling up mid-line. When
    that page is the last one, the pager is cleared here (before the frame
    is even sent) and a `pager_end` action follows, which is what lets a
    student who has just paged to the end land straight back in normal
    command-entry mode with no extra keystroke.
    """
    pager = session.pager
    assert pager is not None
    page = pager.next_page()
    if pager.has_more:
        await channel.send(
            OutputMessage(data=LINE_ENDING + _join_lines(page) + PAGER_PROMPT)
        )
        return
    session.pager = None
    await channel.send(OutputMessage(data=LINE_ENDING + _join_lines(page)))
    await channel.send(ActionMessage(action=TerminalAction.PAGER_END.value))


async def _exit_pager(channel: _Channel, session: HackSession) -> None:
    """Handle a q/Ctrl+C keystroke that ended an active pager early.

    No further page text is sent — real `less`/`more` print nothing extra on
    quit either. Only a line ending, to move the cursor off the prompt
    line, and the `pager_end` action that restores normal input handling.
    """
    session.pager = None
    await channel.send(OutputMessage(data=LINE_ENDING))
    await channel.send(ActionMessage(action=TerminalAction.PAGER_END.value))


async def _handle_message(
    channel: _Channel, session: HackSession, message: ClientMessage
) -> None:
    """Dispatch one validated client message."""
    if isinstance(message, InputMessage):
        if session.pager is not None:
            # A pager is holding this session's terminal input hostage (see
            # the module docstring's "GENERIC OUTPUT PAGER" section): this
            # frame is one raw keystroke the frontend forwarded instead of a
            # buffered command line, and it must never reach
            # `default_router.dispatch` — that is the whole point of routing
            # it here first. An unrecognised keystroke (PagerAction.IGNORE)
            # produces no frame at all: the pager keeps waiting.
            pager_action = PagerAction.from_input(message.data)
            if pager_action is PagerAction.ADVANCE:
                await _advance_pager(channel, session)
            elif pager_action is PagerAction.EXIT:
                await _exit_pager(channel, session)
            return

        # One completed command line. The router resolves it against a closed
        # command table and returns a structured result; it never touches
        # this socket, and nothing in it is executed.
        #
        # `dispatch` contains its own failures, so an unsupported or broken
        # command yields terminal text rather than an exception that would
        # drop the student's session.
        context = CommandContext(session=session)
        result = await default_router.dispatch(message.data, context)
        for frame in _render(result, context.scenario, session):
            await channel.send(frame)
        return

    if isinstance(message, ResizeMessage):
        # Bounds already enforced by the model; record and acknowledge.
        # No PTY exists yet, so there is nothing further to resize.
        session.resize(message.cols, message.rows)
        if session.pager is not None:
            # Only future pages are affected — see `Pager.resize`.
            session.pager.resize(message.rows)
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


#: Frames a session may have waiting behind a running command. The frontend
#: sends one `hardware_status` every 10s, so this covers a command running
#: for minutes; beyond it a frame is refused (with an `error`), never
#: buffered without bound.
MAX_PENDING_FRAMES = 64


async def _process_frames(
    channel: _Channel, session: HackSession, frames: "asyncio.Queue[str | None]"
) -> None:
    """Handle one session's frames, strictly in order, one at a time.

    WHY THIS IS A SEPARATE TASK FROM RECEIVING. It used to be the body of
    the endpoint's own `receive()` loop, which meant nothing called
    `receive()` while a command ran. uvicorn's WebSocket protocol pauses
    reading the socket as soon as one frame is waiting for the app, and
    resumes only on the app's next `receive()` — so during any long command
    (a real 4 MiB `esptool.py read_flash` takes minutes) the first 10-second
    `hardware_status` poll froze the socket, the browser's replies to
    uvicorn's keepalive pings went unread, and uvicorn closed the connection
    with 1011 about 40s in. The command's result was then written into a
    dead socket, and the student's terminal simply never answered again.

    The endpoint now keeps receiving (so pings and a disconnect are always
    seen) and hands each raw frame here. This loop is the old loop body,
    unchanged: frames are still parsed and handled in arrival order, and a
    session still runs exactly one command at a time.
    """
    while True:
        raw = await frames.get()
        if raw is None:
            # Binary frames are not part of the protocol; refuse rather
            # than guessing at an encoding.
            await channel.send(ErrorMessage(message="binary frames are not supported"))
            continue

        try:
            message = _parse(raw)
        except ValueError as exc:
            await channel.send(ErrorMessage(message=str(exc)))
            continue

        await _handle_message(channel, session, message)


async def _replay_session(channel: _Channel, session: HackSession) -> None:
    """Re-send what a re-attached page cannot have: the session's own record.

    Read straight from the recorder and the scenario — the same rows the
    database holds — so nothing is reconstructed or invented. The terminal's
    scrollback is not replayed (xterm.js owns that and a reloaded page starts
    it empty); the session, its events and its state are what matter.
    """
    await channel.send(
        OutputMessage(data="[backend] session resumed - your previous activity is preserved\r\n")
    )
    for record in session.recorder.events:
        await channel.send(
            EventMessage(
                event=record.event_type,
                data=dict(record.data),
                occurred_at=to_iso(record.occurred_at),
                sequence=record.sequence,
            )
        )
    await channel.send(StateMessage(data=session.scenario.snapshot()))


@router.websocket("/ws/hack")
async def hack_websocket(websocket: WebSocket) -> None:
    """Serve one Hack Mode terminal session."""
    await websocket.accept()
    # PHASE 2D.4 — the one lifecycle change. Before creating the session, ask
    # which experiment the attached panel provides: its MAC identifies a
    # registered panel, that panel's package declares a `scenario_id`, and the
    # scenario registry turns that id into a fresh `Scenario`. The whole chain
    # lives in `app/scenario_selection.py`; this endpoint only calls it and
    # passes the result on.
    #
    # It is a lookup, not an action: it reads the device state the shared
    # monitor already holds (no `arduino-cli`, no MAC probe, no port opened),
    # reads a trusted JSON manifest, and constructs an in-memory object.
    # Nothing is compiled, flashed, provisioned or started, no MQTT client
    # exists, no `ScenarioEvent` is emitted, and no frame is sent — the wire
    # protocol below is byte-for-byte what it was.
    #
    # It also never fails: with no board attached — the ordinary development
    # flow — or with a board whose panel, package or scenario id cannot be
    # resolved, the selection is the long-standing default scenario, and the
    # reason is logged rather than shown to the student.
    # RELOAD-SAFE SESSIONS. A client that already has a session (it asked
    # `?session=<id>` after a page reload) is re-attached to it instead of
    # getting a new one: same scenario, same recorder, same telemetry. The
    # lookup is a dict read; anything that cannot be honoured (unknown or
    # ended id, someone else's session) falls through to the normal new-session
    # path below, so a stale id can never corrupt or block a start.
    participant_id = resolve_participant(websocket.query_params.get("participant"))
    session = session_manager.resume(websocket.query_params.get("session"), participant_id)
    if session is not None and not resume_panel_matches(session_manager.panel_of(session.session_id)):
        session = None  # the session's panel is not the attached one: start normally
    resumed = session is not None
    if session is None:
        selection = select_session_scenario()
        # If the attached panel declares a machine-checkable authorization
        # criterion and the lab credentials are provisioned, the session's
        # `mosquitto_pub`/`mosquitto_sub` act over the REAL training broker
        # instead of the in-memory simulation. Still a lookup + in-memory
        # wiring; it opens NOTHING here and never raises.
        configure_live_mqtt(selection)
        # What the screen shows about this scenario, built once from the
        # package that named it and stored with the session so a reload is
        # told the same thing. Pure data: a function of the package, no I/O.
        session = await session_manager.create(
            scenario=selection.scenario,
            participant_id=participant_id,
            panel_id=selection.panel_id,
            briefing=briefing_for(selection.package),
        )
        described = selection.describe()
    else:
        described = "resumed"
    # The connection now serving this session. Its token is how the teardown
    # below knows whether it is still the one entitled to detach it.
    token = session_manager.claim(session.session_id)
    channel = _Channel(websocket)
    # Started here, but it opens nothing: it parks on an empty queue until a
    # serial command connects the board. See `_pump_serial`.
    pump = asyncio.create_task(
        _pump_serial(channel, session), name=f"serial-pump-{session.session_id}"
    )
    logger.info(
        "hack session %s: %s [%s]",
        "resumed" if resumed else "opened",
        session.session_id,
        described,
    )

    # See `_process_frames` for why receiving and handling are two tasks.
    frames: asyncio.Queue[str | None] = asyncio.Queue()
    worker = asyncio.create_task(
        _process_frames(channel, session, frames),
        name=f"hack-worker-{session.session_id}",
    )
    receiving: asyncio.Future | None = None

    try:
        # Protocol v7: the session frame itself carries the scenario briefing
        # and the target's state at attach, so the page is correct before the
        # first command and never waits for a first event to learn what it is
        # looking at. Both are reads — nothing is run, recorded or emitted.
        await channel.send(
            SessionMessage(
                session_id=session.session_id,
                resumed=resumed,
                scenario=session_manager.briefing_of(session.session_id),
                state=session.scenario.snapshot(),
            )
        )
        if resumed:
            await _replay_session(channel, session)
        else:
            await channel.send(OutputMessage(data=BANNER))

        while True:
            receiving = asyncio.ensure_future(websocket.receive())
            await asyncio.wait({receiving, worker}, return_when=asyncio.FIRST_COMPLETED)
            if not receiving.done():
                # The worker ended, which it only does by raising: surface
                # that exactly as the old inline loop would have.
                receiving.cancel()
                worker.result()
                break

            frame = receiving.result()
            if frame["type"] == "websocket.disconnect":
                break

            if frames.qsize() >= MAX_PENDING_FRAMES:
                await channel.send(
                    ErrorMessage(message="too many pending messages; wait for the running command")
                )
                continue
            frames.put_nowait(frame.get("text"))

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
        # So teardown is synchronous calls that cannot be interrupted.
        #
        # A DISCONNECT IS NOT THE END OF THE SESSION. What belongs to this
        # *connection* is stopped: the receive future, the worker (a command
        # still running for a client that has left is killed with its process
        # tree, `app/build/process.py::run_capture`) and the serial pump.
        # `detach` then releases the serial port (so it cannot block Build
        # Mode's flasher) and starts the resume grace period; the session,
        # its scenario and its recorder stay alive for a reloaded page to
        # re-attach to. It is finished by `SessionManager.end` — an explicit
        # end request, or the grace period running out — never by a socket
        # closing. If this connection was superseded by a resume (or the
        # session was ended explicitly), it no longer owns the session and
        # touches nothing of it.
        if receiving is not None:
            receiving.cancel()
        worker.cancel()
        pump.cancel()
        if session_manager.detach(session, token):
            logger.info("hack session detached: %s", session.session_id)
        else:
            logger.info("hack connection closed (session not owned): %s", session.session_id)
