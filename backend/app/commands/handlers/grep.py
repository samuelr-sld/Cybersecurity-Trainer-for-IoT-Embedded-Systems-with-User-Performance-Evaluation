"""`grep` — search an extracted firmware image's strings for a pattern.

    grep mqtt firmware.bin
    grep "OTA update" firmware.bin

Real `grep`'s first argument is the pattern and the rest are files; a
multi-word pattern is the student's own responsibility to quote, exactly as
on a real command line. Matching is case-insensitive (this sandbox's only
supported mode; there is no `-i` flag to toggle it off).

TWO SOURCES, ONE COMMAND (Phase 2H.1) — see
`app/commands/handlers/strings.py` for the full explanation this handler
shares. `context.scenario.analyze_firmware(pattern)` still runs first,
unconditionally, for events and objective tracking. When this session has a
real `firmware_artifact` (a real `esptool.py read_flash` already ran — see
`app/commands/handlers/esptool_py.py`), the match is computed directly
against those real bytes via `extract_printable_strings`, independently of
whatever the scenario's own canned table would have matched; otherwise this
falls back to the scenario's simulated result exactly as before. A search
with no hits prints nothing and exits non-zero, exactly like real `grep`,
in both cases.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result
from app.hardware import extract_printable_strings

SUMMARY = "Search a file's strings for a pattern (e.g. grep mqtt firmware.bin)."

_USAGE = "usage: grep <pattern> <file>"

#: See `app/commands/handlers/esptool_py.py` for why this is a local copy
#: rather than an import from the router.
_EXIT_USAGE = 2
_EXIT_OK = 0
_EXIT_FAILURE = 1


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    if len(command.args) < 2:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)
    pattern = command.args[0]

    scenario_outcome = context.scenario.analyze_firmware(pattern)

    artifact = context.session.firmware_artifact
    if artifact is None:
        # No real capture for this session — the long-standing simulated
        # path, including its own prerequisite-not-met failure text.
        return to_command_result(scenario_outcome)

    needle = pattern.lower()
    matches = tuple(
        line for line in extract_printable_strings(artifact.data) if needle in line.lower()
    )
    return CommandResult(
        lines=matches,
        exit_code=_EXIT_OK if matches else _EXIT_FAILURE,
        events=scenario_outcome.events,
        fields_correct=scenario_outcome.fields_correct,
    )


SPEC = CommandSpec(name="grep", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE)
