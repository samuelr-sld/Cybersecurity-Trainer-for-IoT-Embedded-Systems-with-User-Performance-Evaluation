"""Reading the physical identity (MAC address) of an attached ESP32.

Position in the architecture:

    DeviceMonitor -> IdentityProbe -> EsptoolIdentityProbe -> esptool read_mac
                                                                    |
                                                              serial port -> ESP32

WHY ESPTOOL. `arduino-cli board list` reports the USB *bridge* — for a
classic ESP32 that is a CP2102/CH340 whose `serialNumber` is a generic
factory value ("0001" on this project's reference board), not the ESP32's
own identity. The MAC burned into the chip's OTP ROM is the only stable
per-panel identifier available without running our own firmware, and
`esptool` is how you read it. It is deliberately NOT a new dependency: the
ESP32 Arduino core already ships it, and `arduino-cli upload` — the flash
path this project has used since Phase 3C — invokes that very binary. This
module reuses the toolchain already on the machine rather than inventing a
mechanism.

PROBING IS INTRUSIVE, SO IT IS RARE. Reading the MAC means driving the
board into its serial bootloader (DTR/RTS toggle) and taking over the port
for ~2 seconds; the board is hard-reset afterwards and runs its firmware
again from the start. That is acceptable once per plug-in event and
unacceptable on a 10-second poll, which is why `DeviceMonitor` caches the
result per port and never re-probes a port it already knows — see
`monitor.py`. `--no-stub` is passed for the same reason: it skips uploading
the stub flasher, which is unnecessary for a register read and is the
slower, more invasive half of a default `read_mac`.

NEVER WHILE FLASHING. Two processes must never hold one serial port at
once, and the one that loses would be a firmware upload. `DeviceMonitor`
exposes `hold_identity_probe()` and `BuildService.flash_workspace` wraps its
upload in it, so a probe can never start mid-flash.

SECURITY BOUNDARY — identical to `compiler.py`/`flasher.py`. This module
decides *what* to run (a fixed argument array built entirely from backend
data) and hands it to `app/build/process.py`, still the one place in the
whole backend allowed to spawn a process. No shell, no command string, no
concatenation, no evaluated code. The only caller-supplied value that
reaches the argv is the serial port, and that is an address this backend
read back from `arduino-cli board list` itself — never a value a frontend
can name. See `tests/test_hardware_identity.py`, which asserts the argv
shape statically.
"""

from __future__ import annotations

import glob
import os
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app import config

#: `esptool read_mac` prints the address as `MAC: xx:xx:xx:xx:xx:xx`. v5
#: prints it twice (once from the ROM handshake, once as the command's own
#: result); both are the same value, so the first match is taken.
#:
#: Kept as a pattern *string* used with the module-level `re.search`, rather
#: than a precompiled pattern object: the repo-wide security guard in
#: `tests/test_hack_backend.py` bans the bare identifier that precompiling
#: would require, since it is also the name of a code-evaluation builtin.
#: `re` caches internally, so there is nothing to gain by spelling it the
#: other way and a real static guarantee to lose.
_MAC_PATTERN = r"MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})"

#: Output is capped before it can reach a `MacOutcome.detail` and therefore
#: a wire frame, exactly as the compiler and flasher cap theirs.
_MAX_DETAIL_CHARS = 2000


class IdentityFailure(str, Enum):
    """Why a MAC could not be read. Distinct domains, kept distinct.

    `NONE` means the probe ran and produced an address. Everything else is a
    reason the panel identity is unknown — and an unknown identity is never
    escalated into "no board is attached": detection already established
    that a device is present, and this module cannot overrule it.
    """

    NONE = "none"
    #: No probe was attempted — see `NullIdentityProbe`. Distinct from
    #: every failure below: nothing was tried, so nothing went wrong.
    NOT_PROBED = "not_probed"
    #: No esptool binary could be located at all.
    TOOL_UNAVAILABLE = "tool_unavailable"
    #: esptool ran but could not reach the chip (board held in reset, port
    #: grabbed by another program, not actually an ESP32).
    UNREACHABLE = "unreachable"
    #: esptool ran and succeeded but printed nothing we could read as a MAC.
    UNREADABLE = "unreadable"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class IdentityRequest:
    """A fully backend-constructed request to read one board's MAC.

    `port` is an address this backend read back from `arduino-cli board
    list`; nothing here originates from a client.
    """

    port: str
    timeout_seconds: float


@dataclass(frozen=True)
class IdentityOutcome:
    """The truthful result of one MAC-read attempt.

    A failed probe is not an error state for the device — see
    `IdentityFailure`. `mac` is None and the caller keeps reporting the
    board as connected, just unidentified.
    """

    mac: str | None = None
    category: IdentityFailure = IdentityFailure.NONE
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.mac is not None


class IdentityProbe(Protocol):
    """What the shared layer needs to learn a board's physical identity."""

    async def read_mac(self, request: IdentityRequest) -> IdentityOutcome: ...


def normalize_mac(raw: str) -> str | None:
    """Canonical lower-case colon form, or None if this is not a MAC.

    One spelling everywhere — the panel mapping, the wire, and the UI all
    use it — so a lookup can never miss because the CLI changed case or
    separator. Accepts the `-` separated spelling some tools emit.
    """
    candidate = raw.strip().replace("-", ":").lower()
    if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", candidate):
        return candidate
    return None


