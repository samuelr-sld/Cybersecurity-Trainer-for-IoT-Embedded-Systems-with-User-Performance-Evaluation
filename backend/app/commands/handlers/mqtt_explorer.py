"""`mqtt-explorer` — browse the broker's topic tree / observe telemetry.

The command name is not a legal Python identifier, so the module is named with
an underscore and the name is carried by the spec. Thin adapter over
`Scenario.explore`; the scenario decides what the topic tree reveals and only
exposes the device topic once it has been recovered from firmware.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Browse an MQTT broker's topic tree (mqtt-explorer -h <host>)."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-h", "--host", "-p", "--port"})
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    return to_command_result(context.scenario.explore(host, port))


SPEC = CommandSpec(name="mqtt-explorer", summary=SUMMARY, handler=handle)
