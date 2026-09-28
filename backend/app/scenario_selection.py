"""Which `Scenario` a new Hack Mode session starts in (Phase 2D.4).

THE ONE PLACE THE TWO HALVES MEET. Phases 2D.1-2D.3 built both halves of the
chain and deliberately left them unjoined:

    attached ESP32 -> MAC -> PanelRegistry -> PanelDefinition -> PanelPackage
        (app/hardware/, app/panels/ — "which experiment does this panel run?")

    scenario_id -> ScenarioRegistry -> a fresh Scenario
        (app/scenarios/registry.py — "which class implements that experiment?")

This module runs the first, feeds its answer into the second, and hands the
result to whoever is creating a session. That is its entire job.

WHY IT IS ITS OWN MODULE, NOT A FEW LINES IN `sessions.py`. The two halves
are forbidden to import each other: `app.panels` may not import
`app.scenarios` (asserted statically by `tests/test_panel_packages.py`), and
`app.scenarios.registry` keeps `PanelPackage` behind `TYPE_CHECKING` so the
scenario layer takes no runtime dependency on the package layer. A
composition of two layers that may not know about each other has to live in
a third place. Putting it here also keeps `HackSession` exactly as ignorant
as it was: it receives a `Scenario` object and has never heard of a MAC, a
panel id, a package id, `panel.json`, or a resource root.

SELECTION IS A LOOKUP, NOT AN ACTION. Everything this module does is: read
the device state the shared monitor already holds, read a JSON manifest from
the trusted resource root (via the existing loader), look a string up in a
table, and construct an in-memory object. It does NOT detect hardware, run
`arduino-cli`, probe a MAC, open a serial port, connect to MQTT, compile or
flash firmware, start the experiment, emit a `ScenarioEvent`, or write an
evaluation row. `resolve()` — not `refresh()` — is what it calls on the
resource service, precisely so that creating a session never triggers a
device detection as a side effect.

NO PANEL BRANCHES. There is no `if panel_id == ...` here and none is
permitted. A panel's package names its scenario; the scenario registry maps
that name to an implementation; both are data. Adding a panel is a package
plus a registration, never a branch in this file.

IT READS A CACHE SOMEONE ELSE FILLS. `resolve()` returns whatever the shared
monitor currently knows, and until the monitor's first detection completes
that is NOT_CHECKED — "nobody has looked", which is NOT the same answer as
"nothing is attached" and is reported as its own status rather than folded
into NOT_CONNECTED. Filling the cache is the application lifespan's job
(`app/main.py` primes the monitor at startup), never this module's and never
a session's: selection must stay free of detection, so it reports what it
finds instead of going to look.

FAILURE IS A SELECTION, NOT AN EXCEPTION. A student opening a terminal must
get a terminal. Every way the chain can fail to name a panel-specific
scenario — a monitor that has not looked yet, no board attached, an unread
MAC, an unregistered board, a registered panel with no courseware yet, a
broken package, or a package naming a scenario id nothing implements —
resolves to the same safe answer: the trainer's default development
scenario, with `source` and `detail` recording why. The one thing that must never happen is silently running some
*other* panel's experiment, and that cannot happen here because the only
panel-specific scenario this module can ever choose is the one the attached
panel's own package declared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from app.panels.service import (
    PanelResources,
    PanelResourceService,
    PanelResourceStatus,
)

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.panels.models import PanelPackage
from app.scenarios import (
    DEFAULT_SCENARIO_ID,
    Scenario,
    ScenarioRegistry,
    UnknownScenarioError,
    default_scenario_registry,
)

logger = logging.getLogger(__name__)


class ScenarioSource(str, Enum):
    """Where the selected scenario came from. Two answers, never ambiguous."""

    #: The attached panel's own package declared this scenario id, and the
    #: registry resolved it. The only way a panel-specific experiment is ever
    #: chosen.
    PANEL_PACKAGE = "panel_package"
    #: The trainer's default development scenario. Used whenever a panel's
    #: experiment could not be named — including the ordinary no-hardware
    #: development flow, which is why this is not an error state.
    DEFAULT = "default"


@dataclass(frozen=True)
class ScenarioSelection:
    """One selection outcome: the scenario, and an honest account of why.

    Passive. Holding one grants no capability over the panel it describes —
    it carries a constructed simulation object and three facts about how it
    was chosen.
    """

    #: The instance to attach to a session. Always present: selection never
    #: fails to produce a scenario, it only records what it had to fall back
    #: to.
    scenario: Scenario
    source: ScenarioSource
    #: The id that was looked up in the `ScenarioRegistry` — the package's
    #: declared id for a PANEL_PACKAGE selection, `DEFAULT_SCENARIO_ID`
    #: otherwise. Kept separately from `scenario.scenario_id` because the two
    #: are different facts: this is what was *asked for*, that is what the
    #: resolved implementation *calls itself*, and while several ids may map
    #: to one implementation those will differ.
    scenario_id: str
    #: How far panel resolution got. `READY` with `source=DEFAULT` means the
    #: package loaded but its scenario id is not implemented.
    panel_status: PanelResourceStatus
    #: The identified panel, when there was one. Diagnostics only — nothing
    #: downstream branches on it, and it never reaches `HackSession`.
    panel_id: str | None = None
    #: The loaded package this selection came from, when it was a panel's.
    #: Carried purely as DATA (`resolve()` already had it in hand) so a
    #: separate composition step — `app/hack_live_mqtt.py` — can read the
    #: package's declared broker/topics/lab-identity without re-resolving the
    #: panel. None for a DEFAULT selection. This adds no behaviour here:
    #: selection stays a lookup, and this module still connects to nothing.
    package: "PanelPackage | None" = None
    #: Why the selection is not a panel package's, when there is something to
    #: say. Empty for a clean PANEL_PACKAGE selection.
    detail: str = ""

    @property
    def from_panel(self) -> bool:
        return self.source is ScenarioSource.PANEL_PACKAGE

    def describe(self) -> str:
        """One log line. Never written to the terminal or the wire."""
        panel = self.panel_id or "-"
        suffix = f" ({self.detail})" if self.detail else ""
        return (
            f"scenario={self.scenario_id} source={self.source.value} "
            f"panel={panel} panel_status={self.panel_status.value}{suffix}"
        )


class SessionScenarioSelector:
    """Chooses the scenario a new Hack Mode session starts in.

    Both collaborators are injectable so a test can drive the whole
    lifecycle — a fake device view, a temporary package root, an alternate
    scenario table — without hardware and without touching the shipped
    packages or the process-wide registry.
    """

    def __init__(
        self,
        resources: PanelResourceService | None = None,
        registry: ScenarioRegistry | None = None,
    ) -> None:
        self._resources = resources if resources is not None else PanelResourceService()
        self._registry = registry if registry is not None else default_scenario_registry

    def select(self) -> ScenarioSelection:
        """Select from the device state the shared monitor already holds.

        Passive by construction: this reads `PanelResourceService.resolve()`,
        which takes the monitor's current snapshot rather than re-detecting.
        Creating a session therefore never runs `arduino-cli board list`,
        never probes a MAC (which would reset the board), and never opens a
        port. The monitor's state is kept current by the 10s hardware header
        poll both modes already send.
        """
        return self.select_for(self._resources.resolve())

    def select_for(self, resources: PanelResources) -> ScenarioSelection:
        """The pure mapping `PanelResources -> ScenarioSelection`.

        Separated from `select` so the whole decision table is testable from
        plain values, with no device state and no filesystem involved.
        """
        panel = resources.panel
        panel_id = panel.panel_id if panel is not None else None

        if resources.status is not PanelResourceStatus.READY or resources.package is None:
            # Every non-READY state lands here: NOT_CHECKED, NOT_CONNECTED,
            # UNIDENTIFIED, UNREGISTERED, NO_PACKAGE, PACKAGE_ERROR. They are
            # genuinely
            # different situations (and `panel_status` keeps them distinct),
            # but they share the one thing that decides the outcome: no
            # package named an experiment, so there is no panel-specific
            # scenario to select and guessing one would be exactly the
            # accident this phase must prevent.
            return self._fallback(
                panel_status=resources.status,
                panel_id=panel_id,
                detail=resources.detail,
            )

        package = resources.package
        try:
            scenario = self._registry.create_for_package(package)
        except UnknownScenarioError as error:
            # The package is valid courseware naming an experiment this build
            # does not implement — an authoring/deployment mismatch. It is
            # reported and falls back; it must not drop the student's
            # terminal, and it must not quietly run a different panel's
            # scenario.
            logger.warning(
                "panel %s declares unimplemented scenario id %r; using the default scenario",
                panel_id,
                package.scenario_id,
            )
            return self._fallback(
                panel_status=resources.status,
                panel_id=panel_id,
                detail=str(error),
            )

        return ScenarioSelection(
            scenario=scenario,
            source=ScenarioSource.PANEL_PACKAGE,
            scenario_id=package.scenario_id,
            panel_status=resources.status,
            panel_id=panel_id,
            package=package,
        )

    def _fallback(
        self, *, panel_status: PanelResourceStatus, panel_id: str | None, detail: str
    ) -> ScenarioSelection:
        """The trainer's default scenario, with the reason attached.

        Deliberately NOT "the last panel's scenario", "the only registered
        panel's scenario", or "whatever a partial identification suggests".
        It is the same generic development target `create_default_scenario()`
        has always returned, resolved through the same registry so there is
        one selection algorithm and not two.
        """
        return ScenarioSelection(
            scenario=self._registry.create(DEFAULT_SCENARIO_ID),
            source=ScenarioSource.DEFAULT,
            scenario_id=DEFAULT_SCENARIO_ID,
            panel_status=panel_status,
            panel_id=panel_id,
            detail=detail,
        )


def default_session_scenario_selector() -> SessionScenarioSelector:
    """A selector over the process-wide monitor, registry and resource root.

    Built at call time, like `default_panel_resource_service()` and
    `default_panel_package_loader()`, so a changed package root is honoured
    without reloading the import graph. Construction performs no I/O.
    """
    return SessionScenarioSelector()


def select_session_scenario() -> ScenarioSelection:
    """The one call the Hack Mode connection lifecycle makes.

    Kept as a function rather than a module-level singleton for the reason
    above: the resource root and the panel registry are read at call time.
    """
    return default_session_scenario_selector().select()
