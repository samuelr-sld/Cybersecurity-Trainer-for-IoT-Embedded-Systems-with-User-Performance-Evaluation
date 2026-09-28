"""`strings` — dump the printable strings in an extracted firmware image.

    strings firmware.bin
    strings -n 8 firmware.bin

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

`-n <min-len>` (real `strings(1)`'s own flag, also accepted attached as
`-n8`) raises the minimum run length from the default 4. On a real ESP32
dump most short runs are machine code that merely happens to be printable,
so `-n 8` is the standard first move for cutting that noise. It only
affects the real-artifact path; the simulated table is already curated.

PAGED OUTPUT. A real dump can run to hundreds of lines, which is exactly
what makes it hard to inspect one screenful at a time — so both paths mark
their `CommandResult` `pageable=True` (see `app/pager.py` and the pager
section of `app/websocket.py`). This is a property of the command, not of
which source produced the lines: the simulated table happens to already fit
one screen, so marking it too costs nothing and keeps the two paths
behaving identically from the pager's point of view.
"""

from __future__ import annotations

from dataclasses import replace

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result
from app.hardware import extract_printable_strings
from app.hardware.firmware_strings import DEFAULT_MIN_LENGTH

SUMMARY = "Print the printable strings in a file (e.g. strings firmware.bin)."

_USAGE = "usage: strings [-n <min-len>] <file>"

#: Bounds on `-n`. Real `strings` accepts any positive length; the cap only
#: keeps a typo from silently printing nothing.
_MIN_LENGTH_FLOOR = 1
_MIN_LENGTH_CEILING = 256

#: See `app/commands/handlers/esptool_py.py` for why this is a local copy
#: rather than an import from the router.
_EXIT_USAGE = 2
_EXIT_OK = 0
_EXIT_FAILURE = 1


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    parsed = parse_options(command.args, {"-n"})
    if not parsed.positionals or parsed.flags:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)
    min_length = _min_length(parsed.value("-n"))
    if min_length is None:
        return CommandResult.text(
            f"strings: invalid minimum string length (use {_MIN_LENGTH_FLOOR}-{_MIN_LENGTH_CEILING})",
            _USAGE,
            exit_code=_EXIT_USAGE,
        )

    scenario_outcome = context.scenario.analyze_firmware(None)

    artifact = context.session.firmware_artifact
    if artifact is None:
        # No real capture for this session — the long-standing simulated
        # path, including its own prerequisite-not-met failure text.
        # `replace(..., pageable=True)` rather than passing it in above:
        # `to_command_result` is the shared scenario-outcome adapter (see
        # app/commands/scenario_adapter.py) and stays ignorant of paging,
        # exactly as it stays ignorant of every other command-specific
        # concern its callers might have.
        return replace(to_command_result(scenario_outcome), pageable=True)

    real_lines = extract_printable_strings(artifact.data, min_length)
    return CommandResult(
        lines=real_lines,
        exit_code=_EXIT_OK if real_lines else _EXIT_FAILURE,
        events=scenario_outcome.events,
        fields_correct=scenario_outcome.fields_correct,
        pageable=True,
    )


def _min_length(raw: str | None) -> int | None:
    """The `-n` value, the default when absent, or None when invalid."""
    if raw is None:
        return DEFAULT_MIN_LENGTH
    if not raw.isdigit():
        return None
    value = int(raw)
    if not _MIN_LENGTH_FLOOR <= value <= _MIN_LENGTH_CEILING:
        return None
    return value


SPEC = CommandSpec(
    name="strings", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE
)
