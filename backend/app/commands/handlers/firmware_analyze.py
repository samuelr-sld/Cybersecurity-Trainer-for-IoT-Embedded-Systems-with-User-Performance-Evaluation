"""`firmware-analyze` — simulated firmware analysis (Stage 2).

Second half of the firmware step that the Phase 2B tool set cannot express
(see `firmware_extract.py`). Analysis is what recovers the MQTT configuration
(broker address, port, topic) from the extracted image, so the student is not
handed the topic before doing the work. The scenario enforces that firmware
must be extracted first.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Analyze extracted firmware to recover its MQTT configuration."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    return to_command_result(context.scenario.analyze_firmware())


SPEC = CommandSpec(name="firmware-analyze", summary=SUMMARY, handler=handle)
