"""The command-handler contract.

A handler is a plain function:

    (ParsedCommand, CommandContext) -> CommandResult

It returns a *structured* result and never touches the WebSocket, the
terminal, or any I/O. That is what keeps the router transport-independent:
the same handler can be driven by the WebSocket endpoint, by a test, or by a
future replay/grading harness, because none of them are visible from here.

    parser -> ParsedCommand -> registry -> handler -> CommandResult -> transport
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Callable, Protocol

from app.commands.parser import ParsedCommand
from app.sessions import HackSession

if TYPE_CHECKING:  # pragma: no cover
    from app.events import HackEventRecord
    from app.scenarios import Scenario
    from app.scenarios.events import ScenarioEvent


class TerminalAction(str, Enum):
    """A structured instruction to the terminal front end.

    Some commands act on the terminal itself rather than writing to it.
    `clear` is the current example: emitting a screenful of newlines would be
    a crude imitation that scrolls history instead of clearing it, and
    emitting a raw ANSI sequence would put terminal-control knowledge in the
    backend, which the responsibility boundary places in the front end.

    So the backend says *what* it wants; the front end decides *how*. The
    transport carries this as its own frame type (see `ActionMessage` in
    app/models/messages.py) — the value strings must stay in step.
    """

    CLEAR = "clear"


class CommandCategory(str, Enum):
    """The toolbox section a registered command belongs to — Phase 2C.

    Purely organisational metadata on `CommandSpec`: `help` groups its
    listing by category (see `app/commands/handlers/help.py`), and a future
    frontend could do the same. Nothing in the router or in any handler
    branches on this value — it carries no permission, no validation, and no
    behavioural meaning, which is what keeps it safe to add without touching
    dispatch. It is the "category" column the Phase 2C command registry asks
    for; "aliases" and a declarative "argument specification" are not added
    alongside it because nothing in this sandbox currently needs either —
    every command has exactly one name, and each handler already validates
    its own arguments and returns `ScenarioOutcome.usage(...)`/`EXIT_USAGE`
    for a bad one, which is what the test suite already exercises.

    The five members mirror the five boxes of the generic Hack Engine this
    phase formalises (firmware / network / mqtt / serial) plus the trainer
    utilities that are not tools against a target at all.
    """

    FIRMWARE = "firmware"
    NETWORK = "network"
    MQTT = "mqtt"
    SERIAL = "serial"
    TRAINER = "trainer"


@dataclass(frozen=True)
class CommandResult:
    """What a handler produced.

    `lines` are terminal lines *without* line terminators — the transport
    chooses the line ending, because CRLF-vs-LF is a property of the terminal
    protocol and not of the command.

    `exit_code` follows shell convention (0 success, 127 command not found)
    so the event recorder in a later phase can classify an attempt without
    re-reading the text.

    `events` carries the scenario domain events (if any) this command
    produced, forwarded from `ScenarioOutcome.events` by
    `app.commands.scenario_adapter.to_command_result`. The router does not
    interpret them; the transport turns them into `event` (and, when present,
    `state`) frames — see `app/websocket.py`.

    `records` is those same events after the Phase 2B recorder has stamped
    them: same events, same order, one `HackEventRecord` each, now carrying a
    server-generated timestamp, a per-session sequence number, and the
    session they belong to. HANDLERS NEVER SET IT — it is filled in by
    `CommandRouter.dispatch` on the way out, which is the only place that
    knows both the command's identity and the session's recorder. The
    transport renders `event` frames from these rather than from `events`,
    so what the terminal shows and what the database holds are the same rows.
    An empty tuple alongside a non-empty `events` means the result never went
    through the router (a handler called directly in a test).
    """

    lines: tuple[str, ...] = ()
    actions: tuple[TerminalAction, ...] = ()
    exit_code: int = 0
    events: tuple["ScenarioEvent", ...] = ()
    records: tuple["HackEventRecord", ...] = ()

    @classmethod
    def text(cls, *lines: str, exit_code: int = 0) -> CommandResult:
        """Result that only writes lines to the terminal."""
        return cls(lines=tuple(lines), exit_code=exit_code)

    @classmethod
    def act(cls, *actions: TerminalAction, exit_code: int = 0) -> CommandResult:
        """Result that only asks the terminal to do something."""
        return cls(actions=tuple(actions), exit_code=exit_code)

    @classmethod
    def empty(cls) -> CommandResult:
        """Result that produces nothing — a blank command line."""
        return cls()


@dataclass(frozen=True)
class CommandContext:
    """Everything a handler is allowed to see about its caller.

    Two things, both per-session, which is what makes handlers per-student:
    two connections get two `HackSession` objects — and therefore two
    `Scenario` objects — and cannot observe each other.

    - `session`: terminal geometry and identity.
    - `scenario`: the simulated target the command acts against (Phase 2C).

    `scenario` is exposed as a property delegating to the session rather than
    a second stored field, so there is a single source of truth: the session
    owns the scenario, and there is no way for a context to drift out of sync
    with the session it was built from. Handlers therefore did not change
    shape — they still receive `(command, context)` — they simply now reach
    the target through `context.scenario`.
    """

    session: HackSession

    @property
    def scenario(self) -> "Scenario":
        """The caller's per-session simulated target."""
        return self.session.scenario


class CommandHandler(Protocol):
    """Callable shape every handler satisfies.

    May return a `CommandResult` directly, or a coroutine yielding one —
    the router awaits whichever it gets (see `app/commands/router.py`).
    """

    def __call__(
        self, command: ParsedCommand, context: CommandContext
    ) -> "CommandResult | Awaitable[CommandResult]": ...


#: Convenience alias for annotating handler functions.
#:
#: Phase 2A widened this to allow `async def` handlers. The simulated tools
#: are pure functions of the scenario and stay synchronous; the serial
#: commands perform real I/O against a physical board and cannot. Rather
#: than force every handler into one shape, the router accepts both — which
#: is exactly the await point `CommandRouter.dispatch` was made `async` for
#: in the first place.
HandlerFunction = Callable[
    [ParsedCommand, CommandContext], "CommandResult | Awaitable[CommandResult]"
]


@dataclass(frozen=True)
class CommandSpec:
    """A registered command: its name, its one-line help, its handler.

    `name` is the exact token a student types, so it may contain characters
    that are illegal in a Python identifier (`esptool.py`). The module
    filename and the command name are therefore independent.

    `category` (Phase 2C) is display metadata only — see `CommandCategory`.
    """

    name: str
    summary: str
    handler: HandlerFunction
    category: CommandCategory = CommandCategory.TRAINER

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a command spec needs a name")
