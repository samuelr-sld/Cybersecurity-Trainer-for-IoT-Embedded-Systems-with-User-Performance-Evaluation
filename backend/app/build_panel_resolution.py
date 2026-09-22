"""Which panel Build Mode's session sees at connect — Phase 2E.3.

    attached ESP32 -> MAC -> PanelRegistry -> PanelDefinition -> PanelPackage
        (app/hardware/, app/panels/ — already built, Phase 2D.1)
              |
              v
    resolve_build_panel_resources()   (this module)
              |
              v
    PanelResources   (identity + package, the SAME type Hack Mode's
                       `app/scenario_selection.py` already reads)

THIS IS A THIN CALL, NOT A NEW ARCHITECTURE. `PanelResourceService` (`app/
panels/service.py`) already does 100% of the work — MAC → panel →
package — for Hack Mode. Build Mode needs the exact same answer for a
different purpose (reading a panel's `remediation` declaration and
recording which panel a Build session's evidence belongs to), not a
DIFFERENT chain, so this module exists only to give Build Mode its own,
separately-documented entry point rather than reaching into `app.
scenario_selection` (Hack Mode's own module, which additionally resolves a
`Scenario` — a Hack-only concept Build Mode has no use for and must not
import, keeping the two modes' execution environments separate per the
Phase 2E.3 brief).

PASSIVE, LIKE ITS HACK MODE COUNTERPART. `resolve()` reads the shared
device monitor's CURRENT state; it never detects, probes a MAC, or opens a
port. Calling this at Build Mode session start therefore costs nothing a
`hardware_status` poll does not already cost, and — same as Hack Mode's
`select_session_scenario()` — a board that cannot be resolved (nothing
attached, unidentified, unregistered, no package, a broken package) is not
an error: `BuildSession.panel_id` is simply None, the same honest
"unresolved" state Hack Mode's default-scenario fallback represents.

NO PANEL BRANCH. This module does not choose behaviour based on which
panel resolved — it has no `if panel_id == ...` and cannot: it returns data
(`PanelResources`), and the caller decides what to do with it, exactly as
`app/scenario_selection.py` hands its result to `SessionManager.create`
without branching on panel identity itself.
"""

from __future__ import annotations

from app.panels.service import PanelResourceService, PanelResources


def resolve_build_panel_resources(
    service: PanelResourceService | None = None,
) -> PanelResources:
    """The attached panel's resources, for a Build Mode session to read.

    `service` is injectable so a test can drive this without real hardware
    or the shipped package root — the same convention
    `app/scenario_selection.py::SessionScenarioSelector` follows.
    """
    resolver = service if service is not None else PanelResourceService()
    return resolver.resolve()
