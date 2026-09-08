"""Dispatch: a completed command line in, a CommandResult out.

    raw line -> parser -> ParsedCommand -> registry -> handler -> CommandResult

The router knows nothing about WebSockets, xterm, JSON, or CRLF. It is a
pure function of (line, context) — which is what lets Phase 2D wire it to the
terminal, and a later phase replay it for grading, without either of them
reaching into the other.

It is also the containment boundary for failure. Every path out of
`dispatch` is a `CommandResult`: a blank line, unsupported syntax, an unknown
command, and a handler that raises all produce terminal text, never an
exception that would tear down a student's connection.
"""

from __future__ import annotations

import logging

from app.commands.base import CommandContext, CommandResult
from app.commands.parser import CommandSyntaxError, parse
from app.commands.registry import CommandRegistry, default_registry

logger = logging.getLogger(__name__)

#: Exit statuses, following shell convention so a later event recorder can
#: classify an attempt without re-reading its text.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 127

#: An unknown command name is quoted back to the student, the way a shell
#: does, because "typo: command not found" is the message that actually
#: helps. It is safe to echo: the parser has already rejected control
#: characters, so the name cannot carry an ANSI escape sequence — and it is
#: truncated so a very long token cannot flood the terminal.
MAX_ECHOED_NAME_CHARS = 48

_INTERNAL_ERROR_LINE = "internal error: the command could not be completed"


class CommandRouter:
    """Routes command lines to registered handlers."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry if registry is not None else default_registry

    @property
    def registry(self) -> CommandRegistry:
        return self._registry

    async def dispatch(self, raw: str, context: CommandContext) -> CommandResult:
        """Route one completed command line.

        Async even though every Phase 2B handler is synchronous and returns
        immediately. The await point belongs at the transport seam from the
        start: Phase 2C's simulated tools have durations (a scan takes time,
        a subscription waits for a message), and introducing that later would
        otherwise mean reworking the WebSocket layer rather than just the
        handlers.
        """
        try:
            command = parse(raw)
        except CommandSyntaxError as exc:
            # `exc` is built from the parser's own vocabulary, never from the
            # student's input, so this cannot reflect a payload back.
            return CommandResult.text(f"syntax error: {exc}", exit_code=EXIT_USAGE)

        if command is None:
            # Blank line: a prompt and nothing else, same as a real shell.
            return CommandResult.empty()

        spec = self._registry.get(command.name)
        if spec is None:
            return CommandResult.text(
                f"{_shorten(command.name)}: command not found",
                "Type 'help' to list the commands available in this sandbox.",
                exit_code=EXIT_NOT_FOUND,
            )

        try:
            return spec.handler(command, context)
        except Exception:
            # A bug in a handler is a server problem, not a student problem.
            # The detail goes to the server log; the terminal gets one
            # neutral line, with no exception type, message, or traceback.
            logger.exception(
                "command handler failed: command=%s session=%s",
                spec.name,
                context.session.session_id,
            )
            return CommandResult.text(_INTERNAL_ERROR_LINE, exit_code=EXIT_FAILURE)


def _shorten(name: str) -> str:
    """Cap an echoed token so it cannot flood the terminal."""
    if len(name) <= MAX_ECHOED_NAME_CHARS:
        return name
    return name[:MAX_ECHOED_NAME_CHARS] + "..."


#: Router used by the WebSocket endpoint.
default_router = CommandRouter()
