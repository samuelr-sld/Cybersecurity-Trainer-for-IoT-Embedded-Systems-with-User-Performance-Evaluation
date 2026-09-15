"""Panel identification — "which registered panel is this physical ESP32?".

Position in the architecture (Phase 2C.5):

    connected ESP32
          |
    DeviceMonitor            detection + MAC acquisition (the hardware I/O;
    (monitor.py)             esptool probe cached per port, held off while
          |                  a flash owns the port)
          v
    DeviceState.mac
          |
    identify_panel  ------>  PanelRegistry.resolve(mac)      (panels.py, pure)
    (this module)                    |
          |                          v
          v                  PanelDefinition -> FirmwareConfiguration
    PanelIdentification
          |
    [Phase 2D] provisioning consumes it — NOT in this phase

WHY THIS DOES NOT READ THE MAC ITSELF. `DeviceMonitor` already obtains it,
and owns the two invariants that make doing so safe: probe once per plug-in
(reading a MAC resets the board) and never while a flash holds the port. A
second probe path here would duplicate the mechanism and bypass both. So
this service *delegates* acquisition to the monitor and *delegates* lookup
to the registry, and its own logic is only the small, pure mapping between
the two — `identify_panel` — which takes values, not devices.

IDENTIFICATION NEVER TRIGGERS ANYTHING. Resolving a panel returns data. It
does not compile, flash, provision firmware, start a scenario, emit a
`ScenarioEvent`, or record a Hack Mode row. Nothing in this module imports
`app.build`, `app.scenarios`, or `app.events`, and no caller in Phase 2C.5
acts on the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.hardware.firmware import FirmwareConfiguration
from app.hardware.monitor import DeviceMonitor, device_monitor
from app.hardware.panels import PanelDefinition, PanelMatch, PanelRegistry
from app.hardware.state import DeviceState


class PanelIdentificationStatus(str, Enum):
    """Where identification of the attached board stands."""

    #: No single ESP32 is attached (disconnected, ambiguous, not checked,
    #: or detection could not run). There is no board to identify.
    NOT_CONNECTED = "not_connected"
    #: A board is attached but its MAC has not been read — the probe has not
    #: run yet, is held off by a flash, or failed. Not "unknown panel": we do
    #: not yet know which board this is.
    UNIDENTIFIED = "unidentified"
    #: The MAC was read and no panel is registered for it.
    UNREGISTERED = "unregistered"
    #: The MAC is bound to a registered panel.
    IDENTIFIED = "identified"


@dataclass(frozen=True)
class PanelIdentification:
    """The answer for one attached board. Passive; carries no capability."""

    status: PanelIdentificationStatus
    #: The real detected serial port, when a board is attached.
    port: str | None = None
    #: Canonical MAC, once read.
    mac: str | None = None
    #: The registered panel, only when IDENTIFIED.
    panel: PanelDefinition | None = None

    @property
    def identified(self) -> bool:
        return self.status is PanelIdentificationStatus.IDENTIFIED

    @property
    def firmware(self) -> FirmwareConfiguration | None:
        """The identified panel's firmware configuration, if it has one."""
        return self.panel.firmware if self.panel is not None else None


def identify_panel(state: DeviceState, registry: PanelRegistry) -> PanelIdentification:
    """Map one device state to a panel identification. Pure — no I/O."""
    if not state.connected:
        return PanelIdentification(status=PanelIdentificationStatus.NOT_CONNECTED)

    resolution = registry.resolve(state.mac)
    if resolution.match is PanelMatch.NO_IDENTITY:
        return PanelIdentification(
            status=PanelIdentificationStatus.UNIDENTIFIED, port=state.port
        )
    if resolution.match is PanelMatch.UNREGISTERED:
        return PanelIdentification(
            status=PanelIdentificationStatus.UNREGISTERED,
            port=state.port,
            mac=resolution.mac,
        )
    return PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port=state.port,
        mac=resolution.mac,
        panel=resolution.panel,
    )


class PanelIdentificationService:
    """Answers "which registered panel is attached?" for the whole process.

    `monitor` defaults to the process-wide `device_monitor`, the same one
    both modes read. `registry` defaults to that monitor's own registry, so
    the panel name in the hardware header and the definition returned here
    always come from one lookup table.
    """

    def __init__(
        self,
        monitor: DeviceMonitor | None = None,
        registry: PanelRegistry | None = None,
    ) -> None:
        self._monitor = monitor if monitor is not None else device_monitor
        self._registry = registry

    def _active_registry(self) -> PanelRegistry:
        return self._registry if self._registry is not None else self._monitor.panel_registry

    def identify(self) -> PanelIdentification:
        """Identify from the monitor's current state. No I/O, no probe."""
        return identify_panel(self._monitor.snapshot(), self._active_registry())

    async def refresh(self) -> PanelIdentification:
        """Re-detect through the monitor, then identify.

        Any MAC read happens inside `DeviceMonitor.refresh`, under its
        per-port cache and flash hold — exactly as a header poll would.
        """
        state = await self._monitor.refresh()
        return identify_panel(state, self._active_registry())


#: Process-wide identification over the shared `device_monitor`. Nothing in
#: Phase 2C.5 acts on its answers; it is the seam Phase 2D consumes.
panel_identification_service = PanelIdentificationService()
