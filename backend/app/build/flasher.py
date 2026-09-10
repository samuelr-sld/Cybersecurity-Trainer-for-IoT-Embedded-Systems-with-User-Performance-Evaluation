"""Real Arduino CLI flashing — uploading an already-built artifact.

Position in the pipeline:

    Build Service -> FlasherAdapter -> ArduinoCliFlasher -> arduino-cli upload
                                                                  |
                                                                  v
                                                          serial device -> ESP32

Deliberately parallel to `app/build/compiler.py`, for the same reason:
`app/build/service.py` depends on the `FlasherAdapter` protocol, not on
subprocess details, so a test can substitute a fake flasher (no real
toolchain, no physical board) while production wires up `ArduinoCliFlasher`.

TWO OPERATIONS, ONE ADAPTER. Flashing needs a *port* before it can upload
anything, and choosing that port is device-domain knowledge, not service
orchestration — so `FlasherAdapter` exposes both `detect_devices` and
`run_flash`. `BuildService` never enumerates ports itself and never sees a
command line; it asks this adapter what is connected and then asks it to
upload to the one device it selected.

COMPILATION IS NOT FLASHING. Nothing in this module compiles anything. It
uploads an already-built artifact — `FlashRequest.build_path` is the very
`--build-path` directory a previously *successful* `arduino-cli compile`
wrote for this session (see `app/build/service.py`, which retains that
directory only on success and only until the workspace changes). And a
successful upload means exactly one thing: `arduino-cli upload` exited 0. It
does not mean the firmware is running, correct, or secure — validation is
not implemented anywhere in this codebase.

SECURITY BOUNDARY — the same one `compiler.py` documents. This module
decides *what* to run (two fixed argument arrays built entirely from
backend data) and hands each to `app/build/process.py`, the one place in the
whole backend allowed to spawn a process. This file therefore no longer
touches `subprocess` itself: every execution primitive that is forbidden
elsewhere under `app/build/` (and under `app/scenarios/` and
`app/commands/`) is forbidden here too, with no exception at all, which is
what `tests/test_build_flasher.py::test_flasher_module_stays_safe_and_delegates_execution`
asserts statically. There is still no shell anywhere on this path: no
`shell=True`, no `asyncio.create_subprocess_shell`, no command string built
by concatenation, no evaluated code. See
`tests/test_build_workspace.py::test_build_layer_has_no_execution_primitives`
and `tests/test_hack_backend.py::test_backend_source_contains_no_execution_primitives`.

Every field of a `FlashRequest` is backend-constructed: `sketch_dir` and
`build_path` are temporary directories this backend materialized and built
into, `fqbn` comes from the project's own `BoardInfo`, `port` is an address
*this module itself* read back from `arduino-cli board list`, and
`timeout_seconds` comes from `app/config.py`. No student input reaches the
argv built here — a submitted region edit can change what ends up *inside*
the materialized files, never the upload invocation, the executable, the
board, or the port.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app import config
from app.build.process import ProcessTimedOut, run_capture

#: Upload stdout/stderr are capped before they ever reach a `FlashOutcome`
#: (and therefore a `state` frame), so a pathologically verbose esptool
#: trace cannot flood the WebSocket. Truncation only affects what is shown;
#: it never changes `FlashOutcome.success`, which is decided from the
#: process's real exit code before any truncation happens.
_MAX_OUTPUT_CHARS = 8000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n... (output truncated)"


class FlashFailureCategory(str, Enum):
    """Why a flash did not happen or did not succeed.

    These are distinct *failure domains*, and keeping them distinct is the
    point: "no ESP32 is plugged in" is not a compiler error, and "two boards
    are connected" is not a toolchain problem. Like
    `CompileFailureCategory`, this only characterizes a failure for the
    student/professor and for `BuildEvent` data — `FlashOutcome.success` is
    always decided from the real process exit code, never from text.
    """

    NONE = "none"
    #: Device discovery ran and found no compatible serial device at all.
    NO_DEVICE = "no_device"
    #: Discovery found more than one candidate and this backend will not
    #: guess which board the student meant. See `_select_candidates`.
    AMBIGUOUS_DEVICE = "ambiguous_device"
    #: The upload failed in a way that names the port — the board was
    #: unplugged, reset, or grabbed by another program mid-upload.
    DEVICE_DISCONNECTED = "device_disconnected"
    #: `arduino-cli upload` ran to completion and exited non-zero.
    UPLOAD_ERROR = "upload_error"
    TIMEOUT = "timeout"
    TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
    INTERNAL_ERROR = "internal_error"


#: Substrings that mark an upload failure as a *device* problem rather than
#: a plain upload error. PRESENTATION ONLY — see `FlashFailureCategory`:
#: whether a flash failed is decided by the exit code alone, before any of
#: this is consulted; this only chooses which label the failure carries so
#: the UI can say "the board went away" instead of a generic failure.
_DISCONNECT_MARKERS = (
    "no serial data received",
    "could not open port",
    "cannot open port",
    "failed to open port",
    "access is denied",
    "device not configured",
    "no such file or directory",
    "serialexception",
    "the port doesn't exist",
    "port not found",
)


@dataclass(frozen=True)
class SerialDevice:
    """One serial port `arduino-cli board list` reported.

    `board_fqbn` is populated only when the Arduino CLI could *identify* the
    attached board from its USB ids. Classic ESP32 dev boards sit behind a
    generic CP2102/CH340 USB-UART bridge and are commonly reported with no
    matching board at all, which is exactly why identification is a
    preference in `_select_candidates` and never a requirement.
    """

    port: str
    protocol: str = "serial"
    board_name: str | None = None
    board_fqbn: str | None = None
    has_usb_id: bool = False


@dataclass(frozen=True)
class DeviceDetectRequest:
    """A fully backend-constructed request to enumerate attached devices.

    `fqbn` is the project's own board target — used only to *prefer* a port
    the CLI positively identified as that platform, never to build a filter
    the frontend could influence.
    """

    fqbn: str
    timeout_seconds: float


@dataclass(frozen=True)
class DeviceDetectOutcome:
    """The result of one real device-discovery attempt.

    `category` describes whether discovery itself worked — `NONE` means the
    listing ran, whatever it found. An empty `devices` with `category` NONE
    is a truthful "nothing is plugged in", not an error; deciding what that
    *means* for a flash is `BuildService`'s job, not this module's.
    """

    devices: tuple[SerialDevice, ...] = ()
    category: FlashFailureCategory = FlashFailureCategory.NONE
    stderr: str = ""
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """True when discovery ran; says nothing about how many it found."""
        return self.category is FlashFailureCategory.NONE


@dataclass(frozen=True)
class FlashRequest:
    """A fully backend-constructed request to upload one built artifact.

    See the module docstring: every field is trusted backend/project data,
    never raw student input. In particular `build_path` is the output
    directory of this session's own last *successful* compile — this is the
    link that makes "flash what you compiled" true rather than aspirational.
    """

    sketch_dir: Path
    build_path: Path
    fqbn: str
    port: str
    timeout_seconds: float


@dataclass(frozen=True)
class FlashOutcome:
    """The truthful result of one real upload attempt.

    `success` True means `arduino-cli upload` exited 0 — the firmware was
    transferred to the board. It does not mean the firmware runs correctly
    or is secure; nothing in Phase 3C checks that.
    """

    success: bool
    category: FlashFailureCategory
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    port: str | None = None

    @classmethod
    def ok(
        cls,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_seconds: float,
        port: str,
    ) -> "FlashOutcome":
        return cls(
            success=True,
            category=FlashFailureCategory.NONE,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            port=port,
        )

    @classmethod
    def failed(
        cls,
        category: FlashFailureCategory,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        duration_seconds: float = 0.0,
        port: str | None = None,
    ) -> "FlashOutcome":
        return cls(
            success=False,
            category=category,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            port=port,
        )


class FlasherAdapter(Protocol):
    """What `BuildService` needs from a flasher — real or a test double."""

    async def detect_devices(
        self, request: DeviceDetectRequest
    ) -> DeviceDetectOutcome: ...

    async def run_flash(self, request: FlashRequest) -> FlashOutcome: ...


def _platform_of(fqbn: str) -> str:
    """The `vendor:arch` prefix of an FQBN, e.g. `esp32:esp32:esp32` -> `esp32:esp32`."""
    parts = fqbn.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else fqbn


def _select_candidates(
    devices: tuple[SerialDevice, ...], fqbn: str
) -> tuple[SerialDevice, ...]:
    """Narrow every reported port down to the plausible ESP32 candidates.

    Deliberately a preference ladder, not one hard filter, because no single
    signal is reliable across ESP32 boards:

    1. Serial ports only. A network/`mdns` "port" is never an upload target
       for this project.
    2. If the Arduino CLI positively identified any port as this project's
       own platform (`vendor:arch` of `fqbn`), only those count. This is the
       strongest signal available and boards with native USB provide it.
    3. Otherwise, any port reporting a USB vendor id. A classic ESP32 sits
       behind a USB-UART bridge that the CLI cannot map to a board, but it
       is always a USB device — which drops built-in/Bluetooth/virtual COM
       ports that are certainly not an ESP32.
    4. Otherwise, every serial port, because a real board is better served
       by an honest ambiguous/near-miss result than by discarding it.

    This never *chooses* between multiple survivors — that is the caller's
    ambiguity decision (see `app/build/service.py`). Returning two devices
    here is how this module says "I will not guess."
    """
    serial = tuple(
        device
        for device in devices
        if not device.protocol or device.protocol.lower() == "serial"
    )
    platform = _platform_of(fqbn)
    identified = tuple(
        device
        for device in serial
        if device.board_fqbn and _platform_of(device.board_fqbn) == platform
    )
    if identified:
        return identified
    usb = tuple(device for device in serial if device.has_usb_id)
    if usb:
        return usb
    return serial


def _port_from_entry(entry: dict[str, Any]) -> SerialDevice | None:
    """Read one `board list` entry into a `SerialDevice`, or None if unusable.

    Tolerant of both shapes the Arduino CLI has emitted: the current
    `{"detected_ports": [{"port": {...}, "matching_boards": [...]}]}` and the
    older flat `{"address": ..., "boards": [...]}` list. Parsing defensively
    (rather than asserting one schema) is what keeps a CLI upgrade from
    turning a connected board into a crash.
    """
    port_info = entry.get("port") if isinstance(entry.get("port"), dict) else entry
    address = port_info.get("address") or port_info.get("label")
    if not isinstance(address, str) or not address:
        return None

    protocol = port_info.get("protocol")
    properties = port_info.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    hardware_id = port_info.get("hardware_id")
    has_usb_id = bool(properties.get("vid") or properties.get("pid") or hardware_id)

    boards = entry.get("matching_boards") or entry.get("boards") or []
    board_name: str | None = None
    board_fqbn: str | None = None
    if isinstance(boards, list) and boards and isinstance(boards[0], dict):
        first = boards[0]
        name = first.get("name")
        # The 0.x listing spelled the key "FQBN"; 1.x spells it "fqbn".
        candidate_fqbn = first.get("fqbn") or first.get("FQBN")
        board_name = name if isinstance(name, str) else None
        board_fqbn = candidate_fqbn if isinstance(candidate_fqbn, str) else None

    return SerialDevice(
        port=address,
        protocol=protocol if isinstance(protocol, str) else "",
        board_name=board_name,
        board_fqbn=board_fqbn,
        has_usb_id=has_usb_id,
    )


def parse_board_list(payload: str) -> tuple[SerialDevice, ...]:
    """Parse `arduino-cli board list --format json` output.

    Raises `ValueError` on anything it cannot read as a port listing, which
    the caller turns into `INTERNAL_ERROR` — an unreadable listing is an
    environment problem, never a truthful "no device".
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("board list output was not valid JSON") from exc

    if isinstance(data, dict):
        entries = data.get("detected_ports")
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError("board list output had an unexpected shape")

    if entries is None:
        # A dict with no "detected_ports" key is how the CLI reports an
        # empty listing on some versions — genuinely zero devices.
        return ()
    if not isinstance(entries, list):
        raise ValueError("board list output had an unexpected shape")

    devices = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        device = _port_from_entry(entry)
        if device is not None:
            devices.append(device)
    return tuple(devices)


