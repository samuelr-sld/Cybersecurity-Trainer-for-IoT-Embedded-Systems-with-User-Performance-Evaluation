"""What firmware belongs to a training panel — a reference, never an action.

Position in the architecture (Phase 2C.5):

    ESP32 MAC -> PanelRegistry -> PanelDefinition -> FirmwareConfiguration
                  (panels.py)       (panels.py)        (this module)
                                                              |
                                              [Phase 2D] provisioning consumes it

A `FirmwareConfiguration` answers one question: *which* firmware, built
*how*, for *what* board, talking at *what* line rate, is associated with a
panel. It does not answer "go and build it". There is no method here that
compiles, flashes, opens a port, touches the filesystem, or spawns anything
— this module imports nothing but the standard library, and
`tests/test_panel_registry.py` asserts that statically. Build Mode's real
compile/flash primitives (`app/build/compiler.py`, `flasher.py`,
`process.py`) remain the only things that do those, and nothing in Phase
2C.5 calls them because a panel was identified.

TRUSTED DATA ONLY. Every value here is written in backend source
(`panels.py::BUILT_IN_PANELS`). No field is ever populated from a WebSocket
frame, and there is deliberately no parser that builds one from text — the
`TRAINER_PANEL_MACS` override can bind a MAC to an *already-defined* panel,
but cannot introduce a firmware path. Validation in `__post_init__` is
defence in depth on top of that: a source path that could escape a firmware
root, or an argv-shaped value that could be read as a CLI flag, is rejected
at construction time, so a mistake in the table fails the test suite rather
than reaching a toolchain in Phase 2D.

SHAPE, NOT POLICY. Each group mirrors one stage Phase 2D will drive:

    source          — where the firmware's source comes from
    board           — the Arduino CLI board target (FQBN, options included)
    compilation     — extra compile inputs
    flashing        — upload behaviour
    serial          — how the firmware speaks on USB serial once running

Only fields with a concrete meaning for the existing toolchain are present;
anything else is left for the phase that needs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

#: A stable, lower-case, hyphenated identifier: `smart-home-mqtt-control`.
#: Shared with `panels.py` so panel ids and firmware ids follow one rule.
IDENTIFIER_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"

#: `key=value` for an arduino-cli `--build-property`. The key may not start
#: with `-`, so a property can never be mistaken for a flag once it is placed
#: in an argument array.
_BUILD_PROPERTY_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_.]*=[^\r\n\x00]*"


def _require_identifier(value: str, what: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(IDENTIFIER_PATTERN, value):
        raise ValueError(f"{what} must be a lower-case hyphenated identifier, got {value!r}")


class FirmwareSourceKind(str, Enum):
    """How a firmware source is referenced.

    `BUILD_PROJECT` — a `BuildProject` defined in backend source under
    `app/build/` (addressed by its `project_id`). This is how every firmware
    in the repository exists today (`blink.py`, `environmental.py`), and it
    is what lets Phase 2D reuse `BuildWorkspace.materialize` -> compile ->
    flash with no new mechanism.

    `SKETCH_DIRECTORY` — an on-disk sketch directory, as a relative POSIX
    path under a backend-owned firmware root. Represented so firmware that
    ships as plain `.ino` files needs no new configuration shape; nothing in
    Phase 2C.5 resolves or reads such a path.
    """

    BUILD_PROJECT = "build_project"
    SKETCH_DIRECTORY = "sketch_directory"


@dataclass(frozen=True)
class FirmwareSource:
    """A trusted reference to firmware source. Never read here."""

    kind: FirmwareSourceKind
    #: A `project_id` for BUILD_PROJECT; a relative POSIX path for
    #: SKETCH_DIRECTORY.
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FirmwareSourceKind):
            raise ValueError(f"unknown firmware source kind: {self.kind!r}")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("firmware source reference must be a non-empty string")

        if self.kind is FirmwareSourceKind.BUILD_PROJECT:
            _require_identifier(self.reference, "build project id")
            return

        # SKETCH_DIRECTORY: must stay inside whatever root resolves it.
        # Backslashes and drive letters are rejected outright rather than
        # normalized, so a Windows-shaped absolute path cannot slip through
        # a POSIX parse as a harmless-looking relative one.
        if "\\" in self.reference or ":" in self.reference:
            raise ValueError(f"sketch path must be a relative POSIX path: {self.reference!r}")
        # Segments are checked on the raw text, not on a `PurePosixPath`,
        # which would silently collapse `.` and empty segments.
        if PurePosixPath(self.reference).is_absolute() or any(
            segment in ("", ".", "..") for segment in self.reference.split("/")
        ):
            raise ValueError(f"sketch path may not be absolute or traverse: {self.reference!r}")


@dataclass(frozen=True)
class BoardConfiguration:
    """The Arduino CLI board target the firmware is built and flashed for.

    `fqbn` may carry board options (`esp32:esp32:esp32:PartitionScheme=...`),
    which is how the ESP32 core expresses partition scheme, upload speed and
    flash settings — so this one string is the complete board configuration.
    """

    fqbn: str

    def __post_init__(self) -> None:
        if not isinstance(self.fqbn, str):
            raise ValueError("fqbn must be a string")
        parts = self.fqbn.split(":")
        if len(parts) < 3 or not all(parts[:3]) or self.fqbn.startswith("-"):
            raise ValueError(f"fqbn must be vendor:arch:board[:options], got {self.fqbn!r}")
        if any(char.isspace() for char in self.fqbn):
            raise ValueError(f"fqbn may not contain whitespace: {self.fqbn!r}")


@dataclass(frozen=True)
class CompilationSettings:
    """Extra compile inputs beyond the board target.

    `build_properties` are `key=value` pairs for arduino-cli's
    `--build-property`. Empty for every panel today.
    """

    build_properties: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for prop in self.build_properties:
            if not isinstance(prop, str) or not re.fullmatch(_BUILD_PROPERTY_PATTERN, prop):
                raise ValueError(f"build property must be key=value: {prop!r}")


@dataclass(frozen=True)
class FlashSettings:
    """Upload behaviour. `verify` maps to arduino-cli upload's `--verify`."""

    verify: bool = False


