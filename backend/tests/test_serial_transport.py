"""Phase 2A verification: the real USB-serial transport.

    SerialTransport <-> SerialPort (faked here) <-> ESP32 (absent here)

Every test drives a `FakeSerialPort` — a double satisfying exactly the
`SerialPort` protocol the transport depends on — so the whole suite runs with
no pyserial, no board, and no serial port on the machine. That is deliberate
and required: a test that needed hardware would be a test nobody runs.

What is covered: opening and its failures, reading (including data split
across chunks), writing, a board unplugged mid-stream, clean shutdown with no
surviving thread, reconnection, bounded buffering, and the static guarantee
that none of this can execute anything.
"""

from __future__ import annotations

import asyncio
import pathlib
import threading
import time
import tokenize
from collections import deque

import pytest

from app.hardware.serial_transport import (
    SerialEventKind,
    SerialTextDecoder,
    SerialTransport,
    SerialTransportError,
    encode_send_payload,
)

TRANSPORT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "app"
    / "hardware"
    / "serial_transport.py"
)

#: Matches `config.SERIAL_READ_TIMEOUT_SECONDS` in spirit: short enough that
#: a stopped reader is joined promptly, long enough not to spin.
FAKE_READ_PAUSE = 0.005


class FakeSerialPort:
    """A pyserial-shaped double: scripted reads, captured writes.

    Implements exactly the three methods `SerialPort` declares, which is the
    point — it proves the transport depends on nothing more of pyserial than
    that, and it is why no test here needs the real library.
    """

    def __init__(
        self,
        chunks: tuple[bytes, ...] = (),
        *,
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self._chunks = deque(chunks)
        self.written = bytearray()
        self.closed = False
        self.read_error = read_error
        self.write_error = write_error
        self.reads_after_data = 0

    def read(self, size: int) -> bytes:
        if self._chunks:
            return self._chunks.popleft()
        self.reads_after_data += 1
        if self.read_error is not None:
            raise self.read_error
        # Stand in for a blocking read that timed out with nothing to show:
        # the real one parks in the driver, this parks briefly here.
        time.sleep(FAKE_READ_PAUSE)
        return b""

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error
        self.written.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True

    def feed(self, chunk: bytes) -> None:
        """Queue more bytes for the reader to pick up."""
        self._chunks.append(chunk)


class RecordingFactory:
    """Hands out prepared ports and records what it was asked for."""

    def __init__(self, *ports: FakeSerialPort, error: Exception | None = None) -> None:
        self._ports = deque(ports)
        self.error = error
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, address: str, baud: int, read_timeout: float) -> FakeSerialPort:
        self.calls.append((address, baud, read_timeout))
        if self.error is not None:
            raise self.error
        return self._ports.popleft() if self._ports else FakeSerialPort()

    @property
    def addresses(self) -> list[str]:
        return [call[0] for call in self.calls]


def transport_for(
    *ports: FakeSerialPort, error: Exception | None = None, **kwargs
) -> tuple[SerialTransport, RecordingFactory]:
    factory = RecordingFactory(*ports, error=error)
    return SerialTransport(factory=factory, **kwargs), factory


async def next_event(transport: SerialTransport, timeout: float = 2.0):
    """The next serial event, or a test failure rather than a hang."""
    return await asyncio.wait_for(transport.events.get(), timeout)


async def collect_data(transport: SerialTransport, size: int, timeout: float = 2.0) -> bytes:
    """Accumulate DATA events until `size` bytes have arrived."""
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    while len(buffer) < size:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"only got {bytes(buffer)!r} before the deadline"
        event = await asyncio.wait_for(transport.events.get(), remaining)
        assert event.kind is SerialEventKind.DATA, event
        buffer.extend(event.data)
    return bytes(buffer)


def run(coro):
    return asyncio.run(coro)


# --- 1: initialization ------------------------------------------------------


def test_a_fresh_transport_holds_nothing_open() -> None:
    """Entering Hack Mode must not claim a port or reset a board."""
    transport, factory = transport_for()

    assert transport.is_open is False
    assert transport.address is None
    assert factory.calls == [], "constructing a transport must not open anything"


