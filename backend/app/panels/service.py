"""The one place the whole chain is joined: attached board -> its experiment.

Position in the architecture (Phase 2D.1):

    connected ESP32
          |
    DeviceMonitor                 detection + MAC (hardware I/O, cached per
    (hardware/monitor.py)         plug-in, held off while a flash owns the port)
          |
          v
    PanelIdentificationService    MAC -> PanelDefinition, or an explicit
    (hardware/panel_identification.py)   unidentified/unregistered answer
          |
          v
    PanelPackageLoader            PanelDefinition.package_id -> PanelPackage
    (panels/loader.py)
          |
          v
    PanelResources                identity + scenario configuration +
    (this module)                 firmware configuration, as one answer
          |
    [later phase] provisioning / scenario selection / evaluation consume it

NOTHING IS TRIGGERED BY RESOLVING RESOURCES. This service reads. It does not
compile, flash, provision firmware, select or start a `Scenario`, register or
filter a command, emit a `ScenarioEvent`, or write a Hack Mode event row.
Identifying a panel and loading its package are both inert, and that is the
firmware-provisioning boundary this phase is required to hold: the package
may DEFINE the firmware and its build/flash configuration, and this service
may prove the definition RESOLVES, but the compile -> flash -> verify ->
start lifecycle belongs to a later phase and has no caller here.

COMPOSITION, NOT REIMPLEMENTATION. Every step above already existed or was
built in its own module; this file owns no lookup table, no MAC handling, no
filesystem knowledge and no validation of its own. Its whole job is to run
the two existing services in order and turn their failure modes into one
honest status a caller can render.

LIBRARIES RAISE, SERVICES REPORT. `PanelPackageLoader` raises for a missing
or invalid package, because a library that swallowed those would hide an
authoring mistake. This service catches them and reports a status plus a
detail string, because a UI asking "what is plugged in?" must not crash over
courseware that has not been written yet. The exception is never discarded —
its message becomes `detail`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.hardware.firmware import FirmwareConfiguration
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationService,
    PanelIdentificationStatus,
)
from app.hardware.panels import PanelDefinition
from app.panels.loader import (
    PanelPackageError,
    PanelPackageLoader,
    PanelPackageNotFoundError,
    default_panel_package_loader,
)
from app.panels.models import PanelPackage, ScenarioDefinition


class PanelResourceStatus(str, Enum):
    """How far the chain got. Each member is a genuinely different answer."""

    #: THE MONITOR HAS NOT COMPLETED ITS FIRST DETECTION. We have not looked
    #: yet, so we cannot say what is attached — and must not answer as if we
    #: had. Kept apart from NOT_CONNECTED for the reason the shared
    #: `DeviceStatus` keeps NOT_CHECKED apart from DISCONNECTED: a consumer
    #: that treats "not looked" as "nothing there" will confidently do the
    #: wrong thing for a board that is in fact plugged in. The application
    #: lifespan starts that first detection at startup (`app/main.py`), so
    #: this is a brief startup state, not a resting one.
    NOT_CHECKED = "not_checked"
    #: A detection ran and no single ESP32 is attached. There is no panel to
    #: load resources for.
    NOT_CONNECTED = "not_connected"
    #: A board is attached but its MAC has not been read yet (the probe has
    #: not run, is held off by a flash, or failed). Not "unknown panel".
    UNIDENTIFIED = "unidentified"
    #: The MAC was read and no panel is registered for it. The UI shows the
    #: MAC; nothing is guessed.
    UNREGISTERED = "unregistered"
    #: A registered panel, which references no package yet. An ORDINARY
    #: state of the trainer while the other panels' courseware is written —
    #: deliberately not an error, and deliberately not the same as the next
    #: member.
    NO_PACKAGE = "no_package"
    #: A registered panel whose package is declared but missing, malformed,
    #: or missing its firmware resource. This IS a problem, in the trainer's
    #: own resources rather than in the student's hardware; `detail` says
    #: which.
    PACKAGE_ERROR = "package_error"
    #: A registered panel with a valid package loaded. Its scenario
    #: configuration and firmware configuration are available.
    READY = "ready"


@dataclass(frozen=True)
class PanelResources:
    """Everything known about the attached panel's experiment. Passive.

    Carries no capability: there is no method here that acts on the firmware
    it describes, and holding one of these grants nothing.
    """

    status: PanelResourceStatus
    identification: PanelIdentification
    package: PanelPackage | None = None
    #: Why the status is not READY, when there is something to say. Empty
    #: for READY and for the ordinary NOT_CONNECTED case.
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status is PanelResourceStatus.READY

    @property
    def panel(self) -> PanelDefinition | None:
        return self.identification.panel

    @property
    def mac(self) -> str | None:
        return self.identification.mac

    @property
    def scenario(self) -> ScenarioDefinition | None:
        """The scenario configuration this panel declares, once loaded."""
        return None if self.package is None else self.package.scenario

    @property
    def firmware(self) -> FirmwareConfiguration | None:
        """The firmware configuration, resolved through the package.

        This is the only route to a panel's firmware. `PanelDefinition` does
        not carry one: a panel points at a package, and the package owns the
        firmware description, so there is exactly one place that answers
        "which firmware belongs to this panel?".
        """
        return None if self.package is None else self.package.firmware


class PanelResourceService:
    """Answers "what experiment is the attached panel running?".

    Both dependencies are injectable so a test — or a later phase wanting a
    different resource root — can drive the whole chain without real
    hardware and without touching the shipped packages.
    """

    def __init__(
        self,
        identification: PanelIdentificationService | None = None,
        loader: PanelPackageLoader | None = None,
    ) -> None:
        self._identification = (
            identification if identification is not None else PanelIdentificationService()
        )
        self._loader = loader if loader is not None else default_panel_package_loader()

    @property
    def loader(self) -> PanelPackageLoader:
        return self._loader

    def resolve(self) -> PanelResources:
        """Resources for the currently known device state. No probe, no I/O
        against the board — the package is read from disk, nothing else."""
        return self._resources(self._identification.identify())

    async def refresh(self) -> PanelResources:
        """Re-detect through the monitor, then resolve.

        Any MAC read happens inside `DeviceMonitor.refresh`, under its
        per-port cache and its flash hold, exactly as a header poll would.
        This adds no second probe path.
        """
        return self._resources(await self._identification.refresh())

    def for_panel(self, panel: PanelDefinition) -> PanelPackage:
        """The package a given panel references, ignoring what is plugged in.

        The seam a later phase uses to inspect a panel's experiment without
        a board attached — and what makes the registry -> package chain
        testable on its own. Raises `PanelPackageError` rather than
        reporting a status, because a caller naming a panel explicitly is
        asking a question that has a right answer.
        """
        return self._loader.load_for_panel(panel)

    def _resources(self, identification: PanelIdentification) -> PanelResources:
        status = identification.status
        if status is PanelIdentificationStatus.NOT_CHECKED:
            return PanelResources(
                PanelResourceStatus.NOT_CHECKED,
                identification,
                detail="the hardware monitor has not completed its first detection",
            )
        if status is PanelIdentificationStatus.NOT_CONNECTED:
            return PanelResources(PanelResourceStatus.NOT_CONNECTED, identification)
        if status is PanelIdentificationStatus.UNIDENTIFIED:
            return PanelResources(
                PanelResourceStatus.UNIDENTIFIED,
                identification,
                detail="the attached board's MAC has not been read",
            )
        if status is PanelIdentificationStatus.UNREGISTERED:
            return PanelResources(
                PanelResourceStatus.UNREGISTERED,
                identification,
                detail=f"no panel is registered for MAC {identification.mac}",
            )

        panel = identification.panel
        if panel.package_id is None:
            return PanelResources(
                PanelResourceStatus.NO_PACKAGE,
                identification,
                detail=f"panel {panel.panel_id!r} has no resource package yet",
            )
        try:
            package = self._loader.load_for_panel(panel)
        except PanelPackageNotFoundError as error:
            # A declared package that is not on disk is an installation
            # problem, not an unfinished panel — the panel said it has one.
            return PanelResources(
                PanelResourceStatus.PACKAGE_ERROR, identification, detail=str(error)
            )
        except PanelPackageError as error:
            return PanelResources(
                PanelResourceStatus.PACKAGE_ERROR, identification, detail=str(error)
            )
        return PanelResources(PanelResourceStatus.READY, identification, package=package)


def default_panel_resource_service() -> PanelResourceService:
    """A service over the process-wide monitor and the configured root.

    Built at call time for the same reason `default_panel_registry()` and
    `default_panel_package_loader()` are.

    TWO CALLERS, SINCE PHASE 2E.3. `app/scenario_selection.py` resolves
    resources once per Hack Mode connection, to choose the scenario that
    session runs; `app/build_panel_resolution.py` resolves the same
    resources once per Build Mode connection, to read a panel's
    `remediation` declaration and to record which panel a Build session's
    evidence belongs to. Both use `resolve()` — the passive read — so
    opening either a terminal or a Build Mode workspace still triggers no
    detection. Still nobody calls `refresh()` through this factory: no
    command handler, no frontend frame, and no provisioning path does, and
    the compile -> flash -> verify -> start lifecycle stays inert here.
    """
    return PanelResourceService()
