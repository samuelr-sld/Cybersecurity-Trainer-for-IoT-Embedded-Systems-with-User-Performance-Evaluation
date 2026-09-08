"""`nmap` — network/port reconnaissance against the simulated target.

Delegates entirely to the scenario: the target IP, the MQTT port, and whether
the service is up are scenario-owned facts, not constants in this handler.
This handler only turns the student's arguments into a scan request.
"""

from __future__ import annotations

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.options import parse_options, to_port
from app.commands.parser import ParsedCommand
from app.commands.scenario_adapter import to_command_result

SUMMARY = "Scan a host for open ports and services (e.g. nmap -p 1883 <host>)."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    opts = parse_options(command.args, {"-p", "--port"})
    host = opts.value("-h", "--host") or opts.first_positional()
    port = to_port(opts.value("-p", "--port"))
    return to_command_result(context.scenario.scan(host, port))


SPEC = CommandSpec(name="nmap", summary=SUMMARY, handler=handle)
