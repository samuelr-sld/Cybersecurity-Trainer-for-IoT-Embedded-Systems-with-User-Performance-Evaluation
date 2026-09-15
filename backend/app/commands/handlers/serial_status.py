"""`serial-status` — what board is attached and whether the link is open.

Read-only and side-effect free: it opens nothing, writes nothing, and — per
the shared layer's polling discipline — runs no Arduino CLI discovery of its
own. It reports the `DeviceState` the shared `DeviceMonitor` already holds,
which the frontend refreshes on its own interval.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.handlers.serial_common import EXIT_OK, describe_port
from app.commands.parser import ParsedCommand
from app.hardware import canonical_alias, device_monitor

SUMMARY = "Report the attached ESP32 and the state of the serial link."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    state = device_monitor.snapshot()
    transport = context.session.serial

    if not state.connected or not state.port:
        return CommandResult.text(
            "device:  no ESP32 detected",
            f"serial:  closed (target would be {canonical_alias()})",
            exit_code=EXIT_OK,
        )

    lines = [
        f"device:  {state.panel or state.mac or state.board or 'ESP32'}",
        f"port:    {describe_port(state.port)}",
        f"baud:    {transport.baud}",
    ]
    if transport.is_open:
        # `transport.address` rather than `state.port`: this reports the port
        # actually held open, which is the honest answer if a board were
        # re-enumerated while a link was live.
        lines.append(f"serial:  open on {transport.address}")
    else:
        lines.append("serial:  closed — run 'serial-monitor' to connect")
    return CommandResult.text(*lines, exit_code=EXIT_OK)


SPEC = CommandSpec(name="serial-status", summary=SUMMARY, handler=handle)
