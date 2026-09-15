"""`strings` — dump the printable strings in an extracted firmware image.

    strings firmware.bin

An unfiltered pass over `firmware.bin`, the way real `strings(1)` behaves
with no options: every printable string the scenario's firmware image
contains, in order, no other commentary. Finding the device's MQTT broker,
port, and topic in that dump — rather than being handed them — is the point;
narrowing the output to a specific line is `grep`'s job
(`app/commands/handlers/grep.py`), not this command's.

Thin adapter over `Scenario.analyze_firmware(None)`: this handler does not
know what strings exist or what any of them mean, only that a filename
argument is required, matching real `strings`, which reads from a file (or
stdin — unavailable in this sandbox, so a filename is mandatory here).
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Print the printable strings in a file (e.g. strings firmware.bin)."

_USAGE = "usage: strings <file>"

#: See `app/commands/handlers/esptool_py.py` for why this is a local copy
#: rather than an import from the router.
_EXIT_USAGE = 2


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    if not command.args:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)
    return to_command_result(context.scenario.analyze_firmware(None))


SPEC = CommandSpec(
    name="strings", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE
)