def test_the_configured_baud_is_used_and_not_hardcoded_at_the_call_site() -> None:
    transport, factory = transport_for(baud=9600)

    run(transport.open("COM3"))

    assert factory.calls[0][1] == 9600
    assert transport.baud == 9600


def test_opening_uses_exactly_the_address_it_was_given() -> None:
    """The transport never resolves, guesses, or enumerates a port."""
    transport, factory = transport_for()

    run(transport.open("/dev/ttyACM0"))

    assert factory.addresses == ["/dev/ttyACM0"]
    assert transport.address == "/dev/ttyACM0"
    assert transport.is_open is True


# --- 2: connection failure --------------------------------------------------


def test_a_port_that_will_not_open_raises_a_controlled_error() -> None:
    transport, _ = transport_for(error=OSError("could not open port COM9"))

    with pytest.raises(SerialTransportError) as excinfo:
        run(transport.open("COM9"))

    assert "COM9" in str(excinfo.value)
    assert transport.is_open is False


def test_a_busy_port_says_so_in_terms_a_student_can_act_on() -> None:
    """The common real failure: a serial monitor already holds the port."""
    from app.hardware.serial_transport import _port_failure_message

    message = _port_failure_message("COM3", OSError("Access is denied."))

    assert "COM3" in message
    assert "another program" in message


def test_a_failed_open_leaves_nothing_behind_to_clean_up() -> None:
    transport, _ = transport_for(error=OSError("nope"))
    before = threading.active_count()

    with pytest.raises(SerialTransportError):
        run(transport.open("COM9"))

    assert transport.is_open is False
    assert threading.active_count() <= before


# --- 3: reading -------------------------------------------------------------


def test_data_the_board_sends_arrives_as_events() -> None:
    port = FakeSerialPort((b"hello from esp32\r\n",))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        try:
            return await collect_data(transport, len(b"hello from esp32\r\n"))
        finally:
            await transport.close()

    assert run(scenario()) == b"hello from esp32\r\n"


def test_output_split_across_reads_is_delivered_in_order() -> None:
    port = FakeSerialPort((b"QK", b"774", b":READY\r\n"))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        try:
            return await collect_data(transport, len(b"QK774:READY\r\n"))
        finally:
            await transport.close()

    assert run(scenario()) == b"QK774:READY\r\n"


def test_an_idle_board_produces_no_events_and_does_not_spin() -> None:
    """A silent board must not flood the queue with empty reads."""
    port = FakeSerialPort()
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await asyncio.sleep(0.1)
        idle_events = transport.events.qsize()
        await transport.close()
        return idle_events, port.reads_after_data

    events, reads = run(scenario())

    assert events == 0, "an idle board must produce no output events"
    # It did keep polling — it is a real loop, just a blocking one.
    assert reads > 0


# --- 4: writing -------------------------------------------------------------


def test_written_bytes_reach_the_port() -> None:
    port = FakeSerialPort()
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await transport.write(encode_send_payload("HELLO"))
        await transport.close()

    run(scenario())

    assert bytes(port.written) == b"HELLO\n"


def test_a_sent_line_is_newline_terminated_so_the_sketch_can_read_it() -> None:
    """`Serial.readStringUntil('\\n')` waits forever without this."""
    assert encode_send_payload("HELLO") == b"HELLO\n"
    assert encode_send_payload("") == b"\n"


def test_writing_with_nothing_open_is_a_controlled_error() -> None:
    transport, _ = transport_for()

    with pytest.raises(SerialTransportError) as excinfo:
        run(transport.write(b"HELLO\n"))

    assert "no serial connection" in str(excinfo.value)


def test_a_failed_write_is_a_controlled_error_naming_the_port() -> None:
    port = FakeSerialPort(write_error=OSError("device disconnected"))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        try:
            await transport.write(b"HELLO\n")
        finally:
            await transport.close()

    with pytest.raises(SerialTransportError) as excinfo:
        run(scenario())

    assert "COM3" in str(excinfo.value)
    # No driver traceback leaks into a message the terminal will render.
    assert "OSError" not in str(excinfo.value)


