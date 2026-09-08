"""`clear` — reset the terminal screen."""

from __future__ import annotations

from app.commands.base import (
    CommandContext,
    CommandResult,
    CommandSpec,
    TerminalAction,
)
from app.commands.parser import ParsedCommand

SUMMARY = "Clear the terminal screen."


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    """Ask the terminal to clear itself.

    This returns an *action*, not output. The backend does not know how the
    front end clears its screen and should not: padding the output with
    newlines would scroll the screen rather than clear it, and emitting a raw
    ANSI sequence would move terminal-control knowledge out of the terminal.
    """
    return CommandResult.act(TerminalAction.CLEAR)


SPEC = CommandSpec(name="clear", summary=SUMMARY, handler=handle)
