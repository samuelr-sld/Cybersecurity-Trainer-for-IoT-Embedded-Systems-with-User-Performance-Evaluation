"""`serial-send` — write one line to the attached ESP32.

    serial-send HELLO
    serial-send /dev/ttyUSB0 HELLO

The payload is written verbatim (plus a trailing newline) to the REAL port
behind the target. Whatever the board sends back arrives through the same
stream `serial-monitor` shows, forwarded by the WebSocket's serial pump —
this handler never invents a reply, and when a physical ESP32 is attached
the only response a student sees is the one the firmware actually produced.

THE PAYLOAD IS DATA, NEVER CODE. It is encoded to UTF-8 bytes and handed to
a UART. Nothing parses it, nothing executes it, and it cannot reach a shell,
an interpreter, or the filesystem: the parser upstream has already rejected
shell metacharacters and control characters, and there is no process on this
path to give them meaning even if it had not.
"""

from __future__ import annotations

from app import config
from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.handlers.serial_common import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    describe_port,
    resolve_target,
)
from app.commands.parser import ParsedCommand
from app.hardware import SerialTransportError, encode_send_payload

SUMMARY = "Send a line of text to the ESP32 over serial (e.g. serial-send HELLO)."

_USAGE = "usage: serial-send [target] <text>"


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    target, remaining = resolve_target(command.args)
    if target.error is not None:
        return target.error

    # Words are rejoined with single spaces. The parser collapsed the
    # original run of whitespace when it tokenised, so this reconstructs the
    # student's line as the grammar understood it — and `serial-send "a  b"`
    # keeps its inner spacing, because quoting makes it one token.
    payload = " ".join(remaining)
    if not payload:
        return CommandResult.text(_USAGE, exit_code=EXIT_USAGE)
    if len(payload) > config.MAX_SERIAL_SEND_CHARS:
        return CommandResult.text(
            f"serial: payload exceeds {config.MAX_SERIAL_SEND_CHARS} characters",
            exit_code=EXIT_USAGE,
        )

    # Encoded once, up front, so the count reported below is the number of
    # bytes actually put on the wire — including the trailing newline and
    # any multi-byte characters — rather than the length of the typed text.
    payload_bytes = encode_send_payload(payload)

    transport = context.session.serial
    opened_now = False
    try:
        if not transport.is_open or transport.address != target.address:
            # Opened on demand so proving the link takes one command, not
            # two. Announced below, because opening a port resets most ESP32
            # boards and a student should know that is what just happened.
            await transport.open(target.address)
            opened_now = True
        await transport.write(payload_bytes)
    except SerialTransportError as exc:
        return CommandResult.text(f"serial: {exc}", exit_code=EXIT_FAILURE)

    lines = []
    if opened_now:
        lines.append(
            f"serial: opened {describe_port(target.address)} at {transport.baud} baud"
        )
    lines.append(f"serial: sent {len(payload_bytes)} bytes to {target.address}")
    return CommandResult.text(*lines, exit_code=EXIT_OK)


SPEC = CommandSpec(name="serial-send", summary=SUMMARY, handler=handle)
