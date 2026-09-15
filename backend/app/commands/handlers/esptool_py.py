"""`esptool.py` — read the target's firmware off flash (Stage 1).

    esptool.py [--port PORT] [--baud BAUD] read_flash 0x0 0x400000 firmware.bin

The real tool supports many subcommands (`chip_id`, `flash_id`,
`write_flash`, ...); this sandbox only models `read_flash`, the one this
lab's learning progression needs. `--port`/`--baud` are accepted and parsed
(a student who copies a real invocation should not hit a syntax error) but
are otherwise unused: this scenario is fully simulated, so there is no real
serial port to open. Whatever the offset/size/filename arguments are, they
are not inspected — a real firmware read is a fixed operation the scenario
either allows (image obtainable) or refuses (nothing attached), not
something whose result depends on the numbers a student typed.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Read firmware off flash (esptool.py read_flash 0x0 0x400000 firmware.bin)."

_USAGE = "usage: esptool.py [--port PORT] [--baud BAUD] read_flash <offset> <size> <file>"

_READ_FLASH = "read_flash"

#: Matches the shell convention `app/commands/router.py` uses (2 = usage
#: error). Duplicated locally rather than imported, the same way
#: `app/commands/handlers/serial_common.py` defines its own copy: this
#: handler validates the tool's own subcommand syntax, not a scenario fact,
#: so it has no `ScenarioOutcome.usage(...)` to defer to.
_EXIT_USAGE = 2


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-p", "--port", "-b", "--baud"})
    subcommand = opts.first_positional()
    if subcommand != _READ_FLASH:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)
    return to_command_result(context.scenario.extract_firmware())


SPEC = CommandSpec(
    name="esptool.py", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE
)
