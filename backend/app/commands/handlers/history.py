"""`history` — list the commands this session has run so far.

PHASE 2C TRAINER UTILITY. Deliberately the most generic command in the
toolbox: it has no scenario knowledge at all and does not import anything
from `app.scenarios`. It reads back `context.session.recorder.commands` —
the same per-session, in-memory command log the Phase 2B event recorder
already keeps for every dispatched command (see `app/events/recorder.py`)
— so this command adds no new state, no new persistence, and no new event
type. It exists purely to read the existing seam back out.

Because the router records a command only *after* its handler returns (see
`CommandRouter.dispatch`), a `history` invocation never lists itself — it
shows exactly what happened before the student typed this one.
"""

from __future__ import annotations

from app.commands.base import CommandCategory, CommandContext, CommandResult, CommandSpec
from app.commands.parser import ParsedCommand

SUMMARY = "List the commands run so far in this session."

#: Bounds how many rows are echoed back, for the same reason
#: `app/events/records.py` bounds a stored argument list: a very long
#: session must not be able to flood one reply with an unbounded echo.
MAX_SHOWN = 50


def handle(command: ParsedCommand, context: CommandContext) -> CommandResult:
    records = context.session.recorder.commands
    if not records:
        return CommandResult.text("No commands recorded yet.")

    shown = records[-MAX_SHOWN:]
    lines = ["Command history for this session:"]
    width = max(len(str(record.sequence)) for record in shown)
    for record in shown:
        # `name` is None only for a line that failed to parse at all (see
        # `HackCommandRecord`); echoed as the literal token students typed,
        # never re-interpreted.
        label = record.name if record.name is not None else "(unparsed)"
        argv_tail = " ".join(record.argv[1:]) if len(record.argv) > 1 else ""
        text = f"{label} {argv_tail}".rstrip()
        lines.append(
            f"  #{str(record.sequence).rjust(width)}  {text}  (exit {record.exit_code})"
        )
    if len(records) > MAX_SHOWN:
        lines.append(f"  ... {len(records) - MAX_SHOWN} earlier command(s) not shown")
    return CommandResult.text(*lines)


SPEC = CommandSpec(name="history", summary=SUMMARY, handler=handle, category=CommandCategory.TRAINER)
