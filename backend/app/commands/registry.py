"""The closed set of commands this sandbox recognises.

A registry is an explicit allowlist. There is no discovery, no plugin
loading, and no lookup that can reach anything not registered here by name —
an unknown token resolves to nothing, which is what makes "unknown command"
a safe, boring outcome rather than an attempt to find something to run.

PHASE 2C: THE GENERIC HACK ENGINE'S TOOLBOX. The commands registered below
fall into the categories `CommandCategory` names (firmware / network / mqtt
/ serial / trainer). Every handler in every category reaches its target
information exclusively through `CommandContext.scenario` (see
`app/commands/base.py`, `app/scenarios/base.py`) or, for the serial category,
the shared hardware layer — never through a literal panel id or a
scenario's own configuration value written into the handler. That is what
keeps this table reusable across whichever scenario `create_default_scenario`
(`app/scenarios/__init__.py`) hands a session: adding a new training
activity means adding a new `Scenario` implementation and wiring it in
there, not touching this registry, the router, or any handler here.

Later phases add commands by writing a handler module and listing it in
`build_default_registry`. Nothing in the WebSocket layer changes.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandSpec
from app.commands.handlers import (
    clear,
    esptool_py,
    grep,
    help as help_command,
    history,
    mosquitto_pub,
    mosquitto_sub,
    nmap,
    serial_close,
    serial_monitor,
    serial_send,
    serial_status,
    strings,
)

#: Display order for `help`'s grouped listing — roughly the learning
#: progression (understand the device, then reach it over the network or a
#: wire, then its protocol), with trainer utilities last since they are not
#: tools against a target at all. Purely cosmetic: dispatch never consults
#: this tuple.
CATEGORY_DISPLAY_ORDER: tuple[CommandCategory, ...] = (
    CommandCategory.FIRMWARE,
    CommandCategory.NETWORK,
    CommandCategory.MQTT,
    CommandCategory.SERIAL,
    CommandCategory.TRAINER,
)


class CommandRegistry:
    """Name -> CommandSpec, with registration closed at build time."""

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        """Add a command. Registering a name twice is a programming error."""
        if spec.name in self._specs:
            raise ValueError(f"command already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CommandSpec | None:
        """Look up a command by the exact token typed, or None."""
        return self._specs.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> tuple[str, ...]:
        """Registered command names, sorted, so help output is stable."""
        return tuple(sorted(self._specs))

    def specs(self) -> tuple[CommandSpec, ...]:
        """Registered commands, sorted by name."""
        return tuple(self._specs[name] for name in self.names())

    def by_category(self) -> dict[CommandCategory, tuple[CommandSpec, ...]]:
        """Registered commands grouped by `CommandSpec.category`.

        Each group is name-sorted (via `specs()`), and only categories that
        actually have a registered command appear — so nothing here needs to
        change if a category is temporarily empty. Used by `help` to render
        the toolbox by section instead of one flat list.
        """
        grouped: dict[CommandCategory, list[CommandSpec]] = {}
        for spec in self.specs():
            grouped.setdefault(spec.category, []).append(spec)
        return {category: tuple(specs) for category, specs in grouped.items()}


def build_default_registry() -> CommandRegistry:
    """Build the command set this sandbox recognises.

    `help` is built last and against the finished registry: it is the one
    command whose output depends on what else is registered, so it takes the
    registry by closure instead of reaching for a module-level global.
    """
    registry = CommandRegistry()
    for spec in (
        clear.SPEC,
        esptool_py.SPEC,
        strings.SPEC,
        grep.SPEC,
        nmap.SPEC,
        mosquitto_sub.SPEC,
        mosquitto_pub.SPEC,
        # Phase 2A: the only commands in this table that touch real
        # hardware. They still resolve through the same closed allowlist as
        # every simulated tool — being physical buys them no special path.
        serial_status.SPEC,
        serial_monitor.SPEC,
        serial_send.SPEC,
        serial_close.SPEC,
        # Phase 2C: the trainer-utility side of the toolbox. Reads back the
        # Phase 2B event log (`context.session.recorder`); adds no scenario
        # knowledge and no new persistence.
        history.SPEC,
    ):
        registry.register(spec)
    registry.register(help_command.build_spec(registry))
    return registry


#: Process-wide default registry, used by the default router.
default_registry = build_default_registry()
