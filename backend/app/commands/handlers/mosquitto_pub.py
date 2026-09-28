"""`mosquitto_pub` — publish (potentially manipulated) data to the target.

Thin adapter over `Scenario.publish`. The scenario models the vulnerability:
it decides whether a publish reaches the broker, whether the target consumes
the topic, whether the payload is a usable reading, and whether the target's
reported state therefore changes. This handler only extracts the arguments.

STAYS GENERIC. This knows nothing about a broker, a topic, Panel 1, or whether
the scenario is simulated or backed by a real MQTT connection — it parses
options and calls one scenario method. It runs that method on a worker thread
(`asyncio.to_thread`), for the same reason the serial commands are async: a
scenario MAY do bounded blocking I/O against a real broker, and that must not
stall the event loop. A purely in-memory scenario runs there just as happily.
"""

from __future__ import annotations

import asyncio

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Publish a payload to an MQTT topic (mosquitto_pub -h <host> -t <topic> -m <payload>)."


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(
        command.args,
        {"-h", "--host", "-p", "--port", "-t", "--topic", "-m", "--message"},
    )
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    topic = opts.value("-t", "--topic")
    message = opts.value("-m", "--message")
    outcome = await asyncio.to_thread(
        context.scenario.publish, host, port, topic, message
    )
    return to_command_result(outcome)


SPEC = CommandSpec(
    name="mosquitto_pub", summary=SUMMARY, handler=handle, category=CommandCategory.MQTT
)
