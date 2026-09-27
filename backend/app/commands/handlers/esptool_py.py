"""`esptool.py` — read the target's firmware off flash (Stage 1).

    esptool.py [--port PORT] [--baud BAUD] read_flash 0x0 0x400000 firmware.bin

The real tool supports many subcommands (`chip_id`, `flash_id`,
`write_flash`, ...); this sandbox only models `read_flash`, the one this
lab's learning progression needs. `--port`/`--baud` are accepted and parsed
(a student who copies a real invocation should not hit a syntax error) but
are never read: the port this command opens, real or simulated, is always
the one the shared device layer already detected — never a value a student
names (see `app/hardware/serial_common.py` for the same discipline on the
serial commands).

TWO PATHS, ONE COMMAND (Phase 2H.1). Whether this reads REAL hardware or
returns the scenario's simulated banner depends entirely on whether a real
ESP32 is attached, exactly the way `serial-status` already varies its
answer:

    no board attached / board_present is False
        -> the long-standing, fully simulated path: `Scenario.extract_firmware()`
           and its canned lines, unchanged. This is what keeps Hack Mode usable
           for the no-hardware development flow this whole trainer is built on.

    a real board is attached
        -> a REAL `esptool read_flash` runs against it
           (`app/hardware/flash_reader.py`, the same argv-array/`process.py`
           pattern `identity.py` already uses for MAC reads), and the bytes it
           actually returns are captured on THIS session
           (`HackSession.firmware_artifact`) for `strings`/`grep` to analyse.
           If the real read fails, this reports the REAL failure — it does
           NOT fall back to a fabricated simulated success. "REAL HARDWARE
           RESULT != SIMULATED SCENARIO RESULT" (Phase 2H's own hardware-in-
           the-loop principle) holds all the way through this command.

THE SCENARIO STILL DECIDES WHAT HAPPENED, IN BOTH CASES. Even on the real
path, `context.scenario.extract_firmware()` is still called — for its
EVENTS and objective-tracking side effects only, never for its canned text —
so `firmware_extracted` still fires, ACR/RE/TTE evidence is unaffected, and
the scenario itself never learns a real board exists. This handler is the
ONLY place that decides real-vs-simulated; `SmartHomeMQTTScenario` (and
every other `Scenario`) stays exactly as ignorant of hardware as
`app/scenarios/base.py` requires.

THE STUDENT'S <file> ARGUMENT IS NARRATIVE ONLY, NEVER A PATH. On the real
path the bytes are written by `flash_reader.py` to its OWN backend-chosen
temporary file and read back into memory; the filename a student types is
echoed in output exactly as the simulated path always echoed it, and is
never opened, created, or joined onto a filesystem location here.
"""

from __future__ import annotations

from app import config
from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result
from app.events.clock import utc_now
from app.hardware import FirmwareArtifact, FlashReadRequest, default_flash_reader, device_monitor

SUMMARY = "Read firmware off flash (esptool.py read_flash 0x0 0x400000 firmware.bin)."

_USAGE = "usage: esptool.py [--port PORT] [--baud BAUD] read_flash <offset> <size> <file>"

_READ_FLASH = "read_flash"

#: Matches the shell convention `app/commands/router.py` uses (1 = failure,
#: 2 = usage error). Duplicated locally rather than imported, the same way
#: `app/commands/handlers/serial_common.py` defines its own copy.
_EXIT_USAGE = 2
_EXIT_FAILURE = 1


def _parse_int(token: str) -> int | None:
    """Parse a hex (`0x...`) or decimal integer, the way esptool's own CLI
    accepts either spelling for offset/size arguments. `None` for anything
    else, rather than raising — an invalid argument is this command's usage
    error, not an internal one."""
    try:
        return int(token, 0)
    except ValueError:
        return None


def _simulated(context: CommandContext) -> CommandResult:
    """The long-standing, fully simulated path — unchanged."""
    return to_command_result(context.scenario.extract_firmware())


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-p", "--port", "-b", "--baud"})
    positionals = opts.positionals
    subcommand = positionals[0] if positionals else None
    if subcommand != _READ_FLASH:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)

    if len(positionals) < 4:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)

    offset = _parse_int(positionals[1])
    size = _parse_int(positionals[2])
    # positionals[3] is the student's chosen filename — accepted for syntax
    # compatibility and echoed in output, never used as a filesystem path
    # (see the module docstring).
    if offset is None or size is None or offset < 0 or size <= 0:
        return CommandResult.text(_USAGE, exit_code=_EXIT_USAGE)

    state = device_monitor.snapshot()
    if not state.board_present or not state.port:
        return _simulated(context)

    if size > config.HARDWARE_MAX_FLASH_READ_BYTES:
        return CommandResult.text(
            f"esptool.py: refusing to read {size} bytes; this sandbox's cap "
            f"is {config.HARDWARE_MAX_FLASH_READ_BYTES} bytes",
            exit_code=_EXIT_FAILURE,
        )

    # The read owns the port for its whole duration, exactly like a flash
    # upload does (`BuildService.flash_workspace`): a MAC probe started by
    # another session's poll would reset the board mid-read. `read_flash`
    # itself is bounded (see `app/build/process.py`), so the hold is too.
    with device_monitor.hold_identity_probe():
        outcome = await default_flash_reader.read_flash(
            FlashReadRequest(
                port=state.port,
                offset=offset,
                size=size,
                timeout_seconds=config.HARDWARE_FLASH_READ_TIMEOUT_SECONDS,
            )
        )
    if not outcome.ok:
        return CommandResult.text(
            f"esptool.py: could not read flash ({outcome.category.value})"
            + (f": {outcome.detail}" if outcome.detail else ""),
            exit_code=_EXIT_FAILURE,
        )

    context.session.firmware_artifact = FirmwareArtifact(
        data=outcome.data,
        offset=offset,
        size=size,
        port=state.port,
        captured_at=utc_now(),
    )
    # Events and objective-tracking only — see the module docstring for why
    # this call's LINES are discarded in favour of the real ones below.
    scenario_outcome = context.scenario.extract_firmware()

    read_bytes = len(outcome.data)
    lines = (
        "esptool.py v5.3.1",
        f"Serial port {state.port}",
        "Connecting....",
        "Uploading stub...",
        "Running stub...",
        f"Reading {read_bytes} bytes at {hex(offset)} in flash ({read_bytes} remaining)...",
        f"Read {read_bytes} bytes at {hex(offset)}.",
        "Hard resetting via RTS pin...",
    )
    return CommandResult(
        lines=lines,
        exit_code=0,
        events=scenario_outcome.events,
        fields_correct=scenario_outcome.fields_correct,
    )


SPEC = CommandSpec(
    name="esptool.py", summary=SUMMARY, handler=handle, category=CommandCategory.FIRMWARE
)