# --- 5: the board is unplugged mid-stream -----------------------------------


def test_a_board_unplugged_while_streaming_reports_a_lost_connection() -> None:
    """The failure mode that must never take down a student's session."""
    port = FakeSerialPort((b"alive\r\n",), read_error=OSError("device not configured"))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        first = await next_event(transport)
        second = await next_event(transport)
        return first, second

    data_event, error_event = run(scenario())

    assert data_event.kind is SerialEventKind.DATA
    assert error_event.kind is SerialEventKind.ERROR
    assert "COM3" in error_event.message
    assert "lost" in error_event.message


def test_a_lost_connection_leaves_the_transport_closed() -> None:
    port = FakeSerialPort(read_error=OSError("device not configured"))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await next_event(transport)
        return transport.is_open, transport.address

    is_open, address = run(scenario())

    assert is_open is False
    assert address is None


def test_a_write_after_the_board_vanished_fails_cleanly() -> None:
    port = FakeSerialPort(read_error=OSError("gone"))
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await next_event(transport)  # the ERROR event
        await transport.write(b"HELLO\n")

    with pytest.raises(SerialTransportError):
        run(scenario())


# --- 6: reconnecting --------------------------------------------------------


def test_the_link_can_be_reopened_after_a_loss() -> None:
    """Plugging the board back in and re-running the command must work."""
    dead = FakeSerialPort(read_error=OSError("gone"))
    fresh = FakeSerialPort((b"back\r\n",))
    transport, factory = transport_for(dead, fresh)

    async def scenario():
        await transport.open("COM3")
        lost = await next_event(transport)
        assert lost.kind is SerialEventKind.ERROR

        await transport.open("COM3")
        data = await collect_data(transport, len(b"back\r\n"))
        await transport.close()
        return data

    assert run(scenario()) == b"back\r\n"
    assert factory.addresses == ["COM3", "COM3"]


def test_reopening_the_same_port_does_not_reset_the_board() -> None:
    """A second `serial-monitor` must not close and reopen the link.

    Opening a port resets most ESP32 boards, so a no-op here is the
    difference between a harmless repeat command and interrupting whatever
    the student's firmware was doing.
    """
    port = FakeSerialPort()
    transport, factory = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await transport.open("COM3")
        await transport.close()

    run(scenario())

    assert len(factory.calls) == 1
    assert port.closed is True  # only the final close


def test_opening_a_different_port_releases_the_first() -> None:
    first, second = FakeSerialPort(), FakeSerialPort()
    transport, factory = transport_for(first, second)

    async def scenario():
        await transport.open("COM3")
        await transport.open("COM4")
        await transport.close()

    run(scenario())

    assert factory.addresses == ["COM3", "COM4"]
    assert first.closed is True
    assert second.closed is True


# --- 7: cleanup -------------------------------------------------------------


def test_closing_releases_the_port_and_stops_the_reader_thread() -> None:
    port = FakeSerialPort()
    transport, _ = transport_for(port)
    before = threading.active_count()

    async def scenario():
        await transport.open("COM3")
        await transport.close()

    run(scenario())

    assert port.closed is True
    assert transport.is_open is False
    assert transport.address is None
    # Give the interpreter a beat to reap the finished thread.
    time.sleep(0.05)
    assert threading.active_count() <= before, "a serial reader thread outlived close()"


def test_closing_announces_itself_once() -> None:
    transport, _ = transport_for(FakeSerialPort())

    async def scenario():
        await transport.open("COM3")
        await transport.close()
        return await next_event(transport)

    event = run(scenario())

    assert event.kind is SerialEventKind.CLOSED
    assert "COM3" in event.message


def test_closing_an_already_closed_transport_is_harmless() -> None:
    transport, _ = transport_for()

    async def scenario():
        await transport.close()
        await transport.close()
        return transport.events.qsize()

    assert run(scenario()) == 0


