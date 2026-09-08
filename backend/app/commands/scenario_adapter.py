"""Adapter from a `ScenarioOutcome` to a `CommandResult`.

The scenario layer speaks `ScenarioOutcome` (text lines + success/exit +
domain events); the transport layer speaks `CommandResult` (lines + terminal
actions + exit code + events). This one function bridges them so every tool
handler does the same thing the same way:

    outcome = context.scenario.<operation>(...)
    return to_command_result(outcome)

The scenario's `events` are forwarded onto the `CommandResult` rather than
dropped: they are still retained in the engine's own ordered log
(`scenario.events`) for Phase 2E to read, but Phase 2D-A also needs them at
the transport seam so `app/websocket.py` can render them as `event` frames
(and follow them with a `state` snapshot) without reaching into the scenario
itself. Keeping the mapping this thin is what lets handlers stay free of both
scenario internals and wire concerns.
"""

from __future__ import annotations

from app.commands.base import CommandResult
from app.scenarios import ScenarioOutcome


def to_command_result(outcome: ScenarioOutcome) -> CommandResult:
    return CommandResult(
        lines=tuple(outcome.lines),
        exit_code=outcome.exit_code,
        events=outcome.events,
    )