@dataclass(frozen=True)
class SerialSettings:
    """How the running firmware speaks on USB serial.

    `baud_rate` None means the platform default (`config.SERIAL_BAUD_RATE`,
    which Hack Mode's serial transport already uses); set it only for a
    firmware whose `Serial.begin` differs.
    """

    baud_rate: int | None = None

    def __post_init__(self) -> None:
        if self.baud_rate is not None and (
            isinstance(self.baud_rate, bool)
            or not isinstance(self.baud_rate, int)
            or self.baud_rate <= 0
        ):
            raise ValueError(f"baud rate must be a positive integer, got {self.baud_rate!r}")


@dataclass(frozen=True)
class FirmwareConfiguration:
    """The firmware resources associated with one panel.

    Passive and immutable. Consumed by Phase 2D provisioning; in Phase 2C.5
    it is only ever *resolved* and never acted upon.
    """

    firmware_id: str
    source: FirmwareSource
    board: BoardConfiguration
    compilation: CompilationSettings = field(default_factory=CompilationSettings)
    flashing: FlashSettings = field(default_factory=FlashSettings)
    serial: SerialSettings = field(default_factory=SerialSettings)

    def __post_init__(self) -> None:
        _require_identifier(self.firmware_id, "firmware id")
        for name, expected in (
            ("source", FirmwareSource),
            ("board", BoardConfiguration),
            ("compilation", CompilationSettings),
            ("flashing", FlashSettings),
            ("serial", SerialSettings),
        ):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"firmware {name} must be a {expected.__name__}")
