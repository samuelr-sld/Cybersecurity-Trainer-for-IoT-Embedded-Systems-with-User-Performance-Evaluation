"""The shared, platform-level view of the currently attached ESP32.

Position in the architecture:

                       ESP32
                         |
                  Arduino CLI detection
                  (app/build/flasher.py)
                         |
                         v
                 Shared Device State
                   (this package)
                    /          \\
                   v            v
             Build Mode      Hack Mode
             compile/flash   terminal / future serial transport

THIS MODULE DESCRIBES A DEVICE, NOT A SESSION. A `DeviceState` answers three
questions and no others: *what* ESP32 is attached to this machine, *where*
it is attached, and *whether* it is available. It deliberately says nothing
about compiling, uploading, exploiting, or any student's progress — those
belong to the mode that owns them (see `app/build/` and `app/scenarios/`).
That split is the whole point of Phase 1: neither mode owns the board, so
neither mode's state can disagree with the other's about what is plugged in.

PURE BY DESIGN. Nothing here imports `app.build`, `app.scenarios`, FastAPI,
or anything that can spawn a process. It is a passive value type plus its
vocabulary, in the same spirit as `app/build/models.py` — `monitor.py` is
the only module in this package that reaches out to real detection, and
even it does so through an injected adapter. Keeping this file dependency-
free is also what lets `app/build/models.py` alias `HardwareStatus` to
`DeviceStatus` below without closing an import cycle.

IMMUTABLE AND REPLACED WHOLESALE. `DeviceState` is a frozen dataclass, and
`DeviceMonitor` publishes a new one rather than mutating fields in place.
Under asyncio that is what makes concurrent consumption safe without a lock
on the read path: a reader either sees the whole previous state or the whole
next one, never a half-updated mix of a stale port and a fresh board name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DeviceStatus(str, Enum):
    """Whether an ESP32 is attached, and if not, why we can't say it is.

    This is the single hardware-presence vocabulary for the whole platform.
    `app/build/models.py` aliases it as `HardwareStatus`, which is the name
    Build Mode has always used for it — the values are identical because
    they are literally the same enum, so a Build Mode `state` frame and a
    Hack Mode `hardware` frame can never drift into describing the same
    board with different words.

    The distinctions here are all failure *domains*, kept apart on purpose:

    `NOT_CHECKED` — no detection has run yet in this process. Not the same
    as DISCONNECTED: we have not looked, so claiming nothing is plugged in
    would be an invention.

    `DETECTING` — a real `arduino-cli board list` is in flight right now.

    `CONNECTED` — exactly one plausible ESP32 was found. `port` names it.

    `DISCONNECTED` — detection ran and truthfully found nothing. This is a
    normal, expected state, never an error: "no ESP32 is plugged in" must
    not be presented to a student as a toolchain or build problem. It is the
    same discipline `FlashStatus.NO_DEVICE` exists for.

    `AMBIGUOUS` — several plausible boards were found and this backend will
    not guess which one the student means. Mirrors
    `FlashFailureCategory.AMBIGUOUS_DEVICE`.

    `ERROR` — detection itself could not run (missing/wedged Arduino CLI,
    unreadable listing). Deliberately distinct from DISCONNECTED for the
    reason above: an environment problem is not an empty port list.
    """

    NOT_CHECKED = "not_checked"
    DETECTING = "detecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True)
class DeviceState:
    """One immutable answer to "what ESP32 is attached right now?".

    Every field is either read back from a real `arduino-cli board list` or
    taken from this backend's own configuration — nothing here is ever
    supplied by a frontend, and nothing is invented to make the UI look
    connected. A consumer that renders this is rendering the machine's
    actual USB state.
    """

    #: The presence verdict. Everything below is only meaningful in light of
    #: it: `port`/`board`/`fqbn` are None unless this is CONNECTED.
    status: DeviceStatus = DeviceStatus.NOT_CHECKED

    #: The serial address the OS/Arduino CLI reported — "COM7" on Windows, a
    #: "/dev/tty..." path elsewhere. THE PHASE 2 HOOK: a future Hack Mode
    #: serial transport opens exactly this, and never a port named on the
    #: wire. None whenever `status` is not CONNECTED.
    port: str | None = None

    #: The board name the Arduino CLI positively identified, or None when it
    #: could not name the board. None is common and not a failure: a classic
    #: ESP32 sits behind a generic CP2102/CH340 USB-UART bridge that the CLI
    #: cannot map to a board (see `flasher.py::SerialDevice`). Consumers
    #: apply their own fallback label — Build Mode uses its project's board
    #: name, Hack Mode uses `config.HARDWARE_BOARD_LABEL` — rather than this
    #: layer inventing a name it does not actually know.
    board: str | None = None

    #: The Fully Qualified Board Name to address this device by: the
    #: identified board's own FQBN when `identified` is True, otherwise the
    #: target FQBN detection was run against (`config.HARDWARE_TARGET_FQBN`
    #: or, for a Build Mode check, that session's project board). Either way
    #: it is backend data, never a frontend-supplied value.
    fqbn: str | None = None

    #: True only when the Arduino CLI itself named the board, i.e. `board`
    #: and `fqbn` came from the device rather than from our target. Lets a
    #: reader tell "this really is an identified ESP32 dev module" from
    #: "something ESP32-shaped is on this port and we assumed our target".
    identified: bool = False

    #: The names the student-facing USB field may show, in display order:
    #: the ACTUAL detected port first, then any different spelling detection
    #: itself reported (the CLI's `label`), then the canonical Linux/training
    #: path the courseware and Hack Mode command examples speak (see
    #: `serial_alias.py`). Deduplicated, so on a Raspberry Pi whose real port
    #: already is that canonical path this holds a single entry.
    #:
    #: DISPLAY ONLY — none of these is what gets opened. `port` above is the
    #: real device, and a canonical target a student types is mapped back to
    #: it by `resolve_serial_target` before any serial I/O. Empty unless
    #: CONNECTED, so an unplugged board shows a dash rather than a stale or
    #: invented path.
    port_aliases: tuple[str, ...] = ()

    #: The MAC burned into the ESP32's OTP ROM, canonical lower-case colon
    #: form — the board's durable physical identity, read with `esptool
    #: read_mac` (see `identity.py`). None when no probe has succeeded:
    #: esptool is unavailable, the chip could not be reached, or the device
    #: is not an ESP32. A None MAC never means "disconnected"; detection
    #: alone decides that, and an unidentified board stays connected.
    mac: str | None = None

    #: The human-readable training panel this MAC is mapped to (see
    #: `panels.py`), or None when the MAC is unknown or has no entry. None
    #: makes the UI fall back to showing the MAC itself rather than
    #: inventing a panel name for a board nobody has mapped.
    panel: str | None = None

    #: Short, non-fatal explanation for a non-CONNECTED status — e.g. which
    #: ports made a result AMBIGUOUS. Diagnostic text for a human; never a
    #: reason to treat DISCONNECTED as an error.
    detail: str = ""

    #: When the detection behind this state finished, or None for the
    #: initial NOT_CHECKED state. Drives the monitor's freshness cache.
    checked_at: datetime | None = None

    @property
    def connected(self) -> bool:
        """Whether an ESP32 is available right now.

        Derived from `status` rather than stored as its own field, so it is
        impossible for a state to claim it is connected while carrying no
        port, or to report DISCONNECTED while a stale `connected=True` flag
        hangs off it.
        """
        return self.status is DeviceStatus.CONNECTED

    def snapshot(self) -> dict:
        """A JSON-serialisable view for the wire.

        `status`/`board_name`/`port` are spelled exactly as Build Mode's own
        `hardware` block already spells them (see
        `app/build_sessions.py::BuildSession.snapshot`), so one frontend
        formatter can render a Build Mode `state` frame and a Hack Mode
        `hardware` frame without knowing which mode it came from. The
        remaining keys are the shared layer's own additions.
        """
        return {
            "status": self.status.value,
            "board_name": self.board,
            "port": self.port,
            "connected": self.connected,
            "fqbn": self.fqbn,
            "identified": self.identified,
            "detail": self.detail,
            "checked_at": None if self.checked_at is None else self.checked_at.isoformat(),
            # Physical panel identity. `port_aliases` is the closed set of
            # port spellings the UI may cycle through, and `mac`/`panel` are
            # the two interchangeable representations of the PANEL field —
            # both nullable, because "we do not know" is a real answer the
            # UI renders honestly rather than filling in.
            "port_aliases": list(self.port_aliases),
            "mac": self.mac,
            "panel": self.panel,
        }
