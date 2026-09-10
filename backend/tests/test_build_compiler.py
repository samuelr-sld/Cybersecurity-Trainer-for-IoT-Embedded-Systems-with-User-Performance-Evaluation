"""Phase 3B verification: the Arduino CLI compiler adapter.

Two layers of coverage:

1. Behaviour of `ArduinoCliCompiler` against a small fake "compiler"
   executable this file writes to a temp file and drives through
   `sys.executable` (a real, portable, always-present native executable —
   no dependency on arduino-cli being installed). This covers success,
   real exit-code failure, stdout/stderr capture, timeout handling, a
   missing executable, and that arguments reach the subprocess as a literal
   argv array rather than through any shell interpretation. Every one of
   those behaviours is then re-verified on an event loop that cannot spawn
   subprocesses itself — the Windows/uvicorn-`--reload` configuration that
   used to crash `/ws/build` with `NotImplementedError`.
2. A static safety check on `app/build/compiler.py` itself: every execution
   primitive Hack Mode's and Build Mode's other tests forbid stays forbidden
   there, with no exception at all — this module builds the argument array
   and interprets the result, while `app/build/process.py` is the only file
   in the backend allowed to spawn a process.

A third, optional layer runs only when a real `arduino-cli` is reachable via
`config.ARDUINO_CLI_PATH` (see app/config.py — override with
`TRAINER_ARDUINO_CLI_PATH` on a machine where it isn't on PATH): a genuine
end-to-end compile of the real default Build Mode project (the LED Blink
pipeline-proof project as of Phase 1 — see app/build/blink.py), both with
its default (compiling) editable region and with a deliberately broken one.
These are skipped, never faked, when no real toolchain is configured.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
import textwrap
import tokenize

import pytest

from app import config
from app.build import (
    BLINK_REGION_ID,
    ArduinoCliCompiler,
    CompileFailureCategory,
    CompileRequest,
    create_default_workspace,
)

COMPILER_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "app" / "build" / "compiler.py"
)


def run(coro):
    return asyncio.run(coro)


# --- a small, portable fake "compiler" executable ---------------------------
#
# Invoked as: <sys.executable> <this script> <instructions.json> compile ...
#
# It writes every argv it received to "<instructions.json>.argv.json" (for
# the argument-safety test), then behaves exactly as instructions.json says.

_FAKE_COMPILER_SCRIPT = textwrap.dedent(
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
def fake_compiler_script(tmp_path: pathlib.Path) -> pathlib.Path:
    script_path = tmp_path / "fake_arduino_cli.py"
    script_path.write_text(_FAKE_COMPILER_SCRIPT, encoding="utf-8")
    return script_path


def make_compiler(script_path: pathlib.Path, instructions_path: pathlib.Path) -> ArduinoCliCompiler:
    """An ArduinoCliCompiler that runs the fake script via sys.executable."""
    return ArduinoCliCompiler(
        sys.executable, prefix_args=(str(script_path), str(instructions_path))
    )


def write_instructions(path: pathlib.Path, **instructions) -> pathlib.Path:
    path.write_text(json.dumps(instructions), encoding="utf-8")
    return path


def make_request(tmp_path: pathlib.Path, instructions_path: pathlib.Path, **overrides) -> CompileRequest:
    defaults = dict(
        sketch_dir=tmp_path / "sketch",
        fqbn="esp32:esp32:esp32",
        build_path=tmp_path / "build",
        timeout_seconds=5.0,
    )
    defaults.update(overrides)
    (defaults["sketch_dir"]).mkdir(parents=True, exist_ok=True)
    # `instructions_path` rides along as an extra prefix arg via the
    # compiler's `prefix_args`, not through CompileRequest — see make_compiler.
    return CompileRequest(**defaults)


# --- 1: successful invocation ------------------------------------------------


def test_successful_invocation_reports_success(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json", exit_code=0, stdout="Sketch uses 42 bytes.\n"
    )
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions)

    outcome = run(compiler.run_compile(request))

    assert outcome.success is True
    assert outcome.category is CompileFailureCategory.NONE
    assert outcome.exit_code == 0
    assert "Sketch uses 42 bytes." in outcome.stdout


# --- 2: real exit-code failure -----------------------------------------------


def test_nonzero_exit_code_reports_compiler_error(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=1,
        stderr="main.ino:5:1: error: expected ';' before '}' token\n",
    )
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions)

    outcome = run(compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.COMPILER_ERROR
    assert outcome.exit_code == 1
    assert "error: expected ';'" in outcome.stderr


# --- 3: stdout/stderr capture (both at once) --------------------------------


def test_stdout_and_stderr_are_both_captured(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=0,
        stdout="progress line one\nprogress line two\n",
        stderr="a warning on stderr\n",
    )
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions)

    outcome = run(compiler.run_compile(request))

    assert "progress line one" in outcome.stdout
    assert "progress line two" in outcome.stdout
    assert "a warning on stderr" in outcome.stderr


# --- 4: timeout handling -----------------------------------------------------


def test_timeout_kills_the_process_and_reports_truthfully(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", sleep=10, exit_code=0)
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions, timeout_seconds=0.3)

    outcome = run(compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.TIMEOUT
    # The call returned promptly (bounded by the timeout), not after the
    # fake script's full 10-second sleep — proving the process was actually
    # killed rather than merely abandoned.
    assert outcome.duration_seconds < 5


# --- 5: Arduino CLI missing / unavailable -----------------------------------


def test_missing_executable_reports_toolchain_unavailable(tmp_path: pathlib.Path) -> None:
    compiler = ArduinoCliCompiler(str(tmp_path / "no-such-arduino-cli-binary"))
    request = make_request(tmp_path, tmp_path / "unused.json")

    outcome = run(compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.TOOLCHAIN_UNAVAILABLE
    assert outcome.exit_code is None


# --- 6: safe argument construction ------------------------------------------


def test_arguments_reach_the_process_as_a_literal_array(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A hostile-looking FQBN/path is one literal argv element, never shell text."""
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    compiler = make_compiler(fake_compiler_script, instructions)
    hostile_fqbn = "esp32:esp32:esp32; rm -rf / && echo pwned"
    request = make_request(tmp_path, instructions, fqbn=hostile_fqbn)

    run(compiler.run_compile(request))

    argv = json.loads((tmp_path / "instructions.json.argv.json").read_text(encoding="utf-8"))
    # The hostile string arrived intact as exactly one argv element...
    assert hostile_fqbn in argv
    # ...immediately after "--fqbn", never split on the shell metacharacters
    # it contains, and never interpreted (no "pwned" side effect is possible
    # here since nothing this process ran was a shell).
    fqbn_index = argv.index("--fqbn")
    assert argv[fqbn_index + 1] == hostile_fqbn


