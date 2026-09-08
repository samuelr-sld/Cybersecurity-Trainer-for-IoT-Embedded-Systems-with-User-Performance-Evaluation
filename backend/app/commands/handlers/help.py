"""`help` — list the commands this sandbox recognises."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.commands.base import CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand

if TYPE_CHECKING:  # pragma: no cover
    # Import-time cycle: the registry imports every handler module, and this
    # one needs the registry's type. The annotation is all that is needed at
    # runtime (`from __future__ import annotations` defers it), so the import
    # is type-checking only.
    from app.commands.registry import CommandRegistry

SUMMARY = "List the commands available in this sandbox."


def build_spec(registry: "CommandRegistry") -> CommandSpec:
    """Build the `help` spec bound to the registry it will describe.

    Taking the registry by closure keeps `help` honest — it can only list
    what is actually registered — without a module-level global that would
    make two registries (a test one and the default one) interfere.
    """

    def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
        specs = registry.specs()
        width = max((len(spec.name) for spec in specs), default=0)
        lines = ["Available commands:"]
        lines.extend(f"  {spec.name.ljust(width)}  {spec.summary}" for spec in specs)
        lines.append("")
        lines.append("This is a training sandbox. Only the commands above are")
        lines.append("recognised, and none of them run on the host system.")
        return CommandResult.text(*lines)

    return CommandSpec(name="help", summary=SUMMARY, handler=handle)
