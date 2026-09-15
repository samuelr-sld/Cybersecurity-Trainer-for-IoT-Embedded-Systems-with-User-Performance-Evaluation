"""`grep` — search an extracted firmware image's strings for a pattern.

    grep mqtt firmware.bin
    grep "OTA update" firmware.bin

Real `grep`'s first argument is the pattern and the rest are files; a
multi-word pattern is the student's own responsibility to quote, exactly as
on a real command line — this handler does not rejoin bare words the way the
old `firmware-analyze [search term]` command used to. Matching is
case-insensitive (this scenario's only supported mode; there is no `-i` flag
to toggle it off).

Thin adapter over `Scenario.analyze_firmware(pattern)`; the scenario decides
which strings exist and whether the pattern matches any of them. A search
with no hits prints nothing and exits non-zero, exactly like real `grep`.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Search a file's strings for a pattern (e.g. grep mqtt firmware.bin)."

_USAGE = "usage: grep <pattern> <file>"

#: See `app/commands/handlers/esptool_py.py` for why this is a local copy
#: rather than an import from the router.
_EXIT_USAGE = 2


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    if len(command.args) < 2:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)
    pattern = command.args[0]
    return to_command_result(context.scenario.analyze_firmware(pattern))


SPEC = CommandSpec(name="grep", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE)
