"""Dispatch: a completed command line in, a CommandResult out.

    raw line -> parser -> ParsedCommand -> registry -> handler -> CommandResult

The router knows nothing about WebSockets, xterm, JSON, or CRLF — which is
what lets Phase 2D wire it to the terminal, and a later phase replay it for
grading, without either of them reaching into the other.

It is not, however, side-effect free, and Phase 2B is where that stopped
being an incidental detail. Handlers already mutate the caller's scenario
through `context.scenario`; dispatch now also records the attempt and its
resulting domain events through `context.session.recorder`. Both effects are
reached through the caller's own context, never through process-wide state,
so "transport-independent" and "session-scoped" both still hold. Putting the
recording here rather than in `app/websocket.py` means every entry point —
the live socket, a test, a future replay harness — logs identically, and
there is exactly one place where a command's identity, its exit code, and
its events are all in scope at once.

It is also the containment boundary for failure. Every path out of
`dispatch` is a `CommandResult`: a blank line, unsupported syntax, an unknown
command, and a handler that raises all produce terminal text, never an
exception that would tear down a student's connection.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import replace
from datetime import datetime

from app.commands.base import CommandContext, CommandResult
from app.commands.parser import CommandSyntaxError, parse
from app.commands.registry import CommandRegistry, default_registry
from app.events.clock import utc_now

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
        """Route one completed command line, and record that it happened.

        Async from the start so the await point lived at the transport seam
        before anything needed it. Phase 2A is what needed it: the serial
        commands are `async def` because they talk to a real board, while
        every simulated tool stays synchronous. Both shapes are accepted —
        see the awaitable check below — so adding real I/O did not force a
        rewrite of eight working handlers.

        PHASE 2B: THIS IS WHERE EVENTS ARE RECORDED. Every path out of this
        method that represents a student action passes through
        `_record` before returning, so the durable log is written by the
        backend at the moment it decided what happened — from the parsed
        command name, the exit code, and the events the scenario engine
        emitted. Nothing downstream inspects terminal text to work out what a
        student did, and nothing upstream can assert that something happened.

        `started_at` is taken before the handler runs, so a slow command (a
        serial read against a real board) is stamped when the student
        submitted it, not when it finished.
        """
        started_at = utc_now()
        try:
            command = parse(raw)
        except CommandSyntaxError as exc:
            # `exc` is built from the parser's own vocabulary, never from the
            # student's input, so this cannot reflect a payload back.
            #
            # Recorded with no name and no arguments: the line never became a
            # command, and its raw text is the one string here that has not
            # passed the parser's control-character filter. But it IS an
            # action the student took, and an evaluator counting effort needs
            # to see it — see `HackCommandRecord`.
            self._record(context, None, (), EXIT_USAGE, handled=False, at=started_at)
            return CommandResult.text(f"syntax error: {exc}", exit_code=EXIT_USAGE)

        if command is None:
            # Blank line: a prompt and nothing else, same as a real shell.
            # Deliberately not recorded — pressing Enter is not an action, and
            # counting it would inflate every attempt-based metric.
            return CommandResult.empty()

        spec = self._registry.get(command.name)
        if spec is None:
            # `handled=False` is the durable form of "there was no such
            # tool", which a 127 exit code alone does not distinguish from a
            # tool that ran and reported failure.
            self._record(
                context,
                _shorten(command.name),
                command.argv,
                EXIT_NOT_FOUND,
                handled=False,
                at=started_at,
            )
            return CommandResult.text(
                f"{_shorten(command.name)}: command not found",
                "Type 'help' to list the commands available in this sandbox.",
                exit_code=EXIT_NOT_FOUND,
            )

        try:
            result = spec.handler(command, context)
            if inspect.isawaitable(result):
                # An `async def` handler — the serial commands, which do real
                # I/O against a physical board. Awaited *inside* this try, so
                # a failure during the await is contained exactly like a
                # synchronous handler's and still yields terminal text rather
                # than dropping the student's connection.
                result = await result
        except Exception:
            # A bug in a handler is a server problem, not a student problem.
            # The detail goes to the server log; the terminal gets one
            # neutral line, with no exception type, message, or traceback.
            #
            # The attempt is still recorded, with the failure exit code and
            # no events: the student did run a real command, and a crashed
            # handler must not quietly erase that from their log.
            logger.exception(
                "command handler failed: command=%s session=%s",
                spec.name,
                context.session.session_id,
            )
            self._record(
                context, spec.name, command.argv, EXIT_FAILURE, handled=True, at=started_at
            )
            return CommandResult.text(_INTERNAL_ERROR_LINE, exit_code=EXIT_FAILURE)

        self._record(
            context, spec.name, command.argv, result.exit_code, handled=True, at=started_at
        )
        # The scenario engine already decided which transitions actually
        # occurred; this only stamps and stores them. A command that changed
        # nothing carries no events and therefore adds no event rows.
        records = context.session.recorder.record_scenario_events(
            result.events, command=spec.name, exit_code=result.exit_code
        )
        return replace(result, records=records) if records else result

    @staticmethod
    def _record(
        context: CommandContext,
        name: str | None,
        argv: tuple[str, ...],
        exit_code: int,
        *,
        handled: bool,
        at: datetime,
    ) -> None:
        """Record one submitted command against its own session's log.

        Routed through `context.session.recorder`, so a command can only ever
        be written to the log of the session that ran it — there is no
        process-wide event list for it to land in by mistake.
        """
        context.session.recorder.record_command(
            name=name, argv=argv, exit_code=exit_code, handled=handled, occurred_at=at
        )


def _shorten(name: str) -> str:
    """Cap an echoed token so it cannot flood the terminal."""
    if len(name) <= MAX_ECHOED_NAME_CHARS:
        return name
    return name[:MAX_ECHOED_NAME_CHARS] + "..."


#: Router used by the WebSocket endpoint.
default_router = CommandRouter()
