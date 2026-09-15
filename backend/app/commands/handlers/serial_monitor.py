"""`serial-monitor` — open the link and stream the board's output.

    serial-monitor [target]

Opens the real port behind `target` (defaulting to the attached board) and
leaves it open. From that moment the ESP32's own `Serial.print` output is
forwarded to xterm.js by the WebSocket's serial pump — see
`app/websocket.py`. Nothing is synthesised: what appears is exactly what the
flashed firmware sent.

THIS IS NOT A SHELL, AND OPENS NOTHING BUT A UART. The command takes at most
one argument, that argument is only ever matched against the names of the
already-detected board, and the only effect is a serial port being opened.
There is no process, no interpreter, and no filesystem path involved.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.handlers.serial_common import (
    EXIT_FAILURE,
    EXIT_OK,
    describe_port,
    resolve_target,
    unknown_target_result,
)
from app.commands.parser import ParsedCommand
from app.hardware import SerialTransportError

SUMMARY = "Open the ESP32 serial link and stream its output to this terminal."


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    target, remaining = resolve_target(command.args)
    if target.error is not None:
        return target.error
    if remaining:
        # A leftover argument means the first token did not name this board.
        # Reported rather than ignored: silently monitoring a different port
        # than the student asked for is worse than saying no.
        return unknown_target_result(command.args[0])

    transport = context.session.serial
    already_open = transport.is_open and transport.address == target.address

    try:
        await transport.open(target.address)
    except SerialTransportError as exc:
        # A controlled, already-student-safe message — see
        # `serial_transport.py::_port_failure_message`.
        return CommandResult.text(f"serial: {exc}", exit_code=EXIT_FAILURE)

    if already_open:
        return CommandResult.text(
            f"serial: already monitoring {describe_port(target.address)}",
            exit_code=EXIT_OK,
        )
    return CommandResult.text(
        f"serial: monitoring {describe_port(target.address)} at {transport.baud} baud",
        "Output from the board appears below. Run 'serial-close' to stop.",
        exit_code=EXIT_OK,
    )


SPEC = CommandSpec(name="serial-monitor", summary=SUMMARY, handler=handle)
