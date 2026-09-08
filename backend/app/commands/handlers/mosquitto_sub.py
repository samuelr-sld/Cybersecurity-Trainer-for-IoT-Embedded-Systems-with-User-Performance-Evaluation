"""`mosquitto_sub` — subscribe to and observe the target's MQTT telemetry.

Thin adapter over `Scenario.observe`. Whether a subscription connects, and
what (if anything) it yields, is decided by the scenario against its own
broker/topic — this handler only extracts host, port, and topic.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Subscribe to an MQTT topic (mosquitto_sub -h <host> -t <topic>)."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-h", "--host", "-p", "--port", "-t", "--topic"})
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    topic = opts.value("-t", "--topic")
    return to_command_result(context.scenario.observe(host, port, topic))


SPEC = CommandSpec(name="mosquitto_sub", summary=SUMMARY, handler=handle)
