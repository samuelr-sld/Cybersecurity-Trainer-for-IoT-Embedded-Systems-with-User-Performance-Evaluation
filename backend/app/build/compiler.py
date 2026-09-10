"""Real Arduino CLI compilation — what to run, and what the result means.

Position in the pipeline:

    Build Service -> CompilerAdapter -> ArduinoCliCompiler -> arduino-cli

`app/build/service.py` depends on the `CompilerAdapter` protocol, not on
subprocess details — this is what lets a test substitute a fake compiler
(no real toolchain needed) while production wires up `ArduinoCliCompiler`.

SECURITY BOUNDARY. This module decides *what* to run — a fixed argument
array built entirely from backend data — and hands it to
`app/build/process.py`, the one place in the whole backend allowed to spawn
a process. This file therefore no longer touches `subprocess` itself: every
execution primitive that is forbidden elsewhere under `app/build/` (and
under `app/scenarios/` and `app/commands/`) is forbidden here too, with no
exception at all, which is what
`tests/test_build_compiler.py::test_compiler_module_stays_safe_and_delegates_execution`
asserts statically. There is still no shell anywhere on this path: no
`shell=True`, no `asyncio.create_subprocess_shell`, no command string built
by concatenation, no `eval`/`exec`/`os.system`. See
`tests/test_build_workspace.py::test_build_layer_has_no_execution_primitives`
and `tests/test_hack_backend.py::test_backend_source_contains_no_execution_primitives`.

Every argument in a `CompileRequest` is backend-constructed: `sketch_dir` is
a temporary directory `BuildWorkspace.materialize` just wrote, `fqbn` comes
from the project's own `BoardInfo`, `build_path` is another temporary
directory, and `timeout_seconds` comes from `app/config.py`. No student
input reaches the argv this module builds — a submitted region edit can
change what ends up *inside* the materialized files, never the compiler
invocation itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from app import config
from app.build.process import ProcessTimedOut, run_capture

#: Compiler stdout/stderr are capped before they ever reach a `CompileOutcome`
#: (and therefore a `state` frame), so a pathologically verbose compiler
#: error cannot flood the WebSocket. Truncation only affects what is shown;
#: it never changes `CompileOutcome.success`, which is decided from the
#: process's real exit code before any truncation happens.
_MAX_OUTPUT_CHARS = 8000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n... (output truncated)"


class CompileFailureCategory(str, Enum):
    """Why a compile did not succeed — presentation detail, not control flow.

    `success` on `CompileOutcome` is always decided from the real process
    exit code; this only characterizes a failure for the student/professor
    and for `BuildEvent` data, so the UI can say something more useful than
    "it failed" without this backend parsing compiler text to decide
    success or failure.
    """

    NONE = "none"
    COMPILER_ERROR = "compiler_error"
    TIMEOUT = "timeout"
    TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class CompileRequest:
    """A fully backend-constructed request to compile one materialized sketch.

    See the module docstring: every field is trusted backend/project data,
    never raw student input.
    """

    sketch_dir: Path
    fqbn: str
    build_path: Path
    timeout_seconds: float


@dataclass(frozen=True)
class CompileOutcome:
    """The truthful result of one real compiler invocation."""

    success: bool
    category: CompileFailureCategory
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float

    @classmethod
    def ok(
        cls, *, exit_code: int, stdout: str, stderr: str, duration_seconds: float
    ) -> "CompileOutcome":
        return cls(
            success=True,
            category=CompileFailureCategory.NONE,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
        )

    @classmethod
    def failed(
        cls,
        category: CompileFailureCategory,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        duration_seconds: float = 0.0,
    ) -> "CompileOutcome":
        return cls(
            success=False,
            category=category,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
        )


@dataclass(frozen=True)
class CompiledArtifact:
    """What a *successful* compile left on disk, and what it was built from.

    Added in Phase 3C: flashing must upload the firmware this session
    actually compiled, so the temporary directory a successful compile wrote
    is retained (rather than deleted with the rest of the throwaway build)
    and recorded here. `app/build/flasher.py` is pointed at exactly these
    two paths and never at the live workspace.

    `fingerprint` is `BuildWorkspace.fingerprint()` as of the moment this
    build started. It is what makes "the compiled version *is* the current
    workspace" a checkable fact rather than an assumption: if the student
    edits the security region afterwards, the workspace's fingerprint moves
    and this one does not, so `app/build/service.py` can refuse to flash a
    build that no longer corresponds to the code on screen. It is a
    content hash, not a timestamp or a dirty flag — an edit that is undone
    back to the compiled text correctly compares equal again.

    `root` is the parent temp directory that owns both paths; deleting it is
    how the artifact is discarded (see `app/build/service.py`).
    """

    root: Path
    sketch_dir: Path
    build_path: Path
    fingerprint: str


class CompilerAdapter(Protocol):
    """What `BuildService` needs from a compiler — real or a test double."""

    async def run_compile(self, request: CompileRequest) -> CompileOutcome: ...


class ArduinoCliCompiler:
    """Invokes the real `arduino-cli compile` as a subprocess.

    ARGUMENT ARRAY ONLY. `app/build/process.py::run_capture` takes argv as a
    Python list and passes it straight through as a list — there is no shell
    parsing step for anything to inject into, unlike a hand-built command
    string, which this class never produces.

    `prefix_args` is a testability seam, not a production feature: it lets
    `tests/test_build_compiler.py` drive this class through `sys.executable`
    running a small fake script (`sys.executable <script> <args...> compile
    ...`) instead of a real `arduino-cli`, so the adapter's own behaviour —
    success, failure, timeout, argument safety — is verified without any
    dependency on the real toolchain being installed. Production code never
    passes it; `default_compiler` below leaves it empty.
    """

    def __init__(self, executable: str, *, prefix_args: tuple[str, ...] = ()) -> None:
        self._executable = executable
        self._prefix_args = tuple(prefix_args)

    async def run_compile(self, request: CompileRequest) -> CompileOutcome:
        args = [
            self._executable,
            *self._prefix_args,
            "compile",
            "--no-color",  # ANSI colour codes would otherwise land verbatim
            # in `CompileOutcome.stdout`/`stderr`, and therefore in the
            # `state` frame's `compile_output` the frontend renders as plain
            # text — this asks arduino-cli not to emit them in the first
            # place, rather than stripping escape sequences after the fact.
            "--fqbn",
            request.fqbn,
            "--build-path",
            str(request.build_path),
            str(request.sketch_dir),
        ]

        start = time.monotonic()
        try:
            result = await run_capture(args, timeout_seconds=request.timeout_seconds)
        except ProcessTimedOut:
            # The child was already killed and reaped before this was raised.
            return CompileOutcome.failed(
                CompileFailureCategory.TIMEOUT,
                duration_seconds=time.monotonic() - start,
            )
        except FileNotFoundError:
            # The configured executable does not exist / is not on PATH —
            # this is an environment fact, not a compile error.
            return CompileOutcome.failed(CompileFailureCategory.TOOLCHAIN_UNAVAILABLE)
        except OSError:
            # Anything else launching the process (permissions, etc.) is an
            # infrastructure problem, not a syntax error in the student's
            # code, so it gets its own category rather than COMPILER_ERROR.
            return CompileOutcome.failed(CompileFailureCategory.INTERNAL_ERROR)

        duration = time.monotonic() - start
        stdout = _truncate(result.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate(result.stderr.decode("utf-8", errors="replace"))
        exit_code = result.exit_code

        if exit_code == 0:
            return CompileOutcome.ok(
                exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=duration
            )
        return CompileOutcome.failed(
            CompileFailureCategory.COMPILER_ERROR,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
        )


#: Compiler used by the Build Mode service in production. `config.ARDUINO_CLI_PATH`
#: defaults to relying on PATH; see app/config.py.
default_compiler = ArduinoCliCompiler(config.ARDUINO_CLI_PATH)
