"""Tiny option reader for the simulated tools' argument lists.

The scenario tools (`nmap`, `mosquitto_sub`, `mosquitto_pub`, `mqtt-explorer`)
share a small, familiar option grammar: value options like `-h host`,
`-p 1883`, `-t topic`, `-m payload`, plus bare positional arguments (nmap's
target host). This turns an already-tokenised argument tuple into
`(options, positionals, flags)` so each handler can pull out what it needs
without repeating the walk.

This is NOT a general argument parser and does no expansion — the command
parser has already produced literal tokens. It only groups them. Value
options may be written spaced (`-p 1883`) or attached (`-p1883`); anything
else starting with `-` is treated as a bare flag.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedOptions:
    """Grouped view of one command's arguments."""

    options: dict[str, str]
    positionals: tuple[str, ...]
    flags: frozenset[str]

    def value(self, *names: str) -> str | None:
        """First present value among `names` (e.g. `value("-h", "--host")`)."""
        for name in names:
            if name in self.options:
                return self.options[name]
        return None

    def first_positional(self) -> str | None:
        return self.positionals[0] if self.positionals else None


def parse_options(
    args: tuple[str, ...], value_flags: set[str]
) -> ParsedOptions:
    """Group `args` given the set of options that take a value.

    `value_flags` is the set of option names consuming a following token
    (short forms may also be attached, e.g. `-p1883`). A value option at the
    very end of the args with no following token is recorded with an empty
    string, which the handler will treat as an invalid value.
    """
    options: dict[str, str] = {}
    positionals: list[str] = []
    flags: set[str] = set()

    short_value_flags = {name for name in value_flags if len(name) == 2}

    index = 0
    while index < len(args):
        token = args[index]

        if token in value_flags:
            if index + 1 < len(args):
                options[token] = args[index + 1]
                index += 2
            else:
                options[token] = ""
                index += 1
            continue

        attached = _attached_short(token, short_value_flags)
        if attached is not None:
            name, value = attached
            options[name] = value
            index += 1
            continue

        if token.startswith("-") and len(token) > 1:
            flags.add(token)
            index += 1
            continue

        positionals.append(token)
        index += 1

    return ParsedOptions(
        options=options,
        positionals=tuple(positionals),
        flags=frozenset(flags),
    )


def _attached_short(token: str, short_value_flags: set[str]) -> tuple[str, str] | None:
    """Split an attached short option like `-p1883` into (`-p`, `1883`)."""
    if len(token) > 2 and token[:2] in short_value_flags:
        return token[:2], token[2:]
    return None


def to_port(value: str | None) -> int | None:
    """Parse a port string into a valid TCP port, or None.

    None is returned for a missing option (`-p` not given) and for an
    unparseable or out-of-range value alike. A handler passing this to the
    scenario means "no specific port", which the scenario reads as the default
    broker port — so a garbage `-p` value degrades to the default rather than
    raising.
    """
    if value is None:
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None
