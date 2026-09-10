"""The backend's single sanctioned process-execution primitive.

Position in the pipeline:

    CompilerAdapter -> ArduinoCliCompiler -+
                                           +-> run_capture() -> arduino-cli
    FlasherAdapter  -> ArduinoCliFlasher  -+

`compiler.py` and `flasher.py` decide *what* to run (a fixed, backend-built
argument array) and how to interpret the result; this module is the only
place that actually spawns anything. Consolidating it here narrows the
security boundary rather than widening it: exactly one file under `app/`
now references the stdlib `subprocess` integration at all, and the two
adapter modules that used to spawn processes themselves no longer can. See
`tests/test_build_process.py`, plus the repo-wide static guards in
`tests/test_build_workspace.py::test_build_layer_has_no_execution_primitives`
and `tests/test_hack_backend.py::test_backend_source_contains_no_execution_primitives`,
which now carve out only this file, by name, for only the `subprocess` token.

WHY A WORKER THREAD RATHER THAN `asyncio.create_subprocess_exec`.
`asyncio`'s subprocess API is not implemented on every event loop. On
Windows it requires a `ProactorEventLoop`; a `SelectorEventLoop` raises
`NotImplementedError` from `loop._make_subprocess_transport`. That is not a
hypothetical: uvicorn (>= 0.36) selects the loop with
`uvicorn/loops/asyncio.py::asyncio_loop_factory(use_subprocess=...)`, and on
win32 it returns `SelectorEventLoop` whenever uvicorn itself needs worker
subprocesses — i.e. whenever the server runs with `--reload` or
`--workers N`, which is exactly how this backend is run in development. The
adapters' `asyncio.create_subprocess_exec` therefore crashed the `/ws/build`
handler with `NotImplementedError` the first time a student pressed Compile,
even though `arduino-cli` itself was fine.

The blocking `subprocess.run` API has no such dependency: it works on every
platform and under every event loop. Running it on a worker thread via
`asyncio.to_thread` keeps this function a normal awaitable, so the async
call sites, the adapter protocols and `BuildService` are all unchanged. The
cost is one pooled thread parked for the duration of a compile/upload, which
is bounded by the caller's timeout.

STILL NO SHELL. `subprocess.run` is given a real argument *list* and never a
command string, `shell` is left at its default of False (this module cannot
even name that keyword — the static guards ban the bare identifier), and no
argument is ever concatenated into a command line. Nothing here interpolates
caller-supplied text into anything but one argv element.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


class ProcessTimedOut(Exception):
    """The process exceeded its timeout and was killed.

    A dedicated exception, rather than `asyncio.TimeoutError`, so callers
    keep categorizing a real toolchain timeout distinctly from an unrelated
    async cancellation.
    """


@dataclass(frozen=True)
class ProcessResult:
    """What one finished process produced. Raw bytes; callers decode."""

    exit_code: int
    stdout: bytes
    stderr: bytes


def _run_blocking(args: tuple[str, ...], timeout_seconds: float) -> ProcessResult:
    """Blocking body of `run_capture`; only ever called on a worker thread."""
    try:
        completed = subprocess.run(  # noqa: S603 - argument array, never a shell
            list(args),
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as expired:
        # `subprocess.run` has already killed the child and reaped it before
        # raising, so there is no orphaned toolchain process left behind.
        raise ProcessTimedOut() from expired
    return ProcessResult(
        exit_code=completed.returncode,
        stdout=completed.stdout or b"",
        stderr=completed.stderr or b"",
    )


async def run_capture(
    args: Sequence[str], *, timeout_seconds: float
) -> ProcessResult:
    """Run one argument array to completion, capturing stdout and stderr.

    Raises `FileNotFoundError` if the executable does not exist, `OSError`
    for any other launch failure, and `ProcessTimedOut` if the process had
    to be killed — the three outcomes the Build Mode adapters categorize.
    """
    return await asyncio.to_thread(_run_blocking, tuple(args), timeout_seconds)