def test_command_is_built_as_compile_with_fqbn_and_build_path(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions, fqbn="esp32:esp32:esp32")

    run(compiler.run_compile(request))

    argv = json.loads((tmp_path / "instructions.json.argv.json").read_text(encoding="utf-8"))
    assert "compile" in argv
    assert "--fqbn" in argv
    assert "--build-path" in argv
    assert str(request.sketch_dir) in argv


# --- 7: regression — an event loop that cannot spawn subprocesses -----------
#
# The Windows failure this section exists for: uvicorn (>= 0.36) picks the
# event loop with `uvicorn/loops/asyncio.py::asyncio_loop_factory`, and on
# win32 that returns a `SelectorEventLoop` whenever uvicorn needs worker
# subprocesses of its own — i.e. under `--reload` or `--workers N`, which is
# how this backend is run in development. A Windows `SelectorEventLoop` does
# not implement `loop._make_subprocess_transport`, so the compiler's old
# `asyncio.create_subprocess_exec` call raised `NotImplementedError` and
# killed the `/ws/build` handler the first time a student pressed Compile,
# with a perfectly working `arduino-cli` installed.
#
# Every test above runs under `asyncio.run`, which gives a `ProactorEventLoop`
# on win32 — which is exactly why none of them caught it. The tests below
# repeat the adapter's whole contract (success, real exit code, stdout and
# stderr capture, timeout, missing toolchain, failure categorization) on a
# loop that genuinely cannot spawn a subprocess itself.


