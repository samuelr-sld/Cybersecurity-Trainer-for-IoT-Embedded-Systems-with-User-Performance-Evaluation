"""Reading firmware BYTES off an attached ESP32's flash — real hardware.

Position in the architecture:

    Hack Mode `esptool.py read_flash` (app/commands/handlers/esptool_py.py)
                          |
                    FlashReadProbe
                    (this module)
                          |
              esptool <port> read_flash <offset> <size> <tmp file>
                          |
                        ESP32

WHY THIS IS ITS OWN MODULE, NOT A METHOD ON `identity.py`. `identity.py`
answers "which chip is this?" (a MAC, a handful of bytes); this module
answers "what is actually stored in its flash?" (up to a whole image). The
two share a toolchain and a safety pattern but are genuinely different
operations with different bounds, timeouts and failure shapes, so they stay
separate the same way `compiler.py` and `flasher.py` do.

REUSE, NOT REINVENTION. This is the exact same trusted pattern
`identity.py::EsptoolIdentityProbe` already established: `discover_esptool()`
finds the same esptool binary the ESP32 Arduino core (and therefore
`arduino-cli upload`) already ships, a fixed argument ARRAY (never a string)
is handed to `app/build/process.py::run_capture` — still the one module in
the whole backend allowed to spawn a process — and the caller-supplied port
is always a real address this backend already detected, never a value a
student can name.

WRITES TO A BACKEND-CONTROLLED TEMP FILE, NEVER A STUDENT-NAMED PATH. The
student's `esptool.py read_flash <offset> <size> <file>` names a FILENAME as
part of the tool's familiar syntax, but that string is never used as a
filesystem path anywhere on this path — see
`app/commands/handlers/esptool_py.py`. `EsptoolFlashReader.read_flash` picks
its own `tempfile` location, reads the bytes back into memory, and discards
the file; the only thing that survives the call is the returned `bytes`.

BOUNDED. `offset`/`size` are validated by the caller (the command handler)
against `config.HARDWARE_MAX_FLASH_READ_BYTES` before this module is ever
reached — this module itself performs no I/O beyond what its caller asked
for, and asks for nothing else.

SECURITY BOUNDARY — identical to `identity.py`/`compiler.py`/`flasher.py`.
No shell, no command string, no string concatenation into an argument, no
evaluated code. `tests/test_hack_backend.py`'s repo-wide execution-primitive
scan covers this module with no exemption.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

#: Output is capped before it can reach a `FlashReadOutcome.detail` and
#: therefore a wire frame, exactly as the compiler, flasher and identity
#: probe cap theirs.
_MAX_DETAIL_CHARS = 2000


class FlashReadFailure(str, Enum):
    """Why a real flash read could not be completed. Distinct domains.

    `NONE` means the read ran and produced bytes. Every other member is a
    reason it did not — none of them mean "no board is attached": the
    caller only reaches this module once the shared device layer has
    already established that a board IS present (see
    `app/commands/handlers/esptool_py.py`).
    """

    NONE = "none"
    TOOL_UNAVAILABLE = "tool_unavailable"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class FlashReadRequest:
    """A fully backend-constructed request to read real flash bytes.

    `port` is an address this backend already read back from
    `arduino-cli board list` — nothing here originates from a client. The
    caller has already bounds-checked `offset`/`size`.
    """

    port: str
    offset: int
    size: int
    timeout_seconds: float


@dataclass(frozen=True)
class FlashReadOutcome:
    """The truthful result of one real flash-read attempt."""

    data: bytes | None = None
    category: FlashReadFailure = FlashReadFailure.NONE
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.data is not None


@dataclass(frozen=True)
class FirmwareArtifact:
    """Real bytes read off a real ESP32's flash, held for one session.

    Session-scoped (see `app/sessions.py::HackSession.firmware_artifact`),
    never scenario-scoped: the scenario's job is the LOGICAL discovery
    narrative (`app/scenarios/smart_home.py` and friends), and it must not
    hold, execute, or reason about raw hardware bytes — this artifact is the
    seam that keeps the two apart. `strings`/`grep` read it back through
    `firmware_strings.py`; nothing else does.
    """

    data: bytes
    offset: int
    size: int
    port: str
    captured_at: datetime


class FlashReadProbe(Protocol):
    """What the command layer needs to read real flash bytes."""

    async def read_flash(self, request: FlashReadRequest) -> FlashReadOutcome: ...


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_DETAIL_CHARS else text[:_MAX_DETAIL_CHARS] + "…"


def _process_api() -> Any:
    """Import the sanctioned process runner lazily — see `identity.py`'s
    identical `_process_api` for why this is deferred rather than a
    module-level import (`app.hardware` is imported *by* `app.build`)."""
    from app.build.process import ProcessTimedOut, run_capture

    return ProcessTimedOut, run_capture


class EsptoolFlashReader:
    """Reads real flash bytes with `esptool read_flash`.

    `executable` defaults to whatever `discover_esptool()` finds at call
    time (like `EsptoolIdentityProbe`), so a core installed after the
    backend started is picked up without a restart.
    """

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _resolve(self) -> str | None:
        from app.hardware.identity import discover_esptool

        return self._executable if self._executable is not None else discover_esptool()

    async def read_flash(self, request: FlashReadRequest) -> FlashReadOutcome:
        executable = self._resolve()
        if not executable:
            return FlashReadOutcome(
                category=FlashReadFailure.TOOL_UNAVAILABLE,
                detail="no esptool binary found; install the ESP32 core or set TRAINER_ESPTOOL_PATH",
            )

        timed_out, run_capture = _process_api()

        with tempfile.TemporaryDirectory(prefix="hack-flash-read-") as tmp_dir:
            out_path = Path(tmp_dir) / "read_flash.bin"
            args = [
                executable,
                "--port",
                request.port,
                "read_flash",
                hex(request.offset),
                hex(request.size),
                str(out_path),
            ]

            started = time.monotonic()
            try:
                result = await run_capture(args, timeout_seconds=request.timeout_seconds)
            except timed_out:
                return FlashReadOutcome(category=FlashReadFailure.TIMEOUT)
            except FileNotFoundError:
                return FlashReadOutcome(category=FlashReadFailure.TOOL_UNAVAILABLE)
            except OSError:
                return FlashReadOutcome(category=FlashReadFailure.INTERNAL_ERROR)
            del started

            if result.exit_code != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                stdout = result.stdout.decode("utf-8", errors="replace")
                return FlashReadOutcome(
                    category=FlashReadFailure.UNREACHABLE,
                    detail=_truncate(stderr or stdout),
                )

            if not out_path.is_file():
                return FlashReadOutcome(
                    category=FlashReadFailure.INTERNAL_ERROR,
                    detail="esptool reported success but produced no output file",
                )

            # The temp directory (and this file) is removed on the way out of
            # the `with` block; the bytes themselves are the only thing that
            # survives the call — see the module docstring.
            data = out_path.read_bytes()
            return FlashReadOutcome(data=data)


#: Flash reader used in production. Resolves its binary lazily, so importing
#: this module never touches the filesystem.
default_flash_reader = EsptoolFlashReader()
