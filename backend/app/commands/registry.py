"""The closed set of commands this sandbox recognises.

A registry is an explicit allowlist. There is no discovery, no plugin
loading, and no lookup that can reach anything not registered here by name —
an unknown token resolves to nothing, which is what makes "unknown command"
a safe, boring outcome rather than an attempt to find something to run.

Later phases add commands by writing a handler module and listing it in
`build_default_registry`. Nothing in the WebSocket layer changes.
"""

from __future__ import annotations

from app.commands.base import CommandSpec
from app.commands.handlers import (
    clear,
    firmware_analyze,
    firmware_extract,
    help as help_command,
    mosquitto_pub,
    mosquitto_sub,
    mqtt_explorer,
    nmap,
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


def build_default_registry() -> CommandRegistry:
    """Build the Phase 2B command set.

    `help` is built last and against the finished registry: it is the one
    command whose output depends on what else is registered, so it takes the
    registry by closure instead of reaching for a module-level global.
    """
    registry = CommandRegistry()
    for spec in (
        clear.SPEC,
        firmware_extract.SPEC,
        firmware_analyze.SPEC,
        nmap.SPEC,
        mosquitto_sub.SPEC,
        mosquitto_pub.SPEC,
        mqtt_explorer.SPEC,
    ):
        registry.register(spec)
    registry.register(help_command.build_spec(registry))
    return registry


#: Process-wide default registry, used by the default router.
default_registry = build_default_registry()