def parse_mac(payload: str) -> str | None:
    """Pull the MAC out of `esptool read_mac` output, or None."""
    match = re.search(_MAC_PATTERN, payload)
    return normalize_mac(match.group(1)) if match else None


def _arduino_data_dir() -> Path | None:
    """Where the Arduino CLI keeps installed cores, per platform.

    The ESP32 core — and therefore the bundled esptool — lives under here.
    Returns None rather than guessing when the platform's variable is unset.
    """
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA")
        return Path(base) / "Arduino15" if base else None
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Arduino15"
    return home / ".arduino15"


def discover_esptool() -> str | None:
    """Locate the esptool the ESP32 Arduino core already installed.

    Order of preference:

    1. `config.ESPTOOL_PATH`, when explicitly configured — an operator
       pointing at a specific binary always wins.
    2. The newest `esptool` bundled under the installed ESP32 core. This is
       the same binary `arduino-cli upload` drives, which is exactly why it
       is preferred: if flashing works on this machine, so does this.
    3. None. The caller reports `TOOL_UNAVAILABLE` and the board stays
       connected-but-unidentified; it never becomes a detection failure.

    Versions sort newest-first by their numeric components, so a machine
    with both 4.5.1 and 5.3.1 installed uses 5.3.1.
    """
    if config.ESPTOOL_PATH:
        return config.ESPTOOL_PATH

    data_dir = _arduino_data_dir()
    if data_dir is None:
        return None

    tool_root = data_dir / "packages" / "esp32" / "tools" / "esptool_py"
    names = ("esptool.exe", "esptool") if sys.platform == "win32" else ("esptool", "esptool.py")
    found: list[tuple[tuple[int, ...], str]] = []
    for name in names:
        for path in glob.glob(str(tool_root / "*" / name)):
            version = Path(path).parent.name
            parts = tuple(
                int(piece) if piece.isdigit() else 0 for piece in version.split(".")
            )
            found.append((parts, path))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_DETAIL_CHARS else text[:_MAX_DETAIL_CHARS] + "…"


def _process_api() -> Any:
    """Import the sanctioned process runner lazily.

    Same reason `monitor.py::_detection_api` defers its import: this package
    is imported *by* `app/build/models.py`, so a load-time `app.build`
    import here would close a cycle. See that function's docstring.
    """
    from app.build.process import ProcessTimedOut, run_capture

    return ProcessTimedOut, run_capture


class EsptoolIdentityProbe:
    """Reads a board's MAC with the real `esptool read_mac`.

    `executable` defaults to whatever `discover_esptool()` finds at call
    time rather than at import time, so a core installed after the backend
    started is picked up without a restart — and so a machine with no ESP32
    core at all still imports this module cleanly.
    """

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _resolve(self) -> str | None:
        return self._executable if self._executable is not None else discover_esptool()

    async def read_mac(self, request: IdentityRequest) -> IdentityOutcome:
        executable = self._resolve()
        if not executable:
            return IdentityOutcome(
                category=IdentityFailure.TOOL_UNAVAILABLE,
                detail="no esptool binary found; install the ESP32 core or set TRAINER_ESPTOOL_PATH",
            )

        args = [
            executable,
            "--port",
            request.port,
            # Skip uploading the stub flasher: unnecessary for a register
            # read, and the slower/more invasive half of a default read_mac.
            "--no-stub",
            # Underscore spelling, accepted by esptool v4 (which spells it
            # only this way) and v5 alike.
            "read_mac",
        ]

        timed_out, run_capture = _process_api()
        started = time.monotonic()
        try:
            result = await run_capture(args, timeout_seconds=request.timeout_seconds)
        except timed_out:
            return IdentityOutcome(category=IdentityFailure.TIMEOUT)
        except FileNotFoundError:
            return IdentityOutcome(category=IdentityFailure.TOOL_UNAVAILABLE)
        except OSError:
            return IdentityOutcome(category=IdentityFailure.INTERNAL_ERROR)
        del started

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

        if result.exit_code != 0:
            # Could not reach the chip. Common and benign: the port belongs
            # to something that is not an ESP32, or a serial monitor holds
            # it. The board stays *connected*, just unidentified.
            return IdentityOutcome(
                category=IdentityFailure.UNREACHABLE,
                detail=_truncate(stderr or stdout),
            )

        mac = parse_mac(stdout) or parse_mac(stderr)
        if mac is None:
            return IdentityOutcome(
                category=IdentityFailure.UNREADABLE, detail=_truncate(stdout)
            )
        return IdentityOutcome(mac=mac)


class NullIdentityProbe:
    """An `IdentityProbe` that never touches hardware. Always unidentified.

    NOT A TEST STUB — a correctness requirement. A `DeviceMonitor` whose
    *detector* was injected is reporting whatever that adapter says is
    attached, which need not correspond to anything physically present. It
    would be actively wrong for such a monitor to then drive real esptool at
    the port that adapter named: at best it probes an absent device, at
    worst it resets a real board the caller never meant to touch. So a
    monitor built over an injected detector gets this probe unless it is
    given a real one — see `app/build/service.py::BuildService.__init__`.
    """

    async def read_mac(self, request: IdentityRequest) -> IdentityOutcome:
        return IdentityOutcome(category=IdentityFailure.NOT_PROBED)


#: Identity probe used in production. Resolves its binary lazily, so
#: importing this module never touches the filesystem.
default_identity_probe = EsptoolIdentityProbe()