def test_a_close_still_releases_a_port_whose_device_already_vanished() -> None:
    """`close()` on a yanked device raises in pyserial; it must not here."""

    class ExplodingPort(FakeSerialPort):
        def close(self) -> None:
            self.closed = True
            raise OSError("device already gone")

    port = ExplodingPort()
    transport, _ = transport_for(port)

    async def scenario():
        await transport.open("COM3")
        await transport.close()

    run(scenario())

    assert port.closed is True
    assert transport.is_open is False


# --- 8: bounded buffering ---------------------------------------------------


def test_a_flooding_board_cannot_grow_the_queue_without_bound() -> None:
    chunks = tuple(b"x" * 64 for _ in range(200))
    port = FakeSerialPort(chunks)
    transport, _ = transport_for(port, max_buffered_chunks=8)

    async def scenario():
        await transport.open("COM3")
        await asyncio.sleep(0.2)  # let the reader run without a consumer
        size = transport.events.qsize()
        dropped = transport.dropped_chunks
        await transport.close()
        return size, dropped

    size, dropped = run(scenario())

    assert size <= 8, "the buffer must stay bounded under flood"
    assert dropped > 0, "overflow should be recorded, not silent"


# --- 9: bytes -> terminal text ----------------------------------------------


def test_the_decoder_turns_board_newlines_into_crlf() -> None:
    """xterm is in raw mode: a bare LF would not return to column 0."""
    assert SerialTextDecoder().feed(b"one\ntwo\n") == "one\r\ntwo\r\n"


def test_the_decoder_does_not_double_an_existing_crlf() -> None:
    assert SerialTextDecoder().feed(b"line\r\n") == "line\r\n"


def test_a_crlf_split_across_two_reads_stays_one_line_break() -> None:
    """Serial chunks land arbitrarily; this is the routine awkward case."""
    decoder = SerialTextDecoder()

    first = decoder.feed(b"temperature: 21.5\r")
    second = decoder.feed(b"\nhumidity: 48\r\n")

    assert first + second == "temperature: 21.5\r\nhumidity: 48\r\n"


def test_a_utf8_character_split_across_two_reads_is_reassembled() -> None:
    decoder = SerialTextDecoder()
    payload = "°C\r\n".encode("utf-8")

    first = decoder.feed(payload[:1])
    second = decoder.feed(payload[1:])

    assert first + second == "°C\r\n"


def test_undecodable_bytes_do_not_raise() -> None:
    """Line noise on a serial link must never crash the pump."""
    assert SerialTextDecoder().feed(b"\xff\xfe") != ""


def test_the_decoder_ignores_empty_chunks() -> None:
    assert SerialTextDecoder().feed(b"") == ""


# --- 10: the security boundary ---------------------------------------------


FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "spawnl",
        "spawnv",
        "eval",
        "exec",
        "compile",
        "shell",
    }
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def test_the_serial_transport_cannot_execute_anything() -> None:
    """Real hardware access buys this module no exemption.

    It is scanned with the full forbidden set, exactly like every other
    module under `app/` — `app/build/process.py` remains the only file in
    the backend allowed to spawn a process, and this is not it.
    """
    offenders = sorted(_code_names(TRANSPORT_PATH) & FORBIDDEN_NAMES)
    assert offenders == [], f"execution primitive in the serial transport: {offenders}"


def test_the_transport_writes_payloads_verbatim_without_interpreting_them() -> None:
    """Shell-looking text is bytes on a wire, nothing more.

    The parser upstream rejects these characters on a command line anyway;
    this proves that even reaching the transport directly, they are only
    ever copied to the port.
    """
    port = FakeSerialPort()
    transport, _ = transport_for(port)
    payload = "; rm -rf / && whoami `id`"

    async def scenario():
        await transport.open("COM3")
        await transport.write(encode_send_payload(payload))
        await transport.close()

    run(scenario())

    assert bytes(port.written) == (payload + "\n").encode("utf-8")
