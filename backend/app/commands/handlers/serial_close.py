"""`serial-close` — release the serial link.

The counterpart to `serial-monitor`: without it a student who opened the
link would have no way to stop streaming or hand the port back (to Build
Mode's flasher, for instance, which needs exclusive access to upload).

Idempotent — closing an already-closed link is a plain statement of fact,
not an error. WebSocket teardown calls the same `SerialTransport.close`
unconditionally, so forgetting to run this never leaks a port or a thread.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.handlers.serial_common import EXIT_OK
from app.commands.parser import ParsedCommand

SUMMARY = "Close the ESP32 serial link and stop streaming its output."


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    transport = context.session.serial
    if not transport.is_open:
        return CommandResult.text("serial: no connection is open", exit_code=EXIT_OK)

    address = transport.address
    # `close` never raises: a port whose device already vanished is still
    # released, and the failure is logged rather than shown.
    await transport.close()
    return CommandResult.text(f"serial: closed {address}", exit_code=EXIT_OK)


SPEC = CommandSpec(name="serial-close", summary=SUMMARY, handler=handle)