def _categorize_upload_failure(stdout: str, stderr: str) -> FlashFailureCategory:
    """Label an already-decided failure. Never decides success — see above."""
    haystack = f"{stdout}\n{stderr}".lower()
    if any(marker in haystack for marker in _DISCONNECT_MARKERS):
        return FlashFailureCategory.DEVICE_DISCONNECTED
    return FlashFailureCategory.UPLOAD_ERROR


class ArduinoCliFlasher:
    """Invokes the real `arduino-cli board list` / `arduino-cli upload`.

    ARGUMENT ARRAY ONLY. `app/build/process.py::run_capture` takes argv as a
    Python list and passes it straight through as a list — there is no shell
    parsing step for anything to inject into, unlike a hand-built command
    string, which this class never produces.

    `prefix_args` is the same testability seam `ArduinoCliCompiler` has, and
    is used the same way: `tests/test_build_flasher.py` drives this class
    through `sys.executable` running a small fake script, so upload success,
    real-exit-code failure, timeout, missing toolchain, device discovery and
    argument safety are all verified without a real `arduino-cli` and
    without a physical board. Production never passes it; `default_flasher`
    below leaves it empty.
    """

    def __init__(self, executable: str, *, prefix_args: tuple[str, ...] = ()) -> None:
        self._executable = executable
        self._prefix_args = tuple(prefix_args)

    async def detect_devices(
        self, request: DeviceDetectRequest
    ) -> DeviceDetectOutcome:
        """Ask the Arduino CLI which serial devices are attached right now."""
        args = [
            self._executable,
            *self._prefix_args,
            "board",
            "list",
            "--format",
            "json",
        ]

        start = time.monotonic()
        try:
            result = await run_capture(args, timeout_seconds=request.timeout_seconds)
        except ProcessTimedOut:
            return DeviceDetectOutcome(
                category=FlashFailureCategory.TIMEOUT,
                duration_seconds=time.monotonic() - start,
            )
        except FileNotFoundError:
            return DeviceDetectOutcome(
                category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
            )
        except OSError:
            return DeviceDetectOutcome(category=FlashFailureCategory.INTERNAL_ERROR)

        duration = time.monotonic() - start
        stderr = _truncate(result.stderr.decode("utf-8", errors="replace"))
        if result.exit_code != 0:
            # The listing itself failed. This is an environment problem and
            # must never be reported as "no ESP32 is connected".
            return DeviceDetectOutcome(
                category=FlashFailureCategory.INTERNAL_ERROR,
                stderr=stderr,
                duration_seconds=duration,
            )

        try:
            devices = parse_board_list(result.stdout.decode("utf-8", errors="replace"))
        except ValueError:
            return DeviceDetectOutcome(
                category=FlashFailureCategory.INTERNAL_ERROR,
                stderr=stderr,
                duration_seconds=duration,
            )

        return DeviceDetectOutcome(
            devices=_select_candidates(devices, request.fqbn),
            stderr=stderr,
            duration_seconds=duration,
        )

    async def run_flash(self, request: FlashRequest) -> FlashOutcome:
        """Upload one already-built artifact to one already-selected port."""
        args = [
            self._executable,
            *self._prefix_args,
            "upload",
            "--no-color",  # keep ANSI escapes out of `flash_output`, which
            # the frontend renders as plain text — the same reason
            # `ArduinoCliCompiler` passes it.
            "--fqbn",
            request.fqbn,
            "--port",
            request.port,
            # Upload the artifact that was already built, rather than
            # letting `upload` rebuild the sketch itself: this is what makes
            # "flash exactly what compiled" a property of the command, not a
            # convention.
            "--input-dir",
            str(request.build_path),
            str(request.sketch_dir),
        ]

        start = time.monotonic()
        try:
            result = await run_capture(args, timeout_seconds=request.timeout_seconds)
        except ProcessTimedOut:
            # The child was already killed and reaped before this was raised.
            return FlashOutcome.failed(
                FlashFailureCategory.TIMEOUT,
                duration_seconds=time.monotonic() - start,
                port=request.port,
            )
        except FileNotFoundError:
            return FlashOutcome.failed(
                FlashFailureCategory.TOOLCHAIN_UNAVAILABLE, port=request.port
            )
        except OSError:
            return FlashOutcome.failed(
                FlashFailureCategory.INTERNAL_ERROR, port=request.port
            )

        duration = time.monotonic() - start
        stdout = _truncate(result.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate(result.stderr.decode("utf-8", errors="replace"))
        exit_code = result.exit_code

        if exit_code == 0:
            return FlashOutcome.ok(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                port=request.port,
            )
        return FlashOutcome.failed(
            _categorize_upload_failure(stdout, stderr),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            port=request.port,
        )


#: Flasher used by the Build Mode service in production. Shares
#: `config.ARDUINO_CLI_PATH` with the compiler — one configured toolchain,
#: chosen by the backend, for both operations.
default_flasher = ArduinoCliFlasher(config.ARDUINO_CLI_PATH)
