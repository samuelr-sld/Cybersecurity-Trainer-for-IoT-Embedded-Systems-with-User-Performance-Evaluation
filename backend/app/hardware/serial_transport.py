"""Real USB-serial I/O with the attached ESP32.

Position in the pipeline:

    xterm.js <-> Hack WebSocket <-> SerialTransport <-> pyserial <-> ESP32
                 (app/websocket.py)   (this module)

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT. This module moves bytes to and
from one serial port. It does not interpret them, does not execute them, and
cannot run anything on the host: there is no `subprocess`, no shell, no
`eval`/`exec`, and no filesystem access anywhere on this path. A student's
keystrokes reach a UART and nothing else. The repo-wide static guard in
`tests/test_hack_backend.py` scans this file like every other module under
`app/`, with no exemption — `app/build/process.py` remains the only file in
the backend allowed to spawn a process, and this is not it.

IT DOES NOT CHOOSE A PORT. `open()` is given an address that came from the
shared `DeviceState.port` (see `app/hardware/monitor.py`), resolved from
whatever the student named by `app/hardware/serial_alias.py`. This module
never enumerates devices, never guesses, and never sees the canonical
`/dev/ttyUSB0` training alias — by the time an address arrives here it is a
real port this machine reported.

THREADING MODEL — WHY A THREAD, NOT AN ASYNC READ. pyserial is a blocking
library with no asyncio interface. A reader thread parks inside the OS
driver on `read()` with a short timeout and hands whatever arrives back to
the event loop via `call_soon_threadsafe`. That is a genuine block in the
kernel, not a spin: between bytes the thread consumes no CPU, and the
timeout exists only so a stop request is noticed promptly. Writes go through
`asyncio.to_thread` for the same reason, and for consistency with
`app/build/process.py`, which made the same call for the same reason.

The event loop is therefore never blocked by serial I/O, which is the
requirement that matters: one student's silent board cannot stall the
WebSocket, the hardware poller, or anybody else's session.

EVERY FAILURE IS AN EVENT, NOT AN EXCEPTION. A board that is unplugged
mid-stream, a port that another program holds, a device that vanishes during
a write — all of these surface as a `SerialEvent` of kind ERROR or CLOSED,
or as a `SerialTransportError` the command layer turns into one terminal
line. Nothing here is allowed to tear down a student's WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from app import config

logger = logging.getLogger(__name__)


class SerialTransportError(Exception):
    """A serial operation could not be carried out.

    Carries a short, student-safe message built from this module's own
    vocabulary — never a raw driver traceback — because the command layer
    renders it straight into the terminal.
    """


class SerialEventKind(str, Enum):
    """What arrived from the serial side."""

    #: Bytes the device sent.
    DATA = "data"
    #: The link failed while open (board unplugged, driver error). Terminal.
    ERROR = "error"
    #: The port was closed in an orderly way, by request or by teardown.
    CLOSED = "closed"


@dataclass(frozen=True)
class SerialEvent:
    """One thing the serial side produced, for the transport's consumer.

    A single queue carries data and lifecycle together on purpose: it keeps
    "the board said this" and "the board went away" in the order they
    actually happened, so the terminal cannot print output *after* the
    disconnect notice that ended it.
    """

    kind: SerialEventKind
    data: bytes = b""
    message: str = ""


class SerialPort(Protocol):
    """The slice of pyserial's `Serial` this module actually uses.

    Narrow on purpose: it is the whole contract a fake must satisfy, so the
    entire test suite runs with no pyserial and no board, and it documents
    exactly how much of a large library this code depends on.
    """

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


#: How a port is obtained: (address, baud, read timeout) -> SerialPort.
SerialPortFactory = Callable[[str, int, float], SerialPort]


def open_pyserial_port(address: str, baud: int, read_timeout: float) -> SerialPort:
    """Open a real port with pyserial. The only place that library is used.

    Imported lazily so the backend — and the whole test suite — imports and
    runs on a machine with no pyserial installed. A missing library becomes
    one controlled terminal line rather than an import-time crash that would
    take down Hack Mode entirely.

    `write_timeout` is set alongside the read timeout so a board that stops
    draining its input buffer cannot park a worker thread forever.
    """
    try:
        import serial  # noqa: PLC0415 - deliberately deferred; see above
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise SerialTransportError(
            "the serial library is not installed on this backend"
        ) from exc

    try:
        return serial.Serial(
            port=address,
            baudrate=baud,
            timeout=read_timeout,
            write_timeout=config.SERIAL_WRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # pyserial raises SerialException for a busy/absent port, and OSError
        # for some driver-level failures. Both mean the same thing to a
        # student, and neither should carry a traceback into the terminal.
        raise SerialTransportError(_port_failure_message(address, exc)) from exc


def _port_failure_message(address: str, exc: Exception) -> str:
    """One student-readable sentence about why a port would not open.

    Deliberately built from a fixed vocabulary plus the port address this
    backend itself detected — never from the exception's own text, which can
    contain driver internals and is not written for a student.
    """
    detail = str(exc).lower()
    if "permission" in detail or "access is denied" in detail:
        return (
            f"permission denied opening {address}; another program may be "
            "holding the port"
        )
    if "could not open" in detail or "no such" in detail or "cannot find" in detail:
        return f"{address} could not be opened; check the board is still connected"
    return f"{address} could not be opened"


class SerialTextDecoder:
    """Turns serial bytes into terminal-ready CRLF text, across chunks.

    Two jobs, both of which need state that a stateless helper could not
    keep:

    1. A line ending split across two reads. Serial delivers arbitrary
       chunks, so a board's "\\r\\n" routinely arrives as "...\\r" then
       "\\n...". Remembering a trailing CR is what stops that becoming two
       blank lines in the terminal.
    2. A multi-byte UTF-8 character split across two reads. Decoding each
       chunk independently would emit a replacement character for a sequence
       that is perfectly valid once the rest arrives.

    xterm.js is in raw mode, so a bare LF moves down without returning to
    column 0 — every newline must leave as CRLF. A lone CR from the device
    (a progress-bar rewrite, say) is normalised to a newline too: this is a
    log view, not a cursor-addressable display, and letting the board
    reposition the cursor is exactly the control we are not handing over.
    """

    def __init__(self) -> None:
        self._pending_cr = False
        self._decoder = _incremental_utf8_decoder()

    def feed(self, data: bytes) -> str:
        """Decode one chunk into text whose newlines are all CRLF."""
        if not data:
            return ""
        text = self._decoder.decode(data)
        if not text:
            return ""

        if self._pending_cr:
            # A CR ended the previous chunk. If this one opens with LF they
            # were one CRLF; otherwise the CR stood alone as its own break.
            text = text[1:] if text.startswith("\n") else text
            text = "\n" + text
            self._pending_cr = False

        if text.endswith("\r"):
            self._pending_cr = True
            text = text[:-1]

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.replace("\n", "\r\n")


def _incremental_utf8_decoder():
    """An incremental UTF-8 decoder that never raises on partial input."""
    import codecs

    return codecs.getincrementaldecoder("utf-8")(errors="replace")


class SerialTransport:
    """One student's serial link to one ESP32.

    Owned by a `HackSession` (see `app/sessions.py`), so two connected
    students have two transports and neither can read or write the other's
    stream — the same isolation discipline the scenario engine follows.

    Lifecycle is explicit and student-driven: nothing opens a port at
    connect time. `open()` is called only by a serial command, and `close()`
    by a command or by WebSocket teardown, so plugging a board in never by
    itself resets it or claims its port.
    """

    def __init__(
        self,
        *,
        factory: SerialPortFactory | None = None,
        baud: int = config.SERIAL_BAUD_RATE,
        read_timeout: float = config.SERIAL_READ_TIMEOUT_SECONDS,
        read_chunk: int = config.SERIAL_READ_CHUNK_BYTES,
        max_buffered_chunks: int = config.SERIAL_MAX_BUFFERED_CHUNKS,
    ) -> None:
        self._factory = factory if factory is not None else open_pyserial_port
        self._baud = baud
        self._read_timeout = read_timeout
        self._read_chunk = read_chunk
        self._port: SerialPort | None = None
        self._address: str | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._events: asyncio.Queue[SerialEvent] = asyncio.Queue(
            maxsize=max_buffered_chunks
        )
        self._lock = asyncio.Lock()
        self._dropped = 0

    # --- state -------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether a port is currently held. No I/O; safe to call anywhere."""
        return self._port is not None

    @property
    def address(self) -> str | None:
        """The REAL port address in use, or None. Never a training alias."""
        return self._address

    @property
    def baud(self) -> int:
        return self._baud

    @property
    def events(self) -> "asyncio.Queue[SerialEvent]":
        """Where the consumer (the WebSocket pump) reads from."""
        return self._events

    # --- lifecycle ---------------------------------------------------------

    async def open(self, address: str) -> None:
        """Open `address` and start streaming from it.

        `address` must already be a real port: resolution of a student's
        target happens in the command layer, before this is called.

        Re-opening the same address is a no-op rather than an error, so a
        student running `serial-monitor` twice does not close and reset the
        board in between. Opening a *different* address closes the current
        one first, because this transport holds at most one port.

        Raises `SerialTransportError` — never a driver exception — when the
        port cannot be opened.
        """
        async with self._lock:
            if self._port is not None and self._address == address:
                return
            if self._port is not None:
                await self._close_locked(notify=False)

            # The blocking open runs off the event loop: a wedged USB driver
            # can take seconds to fail, and that must not stall the server.
            #
            # Every failure is converted here rather than trusted to the
            # factory. `open_pyserial_port` already raises a controlled
            # error and is re-raised untouched, but the transport must hold
            # this guarantee itself: a factory that let a raw OSError through
            # would surface to the student as the router's generic "internal
            # error" instead of a sentence naming the port.
            try:
                port = await asyncio.to_thread(
                    self._factory, address, self._baud, self._read_timeout
                )
            except SerialTransportError:
                raise
            except Exception as exc:
                raise SerialTransportError(
                    _port_failure_message(address, exc)
                ) from exc
            self._port = port
            self._address = address
            self._stop = threading.Event()
            self._reader = threading.Thread(
                target=self._read_loop,
                args=(port, asyncio.get_running_loop(), self._stop),
                name=f"serial-reader-{address}",
                daemon=True,
            )
            self._reader.start()

    async def close(self) -> None:
        """Release the port and stop the reader. Safe to call when closed.

        Called by `serial-close` and unconditionally on WebSocket teardown,
        so a disconnecting student never leaves a thread or an open port
        behind.
        """
        async with self._lock:
            await self._close_locked(notify=True)

    def release(self) -> None:
        """Release the port synchronously. Safe from a cancelled context.

        WHY THIS EXISTS RATHER THAN JUST `close()`. When a student closes
        the tab, the ASGI server cancels the task running the WebSocket
        endpoint. Cleanup then runs inside a `finally` on a task with a
        pending cancellation, and *every* `await` in that state raises
        `CancelledError` immediately — so an async teardown gets as far as
        its first await and abandons the port, which then stays held until
        the process exits. That is a real leak, and it takes the board with
        it: Build Mode's flasher cannot upload to a port Hack Mode never
        let go of.

        So this method contains no `await` at all. It cannot be interrupted,
        and it always finishes.

        The port is closed inline on the event loop. That is a deliberate
        trade: closing a file handle is a fast syscall, and the alternative
        — not closing it — is strictly worse. It also makes the reader
        thread exit promptly rather than after a full read timeout, because
        its blocking `read()` on a closed port returns or raises at once.

        No thread join, for the same reason there is no await: joining would
        block the loop. The reader is a daemon watching a set stop flag on a
        closed port, so it ends within one read timeout at the outside and
        can never hold up interpreter shutdown. `close()` below is the
        orderly path, and it does join.
        """
        port, stop = self._port, self._stop
        self._port = None
        self._reader = None
        self._address = None
        if port is None:
            return
        stop.set()
        try:
            port.close()
        except Exception:
            # The device may already be gone; the handle is released either
            # way and there is nobody left to tell.
            logger.debug("serial release failed", exc_info=True)

    async def _close_locked(self, *, notify: bool) -> None:
        port, reader, stop = self._port, self._reader, self._stop
        self._port = None
        self._reader = None
        address = self._address
        self._address = None
        if port is None:
            return

        stop.set()
        if reader is not None:
            # Bounded join: the reader parks in `read()` for at most one
            # timeout, so this returns promptly. The generous multiplier is
            # for a driver that is slow to return, and the thread is a
            # daemon, so even a pathological hang cannot stop the process
            # from exiting.
            await asyncio.to_thread(reader.join, self._read_timeout * 10 + 1.0)
        try:
            await asyncio.to_thread(port.close)
        except Exception:
            # Closing a port whose device already vanished routinely raises.
            # There is nothing to recover and nothing worth telling a
            # student: the link is gone either way.
            logger.debug("serial close failed for %s", address, exc_info=True)
        if notify:
            self._emit(
                SerialEvent(
                    kind=SerialEventKind.CLOSED,
                    message=f"serial connection to {address} closed",
                )
            )

    # --- writing -----------------------------------------------------------

    async def write(self, data: bytes) -> None:
        """Write raw bytes to the open port.

        Raises `SerialTransportError` when nothing is open or the write
        fails — including when the board was unplugged since the last read,
        which is frequently the first place a disconnect is noticed.
        """
        port = self._port
        if port is None:
            raise SerialTransportError("no serial connection is open")
        try:
            await asyncio.to_thread(port.write, data)
        except Exception as exc:
            address = self._address
            logger.debug("serial write failed on %s", address, exc_info=True)
            raise SerialTransportError(
                f"write to {address} failed; the board may have been disconnected"
            ) from exc

    # --- reading (worker thread) -------------------------------------------

    def _read_loop(
        self,
        port: SerialPort,
        loop: asyncio.AbstractEventLoop,
        stop: threading.Event,
    ) -> None:
        """Blocking read loop. Runs on its own thread, never on the loop.

        Exits on three conditions only: a stop request, a read failure, or
        the port being replaced. Every exit path is silent from the loop's
        point of view except a failure, which is reported once as an ERROR
        event and ends the stream.
        """
        while not stop.is_set():
            try:
                chunk = port.read(self._read_chunk)
            except Exception as exc:
                if not stop.is_set():
                    # A genuine mid-stream failure — almost always the board
                    # being unplugged. Reported once, then this thread ends.
                    loop.call_soon_threadsafe(self._on_read_failure, exc)
                return
            if chunk and not stop.is_set():
                loop.call_soon_threadsafe(
                    self._emit, SerialEvent(kind=SerialEventKind.DATA, data=chunk)
                )

    def _on_read_failure(self, exc: Exception) -> None:
        """Turn a reader-thread failure into a terminal event. On the loop."""
        address = self._address
        logger.debug("serial read failed on %s", address, exc_info=exc)
        # The port object is dropped without a join: this runs *because* the
        # reader already ended, and the device is gone, so there is nothing
        # to close cleanly.
        port = self._port
        self._port = None
        self._address = None
        self._reader = None
        if port is not None:
            try:
                port.close()
            except Exception:
                logger.debug("serial close after failure failed", exc_info=True)
        self._emit(
            SerialEvent(
                kind=SerialEventKind.ERROR,
                message=f"serial connection to {address} lost",
            )
        )

    def _emit(self, event: SerialEvent) -> None:
        """Queue one event, dropping the oldest under flood. On the loop.

        A bounded queue is what keeps a chatty board from growing memory
        without limit when a client reads slower than the device writes.
        Dropping the OLDEST is deliberate for a live monitor: the useful
        bytes are the most recent ones.
        """
        while True:
            try:
                self._events.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    self._events.get_nowait()
                    self._dropped += 1
                except asyncio.QueueEmpty:  # pragma: no cover - racy, harmless
                    return

    @property
    def dropped_chunks(self) -> int:
        """How many events were discarded under flood. Diagnostics only."""
        return self._dropped


def encode_send_payload(text: str) -> bytes:
    """Encode one `serial-send` payload for the wire.

    A trailing newline is appended because that is what a board's
    `Serial.readStringUntil('\\n')` / `readLine()` waits for — a student
    typing `serial-send HELLO` means a complete line, not four characters
    the sketch will sit on forever.

    Encoded strictly as UTF-8. The parser upstream has already rejected
    control characters, so this cannot smuggle an escape sequence toward the
    device, and nothing here interprets the payload: it is opaque bytes.
    """
    return (text + "\n").encode("utf-8")