def run_without_loop_subprocess_support(make_coro):
    """Run a coroutine on a loop whose subprocess transport is unavailable.

    On win32 `asyncio.SelectorEventLoop` genuinely lacks subprocess support,
    reproducing the uvicorn-`--reload` configuration that crashed. The
    `_make_subprocess_transport` override makes the same loop hostile on
    every other platform too, so this coverage is not silently Windows-only.
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
    """Guard: the regression tests below are not vacuous."""

    async def spawn():
        await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")

    with pytest.raises(NotImplementedError):
        run_without_loop_subprocess_support(spawn)


def test_compile_succeeds_on_a_loop_without_subprocess_support(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=0,
        stdout="Sketch uses 42 bytes.\n",
        stderr="a warning on stderr\n",
    )
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions)

    outcome = run_without_loop_subprocess_support(lambda: compiler.run_compile(request))

    assert outcome.success is True
    assert outcome.category is CompileFailureCategory.NONE
    assert outcome.exit_code == 0
    assert "Sketch uses 42 bytes." in outcome.stdout
    assert "a warning on stderr" in outcome.stderr


def test_compile_failure_is_categorized_on_a_loop_without_subprocess_support(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=1,
        stderr="main.ino:5:1: error: expected ';' before '}' token\n",
    )
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions)

    outcome = run_without_loop_subprocess_support(lambda: compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.COMPILER_ERROR
    assert outcome.exit_code == 1
    assert "error: expected ';'" in outcome.stderr


def test_compile_timeout_is_honoured_on_a_loop_without_subprocess_support(
    fake_compiler_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", sleep=10, exit_code=0)
    compiler = make_compiler(fake_compiler_script, instructions)
    request = make_request(tmp_path, instructions, timeout_seconds=0.3)

    outcome = run_without_loop_subprocess_support(lambda: compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.TIMEOUT
    # Bounded by the timeout, not by the fake script's full 10-second sleep —
    # the process was really killed, not merely abandoned.
    assert outcome.duration_seconds < 5


def test_missing_executable_is_categorized_on_a_loop_without_subprocess_support(
    tmp_path: pathlib.Path,
) -> None:
    compiler = ArduinoCliCompiler(str(tmp_path / "no-such-arduino-cli-binary"))
    request = make_request(tmp_path, tmp_path / "unused.json")

    outcome = run_without_loop_subprocess_support(lambda: compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.TOOLCHAIN_UNAVAILABLE
    assert outcome.exit_code is None


# --- compiler.py static safety ----------------------------------------------

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


def test_compiler_module_stays_safe_and_delegates_execution() -> None:
    # NAME tokens only — this deliberately does not scan comments/docstrings
    # as raw text, so prose *describing* the forbidden APIs (as this
    # module's own docstring does) cannot produce a false positive here.
    names = _code_names(COMPILER_PATH)

    # This module builds the argv and reads the result; `app/build/process.py`
    # is the only file allowed to spawn anything. So the *full* forbidden set
    # applies here with no exception at all — including "subprocess" itself,
    # and the bare identifier "shell" that a literal `shell=True` keyword
    # argument would require.
    offenders = sorted(names & FORBIDDEN_NAMES)
    assert offenders == [], f"execution primitive in compiler.py: {offenders}"

    # The delegation is real, not vacuous: the module actually calls the
    # sanctioned primitive, and never the shell-string-taking asyncio API.
    assert "run_capture" in names
    assert "create_subprocess_shell" not in names


# --- optional: real arduino-cli, only when actually available --------------

_REAL_ARDUINO_CLI = shutil.which(config.ARDUINO_CLI_PATH)

_SKIP_REASON = (
    f"arduino-cli not available via config.ARDUINO_CLI_PATH={config.ARDUINO_CLI_PATH!r}; "
    "set TRAINER_ARDUINO_CLI_PATH to a working arduino-cli to run this test"
)


@pytest.mark.skipif(_REAL_ARDUINO_CLI is None, reason=_SKIP_REASON)
def test_real_default_blink_project_compiles_successfully(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: the real toolchain actually builds this project's firmware."""
    workspace = create_default_workspace()
    sketch_dir = workspace.materialize(tmp_path / "sketch")
    request = CompileRequest(
        sketch_dir=sketch_dir,
        fqbn=workspace.project.board.fqbn,
        build_path=tmp_path / "build",
        timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
    )
    compiler = ArduinoCliCompiler(config.ARDUINO_CLI_PATH)

    outcome = run(compiler.run_compile(request))

    assert outcome.success is True, outcome.stderr or outcome.stdout
    assert outcome.exit_code == 0


@pytest.mark.skipif(_REAL_ARDUINO_CLI is None, reason=_SKIP_REASON)
def test_real_broken_blink_region_fails_a_real_compile(tmp_path: pathlib.Path) -> None:
    """The real compiler, not a regex, is what decides this fails."""
    workspace = create_default_workspace()
    workspace.update_region(
        "main.ino", BLINK_REGION_ID, "void setup() { ((( ;;; not valid c++ at all"
    )
    sketch_dir = workspace.materialize(tmp_path / "sketch")
    request = CompileRequest(
        sketch_dir=sketch_dir,
        fqbn=workspace.project.board.fqbn,
        build_path=tmp_path / "build",
        timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
    )
    compiler = ArduinoCliCompiler(config.ARDUINO_CLI_PATH)

    outcome = run(compiler.run_compile(request))

    assert outcome.success is False
    assert outcome.category is CompileFailureCategory.COMPILER_ERROR
    assert outcome.exit_code not in (0, None)
