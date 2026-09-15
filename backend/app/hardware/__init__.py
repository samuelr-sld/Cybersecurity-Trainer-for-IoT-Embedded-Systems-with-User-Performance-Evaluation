"""Shared hardware layer — the platform's view of the attached ESP32.

                 ESP32
                   |
            Arduino CLI
             Detection
                   |
                   v
          Shared Device State      <-- this package
             /           \\
            v             v
      Build Mode        Hack Mode
      Compile/Flash     Read/Write
      Arduino CLI       Serial Transport (Phase 2)
                        Network/MQTT

THE ARCHITECTURAL BOUNDARY THIS PACKAGE EXISTS TO ENFORCE:

    Shared layer  — what ESP32 is connected, where it is connected, and
                    whether it is available.
    Build Mode    — compile and flash code to that ESP32.
    Hack Mode     — eventually read/write to that ESP32 through the web
                    terminal, and perform the cybersecurity interactions.

Neither mode owns the board. A mode asks this package what is plugged in and
gets the same answer the other mode gets, because there is exactly one
`device_monitor` per process and exactly one `DeviceStatus` vocabulary —
`app/build/models.py` aliases the latter as `HardwareStatus` rather than
declaring a parallel one.

Module map:

    state.py    `DeviceState` / `DeviceStatus` — passive, dependency-free.
    mac.py      `normalize_mac` — the one canonical MAC spelling. Pure.
    identity.py `IdentityProbe` / `EsptoolIdentityProbe` — reads the ESP32's
                MAC with the esptool the ESP32 Arduino core already ships.
    panels.py   `PanelDefinition` / `PanelRegistry` — MAC -> registered
                training panel. Pure data and lookup, no hardware I/O. One
                registry, so both modes name the same board identically.
    firmware.py `FirmwareConfiguration` — the firmware resources a panel
                references (source, board, compilation, flashing, serial).
                A reference only: nothing here builds or flashes.
    panel_identification.py
                `PanelIdentificationService` — joins the monitor's MAC to
                the registry: connected ESP32 -> PanelDefinition or an
                explicit unregistered/unidentified answer. Triggers nothing.
    serial_alias.py
                The canonical Linux/training serial path (`/dev/ttyUSB0`)
                the courseware speaks, plus `resolve_serial_target` — the
                seam that maps it back to the real `DeviceState.port`
                before any serial I/O. Display-and-command only; it never
                sets or reaches the physical port.
    serial_transport.py
                `SerialTransport` — real USB-serial I/O with the attached
                ESP32, on a reader thread so the event loop never blocks.
                Moves bytes only: no shell, no subprocess, no interpretation.
                Given a REAL port address; never sees the training alias.
    monitor.py  `DeviceMonitor` — owns the current state, runs detection
                through the existing `FlasherAdapter`, resolves identity
                (cached per port, suppressed while flashing), coalesces
                concurrent refreshes, and publishes results. Emits no
                events and writes to no terminal.

PHASE 2A SCOPE: detection, shared state, AND real serial I/O for Hack Mode
(`serial_transport.py`). `DeviceState.port` is what gets opened — the
training alias is resolved away first. Build Mode's compile/flash path is
untouched by any of this and still drives the Arduino CLI as it always has.
"""

from __future__ import annotations

from app.hardware.identity import (
    EsptoolIdentityProbe,
    IdentityFailure,
    IdentityOutcome,
    IdentityProbe,
    IdentityRequest,
    NullIdentityProbe,
    default_identity_probe,
    normalize_mac,
)
from app.hardware.firmware import (
    BoardConfiguration,
    CompilationSettings,
    FirmwareConfiguration,
    FirmwareSource,
    FirmwareSourceKind,
    FlashSettings,
    SerialSettings,
)
from app.hardware.monitor import DeviceDetector, DeviceMonitor, device_monitor
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationService,
    PanelIdentificationStatus,
    identify_panel,
    panel_identification_service,
)
from app.hardware.panels import (
    BUILT_IN_PANELS,
    PanelDefinition,
    PanelMatch,
    PanelRegistry,
    PanelResolution,
    default_panel_registry,
    parse_panel_bindings,
)
from app.hardware.serial_alias import (
    canonical_alias,
    resolve_serial_target,
    serial_representations,
)
from app.hardware.serial_transport import (
    SerialEvent,
    SerialEventKind,
    SerialPort,
    SerialTextDecoder,
    SerialTransport,
    SerialTransportError,
    encode_send_payload,
    open_pyserial_port,
)
from app.hardware.state import DeviceState, DeviceStatus

__all__ = [
    "BUILT_IN_PANELS",
    "BoardConfiguration",
    "CompilationSettings",
    "FirmwareConfiguration",
    "FirmwareSource",
    "FirmwareSourceKind",
    "FlashSettings",
    "PanelDefinition",
    "PanelIdentification",
    "PanelIdentificationService",
    "PanelIdentificationStatus",
    "PanelMatch",
    "PanelRegistry",
    "PanelResolution",
    "SerialSettings",
    "default_panel_registry",
    "identify_panel",
    "panel_identification_service",
    "parse_panel_bindings",
    "DeviceDetector",
    "DeviceMonitor",
    "DeviceState",
    "DeviceStatus",
    "SerialEvent",
    "SerialEventKind",
    "SerialPort",
    "SerialTextDecoder",
    "SerialTransport",
    "SerialTransportError",
    "EsptoolIdentityProbe",
    "IdentityFailure",
    "IdentityOutcome",
    "IdentityProbe",
    "IdentityRequest",
    "NullIdentityProbe",
    "canonical_alias",
    "default_identity_probe",
    "encode_send_payload",
    "device_monitor",
    "normalize_mac",
    "open_pyserial_port",
    "resolve_serial_target",
    "serial_representations",
]
