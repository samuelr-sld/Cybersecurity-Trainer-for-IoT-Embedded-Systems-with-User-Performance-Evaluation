"""`mosquitto_pub` — publish (potentially manipulated) data to the target.

Thin adapter over `Scenario.publish`. The scenario models the vulnerability:
it decides whether a publish reaches the broker, whether the target consumes
the topic, whether the payload is a usable reading, and whether the target's
reported state therefore changes. This handler only extracts the arguments.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Publish a payload to an MQTT topic (mosquitto_pub -h <host> -t <topic> -m <payload>)."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(
        command.args,
        {"-h", "--host", "-p", "--port", "-t", "--topic", "-m", "--message"},
    )
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    topic = opts.value("-t", "--topic")
    message = opts.value("-m", "--message")
    return to_command_result(context.scenario.publish(host, port, topic, message))


SPEC = CommandSpec(name="mosquitto_pub", summary=SUMMARY, handler=handle)
