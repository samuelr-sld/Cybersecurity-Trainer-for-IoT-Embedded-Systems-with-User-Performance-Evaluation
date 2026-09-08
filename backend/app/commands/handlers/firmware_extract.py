"""`firmware-extract` — simulated authorized firmware extraction (Stage 1).

WHY THIS COMMAND EXISTS. The Phase 2B tool set (nmap, mosquitto_*, mqtt-
explorer) covers only network and MQTT actions; none of them can represent
obtaining the device's firmware, which is a distinct step in the learning
progression (understand device -> extract firmware -> analyze -> ...). Rather
than overloading an unrelated tool, firmware handling gets its own command.

This is fully simulated: no serial port, no `esptool`, no real device. The
scenario flips `firmware_extracted` and produces a canned image summary.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Extract the target device's firmware image (simulated)."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    return to_command_result(context.scenario.extract_firmware())


SPEC = CommandSpec(name="firmware-extract", summary=SUMMARY, handler=handle)
