"""The backend's one sanctioned process-execution primitive.

Two layers of coverage:

1. Behaviour of `app.build.process.run_capture` against a small fake
   executable this file writes to a temp file and drives through
   `sys.executable` (a real, portable, always-present native executable —
   no dependency on arduino-cli being installed): exit code, stdout/stderr
   capture, timeout, and a missing executable. Every one of those is
   verified twice — once on an ordinary event loop, and once on an event
   loop that cannot spawn subprocesses at all (see below).
2. A static safety check on `app/build/process.py` itself: it is the only
   file in the backend allowed to name `subprocess`, and every *other*
   execution primitive the repo-wide guards forbid stays forbidden there
   too — including the bare identifier `shell`, which a literal
   `shell=True` keyword argument would require.

WHY THE HOSTILE-LOOP TESTS EXIST. This module was introduced to fix a real
runtime crash: `asyncio.create_subprocess_exec` is not implemented on every
event loop, and uvicorn (>= 0.36) runs the server on a `SelectorEventLoop`
on win32 whenever it needs worker subprocesses of its own — i.e. under
`--reload` or `--workers N`. Pressing Compile in Build Mode on Windows
therefore raised `NotImplementedError` out of
`loop._make_subprocess_transport` and killed the `/ws/build` handler, even
though `arduino-cli` itself worked fine from a terminal. The old tests all
passed because `asyncio.run` gives you a `ProactorEventLoop`, which does
support subprocesses — so the tests below deliberately run on a loop that
does not, which is the configuration that actually failed.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import textwrap
import tokenize

import pytest

from app.build.process import ProcessTimedOut, run_capture

PROCESS_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "app" / "build" / "process.py"
)


# --- running on an event loop that cannot spawn subprocesses ---------------


def run_on_ordinary_loop(make_coro):
    """Baseline: whatever loop `asyncio.run` picks (a ProactorEventLoop on win32)."""
    return asyncio.run(make_coro())


def run_without_loop_subprocess_support(make_coro):
    """Run a coroutine on a loop whose subprocess transport is unavailable.

    On win32 `asyncio.SelectorEventLoop` genuinely lacks subprocess support,
    which is exactly the uvicorn-`--reload` configuration that crashed. The
    `_make_subprocess_transport` override makes the same loop hostile on
    every other platform too, so this regression coverage is not silently
    Windows-only.
    """

    async def main():
        loop = asyncio.get_running_loop()

        async def refuse(*args, **kwargs):
            raise NotImplementedError

        loop._make_subprocess_transport = refuse
        return await make_coro()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(main())


def test_the_hostile_loop_really_cannot_spawn_subprocesses() -> None:
    """Guard: the regression tests below are not vacuous.

    If this ever stops raising, the loop used by those tests has gained
    subprocess support and they would no longer prove anything.
    """

    async def spawn():
        await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")

    with pytest.raises(NotImplementedError):
        run_without_loop_subprocess_support(spawn)


# --- a small, portable fake executable --------------------------------------
#
# Invoked as: <sys.executable> <this script> <instructions.json>
#
# It writes every argv it received to "<instructions.json>.argv.json", then
# behaves exactly as instructions.json says.

_FAKE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys
    import time

    instructions_path = sys.argv[1]
    with open(instructions_path, encoding="utf-8") as handle:
        instructions = json.load(handle)

    with open(instructions_path + ".argv.json", "w", encoding="utf-8") as handle:
        json.dump(sys.argv, handle)

    sleep_seconds = instructions.get("sleep", 0)
    if sleep_seconds:
        time.sleep(sleep_seconds)

    sys.stdout.write(instructions.get("stdout", ""))
    sys.stderr.write(instructions.get("stderr", ""))
    sys.exit(instructions.get("exit_code", 0))
    """
)


@pytest.fixture
def fake_executable(tmp_path: pathlib.Path) -> pathlib.Path:
    script_path = tmp_path / "fake_tool.py"
    script_path.write_text(_FAKE_SCRIPT, encoding="utf-8")
    return script_path


def write_instructions(path: pathlib.Path, **instructions) -> pathlib.Path:
    path.write_text(json.dumps(instructions), encoding="utf-8")
    return path


#: Both runners are exercised by every behavioural test below, so a future
#: change that quietly reintroduces a loop-dependent spawn fails here.
RUNNERS = (
    pytest.param(run_on_ordinary_loop, id="ordinary-loop"),
    pytest.param(run_without_loop_subprocess_support, id="no-subprocess-loop"),
)


@pytest.mark.parametrize("runner", RUNNERS)
def test_exit_code_and_streams_are_captured(
    runner, fake_executable: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=3,
        stdout="on stdout\n",
        stderr="on stderr\n",
    )
    args = [sys.executable, str(fake_executable), str(instructions)]

    result = runner(lambda: run_capture(args, timeout_seconds=30.0))

    assert result.exit_code == 3
    assert result.stdout.decode().strip() == "on stdout"
    assert result.stderr.decode().strip() == "on stderr"


@pytest.mark.parametrize("runner", RUNNERS)
def test_timeout_raises_process_timed_out(
    runner, fake_executable: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", sleep=10)
    args = [sys.executable, str(fake_executable), str(instructions)]

    with pytest.raises(ProcessTimedOut):
        runner(lambda: run_capture(args, timeout_seconds=0.3))


@pytest.mark.parametrize("runner", RUNNERS)
def test_missing_executable_raises_file_not_found(
    runner, tmp_path: pathlib.Path
) -> None:
    args = [str(tmp_path / "no-such-executable-anywhere")]

    with pytest.raises(FileNotFoundError):
        runner(lambda: run_capture(args, timeout_seconds=5.0))


@pytest.mark.parametrize("runner", RUNNERS)
def test_arguments_arrive_as_literal_argv_elements(
    runner, fake_executable: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A hostile-looking argument is one literal argv element, never shell text."""
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    hostile = "esp32:esp32:esp32; rm -rf / && echo pwned"
    args = [sys.executable, str(fake_executable), str(instructions), hostile]

    runner(lambda: run_capture(args, timeout_seconds=30.0))

    argv = json.loads(
        (tmp_path / "instructions.json.argv.json").read_text(encoding="utf-8")
    )
    # It arrived intact as exactly one element, never split on the shell
    # metacharacters it contains — nothing on this path is a shell.
    assert argv[-1] == hostile


# --- process.py static safety ----------------------------------------------

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
    }
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def test_process_module_stays_safe_except_for_its_one_sanctioned_exception() -> None:
    # NAME tokens only — this deliberately does not scan comments/docstrings
    # as raw text, so prose *describing* the forbidden APIs (as this
    # module's own docstring does) cannot produce a false positive here.
    names = _code_names(PROCESS_PATH)

    # Every forbidden name except "subprocess" remains banned here too —
    # this also means the bare identifier "shell" (which a literal
    # `shell=True` keyword argument would require) is still banned.
    other_forbidden = FORBIDDEN_NAMES - {"subprocess"}
    offenders = sorted(names & other_forbidden)
    assert offenders == [], f"execution primitive in process.py: {offenders}"

    # The exception is real (not vacuous) and taken through the safe API
    # that accepts an argument list — never `create_subprocess_shell`.
    assert "subprocess" in names
    assert "create_subprocess_shell" not in names
