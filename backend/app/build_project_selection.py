"""Which project a new Build Mode session loads (Phase B2).

THE OTHER HALF OF THE BRIDGE. `app/build_panel_resolution.py` already
answers "which panel is attached, and what package does it declare?", and
`app/build/sketch_source.py` already answers "how does a sketch directory
become a `BuildProject`?". Neither knows the other exists. This module runs
the first, feeds its answer into the second, and hands the resulting
workspace to whoever is creating a session:

    resolve_build_panel_resources()      PanelResources (MAC -> panel -> package)
              |
              v
    package.firmware_sketch_path         the panel's REAL .ino, already
              |                          validated by app/panels/loader.py
              v
    load_sketch_project()                B1 discovery -> BuildDocument ->
              |                          CodeSection[] -> FileSegment[]
              v
    BuildWorkspace -> BuildSession -> the existing Build Mode

It is the Build Mode counterpart of `app/scenario_selection.py`, deliberately
built to the same shape — a `*Selection` value object carrying the chosen
thing plus an honest account of why, a selector whose collaborators are
injectable, and a pure `select_for(resources)` so the whole decision table is
testable from plain values with no hardware and no filesystem.

PANEL RESOLUTION STAYS OUTSIDE THE SESSION. `BuildSession` receives a ready
`BuildWorkspace` and has never heard of a MAC, a panel id, a package id,
`panel.json`, or a resource root — exactly as `HackSession` receives a
`Scenario`. The flow is `panel resolution -> project materialization ->
session`, never `session -> registry -> package -> filesystem`.

ONE RESOLUTION PER CONNECTION. `select()` resolves the panel once and reads
the package once; `app/build_websocket.py` takes both the workspace and the
`panel_id` off the single `BuildProjectSelection` it gets back, rather than
resolving the same chain twice for two different fields.

SELECTION IS A LOOKUP PLUS A FILE READ, NOT AN ACTION. `resolve()` — the
passive read of the shared device monitor's current state — is what runs, so
opening Build Mode still triggers no `arduino-cli`, no MAC probe (which would
reset the board), and no open port. The one thing added beyond what Build Mode
already did at connect is reading the panel's own `.ino` text from the trusted
package root. Nothing here compiles, flashes, provisions, validates, starts a
scenario, or emits an event.

FAILURE IS A SELECTION, NOT AN EXCEPTION — BUT IT IS NO LONGER LED BLINK.
A student opening Build Mode must get a `BuildProjectSelection`; it does not
follow that the selection names a real activity. Every way the chain can fail
to produce a panel project — the monitor has not looked yet, no board, an
unread MAC, an unregistered board, a registered panel with no courseware, a
broken package, a package that declares no firmware, a firmware reference B2
cannot materialize, or source the structural analyzer chokes on — falls back
to `create_no_device_workspace()` (`app/build/no_device.py`), an inert
placeholder that names no activity, and `source` records `NONE` so the
connection lifecycle knows this session has nothing to offer
(`BuildSession.has_active_project`, set False by `app/build_websocket.py`).
The reason is logged rather than shown to the student, but the important
correction is behavioural, not cosmetic: the LED Blink pipeline-proof project
(`app/build/blink.py`) is no longer reachable from this selector at all — it
remains a hand-constructible fixture for direct tests and nothing on the real
`/ws/build` connection path can select it. The one thing that must never
happen is silently loading some *other* panel's firmware, and that cannot
happen here: the only panel firmware this module can choose is the one the
attached panel's own package declared.

NO PANEL BRANCH. There is no `if panel_id == ...` here and none is permitted.
The package names its firmware; the loader validates it; the sketch reader
materializes it. All of it is data.

PHASE B8 ADDS ONE MORE THING READ OFF THE SAME PACKAGE, AND IT IS STILL DATA.
A panel's remediation declaration now names which discovered sections a
student may edit, which are for exploring, and which one is the remediation
region (`app/panels/models.py::RemediationDeclaration`). Those ids are passed
straight into `load_sketch_project`, which turns them into the project's
`ProjectPolicy` (`app/build/policy.py`). The decision is the courseware's;
this module only carries it, exactly as it carries the project id, the board
and the firmware name. A package with no remediation declaration produces the
same read-only project B2 produced, and an id naming a section this firmware
does not have is a materialization failure that falls back to the default
project with the reason logged — never a silently ignored permission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.build import BuildWorkspace, create_no_device_workspace
from app.build.document_project import DocumentProjectError, board_info_from_fqbn
from app.build.sketch_source import SketchSourceError, load_sketch_project
from app.build_panel_resolution import resolve_build_panel_resources
from app.hardware.firmware import FirmwareSourceKind
from app.panels.models import PanelPackage
from app.panels.service import PanelResources, PanelResourceService, PanelResourceStatus

logger = logging.getLogger(__name__)


class BuildProjectSource(str, Enum):
    """Where the loaded project came from. Two answers, never ambiguous."""

    #: The attached panel's own package declared this firmware, and it was
    #: materialized from the real sketch that package ships. The only way a
    #: panel-specific firmware is ever loaded.
    PANEL_PACKAGE = "panel_package"
    #: No activity. Used whenever a panel's firmware could not be
    #: materialized — including the ordinary no-hardware flow, an
    #: unidentified/unregistered board, or a registered panel with no
    #: courseware yet. Not an error state on its own (the reason is carried
    #: separately in `detail`/`panel_status`), but it is never a real
    #: activity: the workspace this produces is `create_no_device_workspace()`
    #: (`app/build/no_device.py`), never LED Blink.
    NONE = "none"


@dataclass(frozen=True)
class BuildProjectSelection:
    """One selection outcome: the workspace, and an honest account of why."""

    #: The workspace to attach to a session. Always present: selection never
    #: fails to produce one, it only records what it had to fall back to.
    #: Freshly constructed per selection, so no two sessions share it.
    workspace: BuildWorkspace
    source: BuildProjectSource
    panel_status: PanelResourceStatus
    #: The identified panel, when there was one. This is the same value Build
    #: Mode already recorded on the session before B2 — carried here so the
    #: connection path reads one object instead of resolving twice.
    panel_id: str | None = None
    #: Why the selection is not a panel package's, when there is something to
    #: say. Empty for a clean PANEL_PACKAGE selection.
    detail: str = ""
    #: The resolved package itself, when the panel chain produced one. Added
    #: in Phase B7 so the connection lifecycle can also choose the session's
    #: VALIDATOR (`app/build_validation_selection.py`) off this one
    #: resolution instead of walking the MAC -> panel -> package chain a
    #: second time. Carrying it changes nothing about the selection: it is
    #: present even when the firmware could not be materialized and the
    #: workspace fell back to the default, because "which panel's courseware
    #: is attached" and "whose firmware loaded" are different questions.
    package: PanelPackage | None = None

    @property
    def from_panel(self) -> bool:
        return self.source is BuildProjectSource.PANEL_PACKAGE

    @property
    def has_active_project(self) -> bool:
        """Whether this selection names a real activity a student may use.

        False exactly when `source` is `NONE` — the placeholder workspace
        loaded, not a panel's firmware. `app/build_websocket.py` reads this
        once, at connect, onto `BuildSession.has_active_project`.
        """
        return self.source is not BuildProjectSource.NONE

    @property
    def project_id(self) -> str:
        """The materialized project's own id — diagnostics and logging."""
        return self.workspace.project.project_id

    def describe(self) -> str:
        """One log line. Never written to the wire or shown to a student."""
        panel = self.panel_id or "-"
        suffix = f" ({self.detail})" if self.detail else ""
        return (
            f"project={self.project_id} source={self.source.value} "
            f"panel={panel} panel_status={self.panel_status.value}{suffix}"
        )


