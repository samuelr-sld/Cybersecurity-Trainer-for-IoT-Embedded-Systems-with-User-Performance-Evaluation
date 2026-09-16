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
          v                  PanelDefinition (identity + package_id)
    PanelIdentification
          |
    PanelResourceService (app/panels/service.py) loads the panel's package,
          |              which owns its scenario + firmware configuration
    [later phase] provisioning consumes it — NOT in this phase

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
`app.build`, `app.scenarios`, `app.events`, or `app.panels`, and no caller
acts on the result — loading a panel's resource package is likewise inert,
and happens one layer up in `app/panels/service.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.hardware.monitor import DeviceMonitor, device_monitor
from app.hardware.panels import PanelDefinition, PanelMatch, PanelRegistry
from app.hardware.state import DeviceState


class PanelIdentificationStatus(str, Enum):
    """Where identification of the attached board stands."""

    #: NO DETECTION HAS COMPLETED YET in this process, so there is nothing
    #: to identify *from*. Deliberately not NOT_CONNECTED: the shared
    #: `DeviceStatus` vocabulary keeps "we have not looked" apart from "we
    #: looked and found nothing" (see `app/hardware/state.py`), and
    #: flattening the two here would make a cold monitor indistinguishable
    #: from an empty USB port — which is how a consumer ends up confidently
    #: choosing a fallback for a board that is, in fact, plugged in.
    #:
    #: Transient by construction: the monitor's initial detection is started
    #: by the application lifespan (`app/main.py`), so this is the state
    #: between process start and that detection completing.
    NOT_CHECKED = "not_checked"
    #: A detection ran and no single ESP32 is attached (disconnected,
    #: ambiguous, or detection could not run). There is no board to
    #: identify.
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
    def package_id(self) -> str | None:
        """The resource package this panel references, if it has one.

        An identifier, not a path, and not the package itself: loading one
        is `app/panels/loader.py`'s job, and this module stays free of the
        filesystem so identification remains pure (see `identify_panel`).
        Since Phase 2D.1 a panel's FIRMWARE configuration lives in that
        package rather than on the definition, so there is deliberately no
        `firmware` property here — `PanelResources.firmware`
        (`app/panels/service.py`) is the one route to it.
        """
        return self.panel.package_id if self.panel is not None else None


def identify_panel(state: DeviceState, registry: PanelRegistry) -> PanelIdentification:
    """Map one device state to a panel identification. Pure — no I/O.

    TWO QUESTIONS BEFORE ANY LOOKUP, AND NEITHER IS `connected`.

    `checked` first: answering "no panel" for a monitor that has not run its
    first detection would be an invention, not an observation, and a caller
    acting on it (scenario selection, say) would act on that invention.

    Then `board_present` rather than `connected`, for the same reason one
    step further on. A re-detection publishes DETECTING over the previous
    verdict, keeping its port, MAC and panel, so `connected` goes False for
    a board that is still plugged in and this function would report
    NOT_CONNECTED purely because a poll happens to be in flight. Nothing has
    established that the panel disappeared — we are merely asking again — so
    the honest answer is the one we already had, which is exactly what the
    retained fields below resolve to. When the detection completes the
    monitor replaces the state wholesale and the next call reports the new
    verdict; there is nothing here to invalidate.

    This function still performs no detection of its own — it only refuses
    to turn silence, or an outstanding question, into a verdict.
    """
    if not state.checked:
        return PanelIdentification(status=PanelIdentificationStatus.NOT_CHECKED)
    if not state.board_present:
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
