"""`help` — list the commands this sandbox recognises.

PHASE 2C: grouped by `CommandCategory` (firmware / network / mqtt / serial /
trainer) rather than one flat alphabetical list, so the toolbox's shape —
the same five sections `app/commands/registry.py` documents — is visible to
a student reading `help`, not just to a developer reading the source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand

if TYPE_CHECKING:  # pragma: no cover
    # Import-time cycle: the registry imports every handler module, and this
    # one needs the registry's type. The annotation is all that is needed at
    # runtime (`from __future__ import annotations` defers it), so the import
    # is type-checking only.
    from app.commands.registry import CommandRegistry

SUMMARY = "List the commands available in this sandbox."

#: Human-facing section headings. A category with no registered command is
#: simply absent from the output (see `CommandRegistry.by_category`), so
#: this table can list every member without special-casing an empty one.
_CATEGORY_TITLES: dict[CommandCategory, str] = {
    CommandCategory.FIRMWARE: "Firmware / analysis",
    CommandCategory.NETWORK: "Network reconnaissance",
    CommandCategory.MQTT: "MQTT",
    CommandCategory.SERIAL: "Serial / hardware",
    CommandCategory.TRAINER: "Trainer utilities",
}


def build_spec(registry: "CommandRegistry") -> CommandSpec:
    """Build the `help` spec bound to the registry it will describe.

    Taking the registry by closure keeps `help` honest — it can only list
    what is actually registered — without a module-level global that would
    make two registries (a test one and the default one) interfere.
    """

    def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
        grouped = registry.by_category()
        all_specs = registry.specs()
        width = max((len(spec.name) for spec in all_specs), default=0)

        lines = ["Available commands:"]
        # Imported here, not at module scope: `registry.py` and `help.py`
        # already have an import-time cycle (see the TYPE_CHECKING note
        # above), and `CATEGORY_DISPLAY_ORDER` lives in the module that
        # imports this one.
        from app.commands.registry import CATEGORY_DISPLAY_ORDER

        for category in CATEGORY_DISPLAY_ORDER:
            specs = grouped.get(category)
            if not specs:
                continue
            lines.append("")
            lines.append(f"{_CATEGORY_TITLES[category]}:")
            lines.extend(f"  {spec.name.ljust(width)}  {spec.summary}" for spec in specs)

        lines.append("")
        lines.append("This is a training sandbox. Only the commands above are")
        lines.append("recognised, and none of them run on the host system.")
        return CommandResult.text(*lines)

    return CommandSpec(
        name="help", summary=SUMMARY, handler=handle, category=CommandCategory.TRAINER
    )