class BuildProjectSelector:
    """Chooses the project a new Build Mode session starts in.

    `resources` is injectable so a test can drive the whole lifecycle — a
    fake device view, a temporary package root — without hardware and without
    touching the shipped packages, the same convention
    `SessionScenarioSelector` follows.
    """

    def __init__(self, resources: PanelResourceService | None = None) -> None:
        self._resources = resources

    def select(self) -> BuildProjectSelection:
        """Select from the device state the shared monitor already holds."""
        return self.select_for(resolve_build_panel_resources(self._resources))

    def select_for(self, resources: PanelResources) -> BuildProjectSelection:
        """The mapping `PanelResources -> BuildProjectSelection`.

        Separated from `select` so the decision table is testable from plain
        values, with no device state involved.
        """
        panel = resources.panel
        panel_id = panel.panel_id if panel is not None else None

        package = resources.package
        if resources.status is not PanelResourceStatus.READY or package is None:
            # Every non-READY state lands here: NOT_CHECKED, NOT_CONNECTED,
            # UNIDENTIFIED, UNREGISTERED, NO_PACKAGE, PACKAGE_ERROR. They are
            # genuinely different situations (`panel_status` keeps them
            # distinct), but they share the one fact that decides the
            # outcome: no package named a firmware, so there is no panel
            # firmware to load and guessing one is exactly the accident this
            # phase must prevent.
            return self._fallback(resources.status, panel_id, resources.detail)

        try:
            project = self._project_for(package)
        except (SketchSourceError, DocumentProjectError) as error:
            # Valid courseware whose firmware source this build cannot
            # materialize — an authoring or deployment problem. It is
            # reported and falls back; it must not drop the student's
            # workspace, and it must not quietly load a different panel's
            # firmware.
            logger.warning(
                "panel %s firmware could not be materialized (%s); using the default project",
                panel_id,
                error,
            )
            return self._fallback(resources.status, panel_id, str(error), package)

        return BuildProjectSelection(
            workspace=BuildWorkspace(project),
            source=BuildProjectSource.PANEL_PACKAGE,
            panel_status=resources.status,
            panel_id=panel_id,
            package=package,
        )

    def _project_for(self, package: PanelPackage):
        """The package's declared firmware, as a `BuildProject`.

        Every identity value is read off the package — there is no table here
        mapping a panel to a name, a board or a project id, and nothing is
        invented: `firmware_id` names the project, the declared `scenario_id`
        is carried through so a `WORKSPACE_LOADED` row says which experiment
        this firmware belongs to, the panel id is the module, and the
        scenario's own title is what the student sees.
        """
        firmware = package.firmware
        if firmware is None:
            raise SketchSourceError(f"package {package.panel_id!r} declares no firmware")
        if firmware.source.kind is not FirmwareSourceKind.SKETCH_DIRECTORY:
            # A BUILD_PROJECT reference names a `BuildProject` defined in
            # backend source rather than a sketch on disk. Resolving one is a
            # different mechanism with its own registry, and no shipped
            # package uses it; B2 materializes sketches only and says so
            # instead of silently loading something else.
            raise SketchSourceError(
                f"package {package.panel_id!r} firmware source kind "
                f"{firmware.source.kind.value!r} is not materialized by this build"
            )
        sketch = package.firmware_sketch_path
        if sketch is None:
            raise SketchSourceError(
                f"package {package.panel_id!r} firmware sketch path is unresolved"
            )
        return load_sketch_project(
            sketch,
            project_id=firmware.firmware_id,
            scenario_id=package.scenario_id,
            module_id=package.panel_id,
            firmware_name=package.title,
            board=board_info_from_fqbn(firmware.board.fqbn),
            # THIS MODULE STILL DECIDES NOTHING ABOUT EDITABILITY. B2 passed
            # nothing here because no phase had made that decision yet; B8
            # makes it in the one place that can — the panel's own
            # remediation declaration — and this reads it off the package
            # exactly as it reads the project's identity and board. There is
            # no default, no inference from a section's kind, and above all
            # no table here mapping a panel to a set of editable functions.
            # A package that declares no remediation and no `build` block, or
            # one that declares its remediation in prose only, yields the
            # same read-only project B2 produced.
            **self._policy_for(package),
        )

    @staticmethod
    def _policy_for(package: PanelPackage) -> dict[str, object]:
        """The panel's declared section policy, as `load_sketch_project` args.

        Every value comes from `package.remediation` or, for a panel with no
        remediation activity, `package.build`; a package declaring neither
        gives the empty, conservative arguments B2 always passed. Whether the
        named sections actually exist in this firmware is checked downstream
        by `app/build/document_project.py`, which holds the document and can
        therefore reject an unknown id rather than ignore it — and a rejection
        there surfaces as an ordinary selection fallback, never a broken
        session.
        """
        remediation = package.remediation
        if remediation is not None and remediation.declares_section_policy:
            return {
                "editable_section_ids": remediation.editable_section_ids,
                "explore_section_ids": remediation.explore_section_ids,
                "security_region_id": remediation.security_section_id,
            }
        # No remediation policy, but a panel may still declare which of its
        # own sections Build Mode opens (`BuildDeclaration` — a foundation
        # package with real firmware and no vulnerability to fix, or one whose
        # remediation is described in prose only). It names no security
        # region, because a panel with no remediation policy has none, and
        # `PanelPackage` guarantees this block and a remediation policy never
        # both exist, so there is still exactly one list to read.
        build = package.build
        if build is not None:
            return {
                "editable_section_ids": build.editable_section_ids,
                "explore_section_ids": build.explore_section_ids,
                "security_region_id": None,
            }
        return {
            "editable_section_ids": (),
            "explore_section_ids": (),
            "security_region_id": None,
        }

    def _fallback(
        self,
        panel_status: PanelResourceStatus,
        panel_id: str | None,
        detail: str,
        package: PanelPackage | None = None,
    ) -> BuildProjectSelection:
        """No activity, with the reason attached.

        Deliberately NOT "the last panel's firmware", "the only registered
        panel's firmware", "whatever a partial identification suggests", or —
        since this correction — LED Blink. The physical ESP32 selects the
        activity; when it does not resolve to one, there is no activity to
        load, and `create_no_device_workspace()` is an honest placeholder
        that says so rather than a real firmware project standing in for one.
        """
        return BuildProjectSelection(
            workspace=create_no_device_workspace(),
            source=BuildProjectSource.NONE,
            panel_status=panel_status,
            panel_id=panel_id,
            detail=detail,
            package=package,
        )


def default_build_project_selector() -> BuildProjectSelector:
    """A selector over the process-wide monitor and the configured root.

    Built at call time, like `default_panel_resource_service()`, so a changed
    package root is honoured without reloading the import graph. Construction
    performs no I/O.
    """
    return BuildProjectSelector()


def select_build_project() -> BuildProjectSelection:
    """The one call the Build Mode connection lifecycle makes."""
    return default_build_project_selector().select()
