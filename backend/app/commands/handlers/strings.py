"""`strings` — dump the printable strings in an extracted firmware image.

    strings firmware.bin

An unfiltered pass over `firmware.bin`, the way real `strings(1)` behaves
with no options. Finding the device's MQTT broker, port, and topic in that
dump — rather than being handed them — is the point; narrowing the output to
a specific line is `grep`'s job (`app/commands/handlers/grep.py`), not this
command's.

TWO SOURCES, ONE COMMAND (Phase 2H.1). `context.scenario.analyze_firmware`
still runs first, unconditionally, for its events and objective-tracking
side effects — unchanged from before. But the LINES this command prints come
from `context.session.firmware_artifact` whenever one exists: the real bytes
a real `esptool.py read_flash` (this same session, this same command
registry) actually captured off a real, physically attached board (see
`app/commands/handlers/esptool_py.py`). Only when no real artifact exists —
every session today with no hardware attached, and any session before its
first `esptool.py read_flash` — does this fall back to the scenario's own
simulated string table, exactly as it always has.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result
from app.hardware import extract_printable_strings

SUMMARY = "Print the printable strings in a file (e.g. strings firmware.bin)."

_USAGE = "usage: strings <file>"

#: See `app/commands/handlers/esptool_py.py` for why this is a local copy
#: rather than an import from the router.
_EXIT_USAGE = 2
_EXIT_OK = 0
_EXIT_FAILURE = 1


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    if not command.args:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)

    scenario_outcome = context.scenario.analyze_firmware(None)

    artifact = context.session.firmware_artifact
    if artifact is None:
        # No real capture for this session — the long-standing simulated
        # path, including its own prerequisite-not-met failure text.
        return to_command_result(scenario_outcome)

    real_lines = extract_printable_strings(artifact.data)
    return CommandResult(
        lines=real_lines,
        exit_code=_EXIT_OK if real_lines else _EXIT_FAILURE,
        events=scenario_outcome.events,
        fields_correct=scenario_outcome.fields_correct,
    )


SPEC = CommandSpec(
    name="strings", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE
)
