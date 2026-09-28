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

STILL NO SHELL. `subprocess.Popen` is given a real argument *list* and never
a command string, `shell` is left at its default of False (this module
cannot even name that keyword — the static guards ban the bare identifier),
and no argument is ever concatenated into a command line. Nothing here
interpolates caller-supplied text into anything but one argv element.

THE TIMEOUT IS A HARD BOUND ON THE WHOLE PROCESS TREE. This used to be
`subprocess.run(timeout=...)`, which is not a hard bound: on a timeout it
kills only the DIRECT child and then, on Windows, calls `communicate()` with
no timeout at all to collect the output. The ESP32 core's `esptool.exe` is
a PyInstaller one-file bundle — a bootloader parent that runs the real tool
as a CHILD process holding the same stdout/stderr pipes. Killing the parent
orphaned the child, the pipes never reached EOF, and `run_capture` waited
for as long as the orphan kept running (a full 4 MiB `read_flash` at the
default baud: minutes past the timeout). The awaiting Hack Mode command
therefore never returned, and because a session answers one command at a
time, every later command in that session queued behind it. On POSIX the
same bundle survives a SIGKILL of its parent too, holding the serial port.

So a timeout now kills the whole TREE (`_kill_tree`: `taskkill /T` on
Windows; a process-group kill on POSIX, where the child is started in its
own session), and the post-kill drain is itself bounded by
`KILL_GRACE_SECONDS`. If some descendant still escaped, the output is
abandoned rather than waited for: `ProcessTimedOut` is raised regardless,
so `timeout_seconds + 2 * KILL_GRACE_SECONDS` is the most any caller can
ever wait. A cancelled caller (a WebSocket torn down mid-command) kills the
tree too, instead of leaving it running unobserved until its timeout.
The tree-killers are fixed argument arrays with only an integer PID in
them — the same no-shell discipline as everything else here.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

#: How long a kill, and the output drain after it, may take before the
#: process is abandoned. Bounds the time past `timeout_seconds` a caller can
#: wait; small, because a killed process has nothing left to do.
KILL_GRACE_SECONDS: float = 5.0


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


