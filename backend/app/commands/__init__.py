"""Controlled command router for Hack Mode terminal input.

Position in the pipeline:

    WebSocket -> Session -> Command Router -> Scenario Engine -> simulated IoT
                            ^^^^^^^^^^^^^^    tool handlers call the scenario
                                              via CommandContext.scenario

Responsibility split inside this package:

    parser.py     tokenise a completed command line into name + arguments
    registry.py   the closed allowlist of commands this sandbox recognises
    router.py     resolve a line to a handler and contain every failure
    base.py       the handler contract (CommandContext, CommandResult)
    handlers/     one module per command

SECURITY BOUNDARY — inherited from app/websocket.py and non-negotiable.
This package is NOT a shell. It resolves input against a fixed table of
simulated tools and produces text; it never delegates to the host operating
system. Nothing under `app/` may use `subprocess`, `os.system`/`os.popen`,
`shell=True`, `eval`/`exec`, a PTY, or a CMD/PowerShell/sh invocation, and
`tests/test_hack_backend.py` enforces that with a static scan of the package.
A student's keystrokes must never reach a host shell.

TERMINAL RESPONSIBILITY BOUNDARY. Line editing lives in the browser. xterm.js
owns character input, local echo, Backspace, Ctrl+C, and cursor behaviour;
this package receives the completed line after Enter. There is deliberately
no backend-side echo state, key handling, or line discipline here.
"""

from app.commands.base import (
    CommandContext,
    CommandResult,
    CommandSpec,
    HandlerFunction,
    TerminalAction,
)
from app.commands.parser import CommandSyntaxError, ParsedCommand, parse
from app.commands.registry import CommandRegistry, build_default_registry, default_registry
from app.commands.router import CommandRouter, default_router

__all__ = [
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandRouter",
    "CommandSpec",
    "CommandSyntaxError",
    "HandlerFunction",
    "ParsedCommand",
    "TerminalAction",
    "build_default_registry",
    "default_registry",
    "default_router",
    "parse",
]
