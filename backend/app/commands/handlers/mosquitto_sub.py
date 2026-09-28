"""`mosquitto_sub` — subscribe to and observe the target's MQTT telemetry.

Thin adapter over `Scenario.observe`. Whether a subscription connects, and
what (if anything) it yields, is decided by the scenario against its own
broker/topic — this handler only extracts host, port, and topic.

STAYS GENERIC, RUNS OFF THE LOOP. Like `mosquitto_pub`, this knows nothing of
brokers, topics, panels, or whether the scenario is simulated or live. It runs
the scenario method on a worker thread (`asyncio.to_thread`) so a bounded live
subscription cannot stall the event loop; an in-memory scenario is unaffected.
"""

from __future__ import annotations

import asyncio

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Subscribe to an MQTT topic (mosquitto_sub -h <host> -t <topic>)."


async def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-h", "--host", "-p", "--port", "-t", "--topic"})
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    topic = opts.value("-t", "--topic")
    outcome = await asyncio.to_thread(context.scenario.observe, host, port, topic)
    return to_command_result(outcome)


SPEC = CommandSpec(
    name="mosquitto_sub", summary=SUMMARY, handler=handle, category=CommandCategory.MQTT
)