def _tree_kill_argv(pid: int) -> list[str] | None:
    """The fixed argv that kills `pid` AND its descendants, or None if the
    platform's killer is not installed (the direct kill still happens)."""
    if _WINDOWS:
        killer = shutil.which("taskkill")
        return [killer, "/PID", str(pid), "/T", "/F"] if killer else None
    killer = shutil.which("kill")
    # The child was started with `start_new_session`, so its process-group id
    # is its pid; the negative id addresses the whole group.
    return [killer, "-KILL", "--", f"-{pid}"] if killer else None


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill a child and everything it started. Never raises.

    The tree kill must run while the direct child is still alive on Windows:
    `taskkill /T` finds descendants through their parent, and an orphan whose
    parent is already gone can no longer be found that way. (While the child
    is unreaped its PID cannot be reused, so it is never someone else's.) On
    POSIX a process group outlives its leader, so the group kill is always
    sent.
    """
    argv = _tree_kill_argv(process.pid)
    if argv is not None and (not _WINDOWS or process.poll() is None):
        try:
            subprocess.run(  # noqa: S603 - fixed argument array, never a shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=KILL_GRACE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("tree kill failed for pid %s", process.pid, exc_info=True)
    try:
        process.kill()
    except OSError:
        pass


#: The flash/read tool that talks to the ESP32 over the serial port. A
#: leftover instance from an earlier session — an upload that was cancelled,
#: or a firmware read whose parent died before it released the port — keeps
#: the serial port open, and the next flash then fails with esptool's own
#: "No more data to read from the serial port". `read-flash` of the whole 4MB
#: image is the worst offender because it holds the port for a long time.
#: Windows matches by image name; POSIX matches the command line, which also
#: catches the `esptool.py`/`esptool` spellings the bundled tool uses there.
_UPLOADER_IMAGE_WINDOWS = "esptool.exe"
_UPLOADER_PATTERN_POSIX = "esptool"


def reap_stale_uploaders() -> bool:
    """Best-effort kill of orphaned esptool processes still holding the serial
    port, run just before a flash so a leftover uploader cannot lock it.

    Never raises. Returns True if a reap command was launched (a missing
    process is the goal state, so the command's own exit status is ignored),
    False when the platform's process killer is not installed.

    Safe because the trainer drives ONE board and a preparation holds a
    process-wide lock while it runs: no *wanted* uploader is running at the
    moment this is called, since the caller has not yet started its own.
    """
    if _WINDOWS:
        killer = shutil.which("taskkill")
        argv = [killer, "/IM", _UPLOADER_IMAGE_WINDOWS, "/T", "/F"] if killer else None
    else:
        killer = shutil.which("pkill")
        argv = [killer, "-KILL", "-f", _UPLOADER_PATTERN_POSIX] if killer else None
    if argv is None:
        logger.debug("no stale-uploader reaper available on this platform; skipping")
        return False
    try:
        subprocess.run(  # noqa: S603 - fixed argument array, never a shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=KILL_GRACE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("stale-uploader reap failed", exc_info=True)
    return True


class _Running:
    """The one running child, shared by the worker thread and its awaiter.

    Lets a cancelled `run_capture` kill the process it started even though
    the blocking wait lives on another thread. `abandon()` before the child
    exists is remembered, so the worker kills it the moment it is attached.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._abandoned = False

    def attach(self, process: subprocess.Popen) -> bool:
        """Record the child; False if the caller has already given up on it."""
        with self._lock:
            self._process = process
            return not self._abandoned

    def abandon(self) -> None:
        """Kill the child's tree off the event loop — `taskkill` blocks."""
        with self._lock:
            self._abandoned = True
            process = self._process
        if process is not None:
            threading.Thread(
                target=_kill_tree, args=(process,), name="process-abandon", daemon=True
            ).start()


def _drain_after_kill(process: subprocess.Popen) -> None:
    """Collect what a killed process left, but never wait on it for long.

    If a descendant escaped the tree kill and still holds the pipes, the
    output is abandoned: the reader threads (daemon threads, on Windows) end
    whenever the pipes finally close, and nobody waits for them.
    """
    try:
        process.communicate(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "pid %s: output pipes still open %.0fs after kill; abandoning them",
            process.pid,
            KILL_GRACE_SECONDS,
        )
    except (OSError, ValueError):
        pass


def _run_blocking(
    args: tuple[str, ...], timeout_seconds: float, running: _Running | None = None
) -> ProcessResult:
    """Blocking body of `run_capture`; only ever called on a worker thread."""
    process = subprocess.Popen(  # noqa: S603 - argument array, never a shell
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=not _WINDOWS,
    )
    if running is not None and not running.attach(process):
        _kill_tree(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as expired:
        _kill_tree(process)
        _drain_after_kill(process)
        raise ProcessTimedOut() from expired
    except BaseException:
        _kill_tree(process)
        raise
    return ProcessResult(
        exit_code=process.returncode,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )


async def run_capture(
    args: Sequence[str], *, timeout_seconds: float
) -> ProcessResult:
    """Run one argument array to completion, capturing stdout and stderr.

    Raises `FileNotFoundError` if the executable does not exist, `OSError`
    for any other launch failure, and `ProcessTimedOut` if the process had
    to be killed — the three outcomes the Build Mode adapters categorize.
    Returns or raises within `timeout_seconds + 2 * KILL_GRACE_SECONDS`,
    whatever the process tree does. If the caller is cancelled, the process
    tree is killed rather than left running.
    """
    running = _Running()
    try:
        return await asyncio.to_thread(
            _run_blocking, tuple(args), timeout_seconds, running
        )
    except asyncio.CancelledError:
        running.abandon()
        raise
