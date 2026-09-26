"""Which compile-time provisioning strategy a new Build Mode session gets.

THE FOURTH THING READ OFF ONE ALREADY-RESOLVED PANEL RESOLUTION.
`app/build_project_selection.py` turns the attached panel's package into the
workspace a session edits; `app/build_validation_selection.py` turns it into
who checks the student's remediation. This module takes the same
`BuildProjectSelection` and answers a third, independent question: does
compiling this session's workspace need real, non-committed credentials
injected into the throwaway copy the compiler sees?

    BuildProjectSelection.package        the panel's own PanelPackage (or None)
              |
              v
    package.scenario_id                  the experiment id — already public,
              |                          already what selects a Scenario/
              |                          ValidationStrategy
              v
    ProvisioningStrategyRegistry.create  the registry's one registered row
              |                          (none today — see below), or the
              |                          no-op default for every other panel
              v
    ProvisioningPlan -> BuildSession -> BuildService.compile_workspace

NO PANEL REGISTERS A STRATEGY TODAY. Panel 1's Option A compile-time
credential rewrite (real Wi-Fi/MQTT values injected into a throwaway
copy) was retired once its committed `.ino` started carrying its real
lab credentials as literals — see that file's own header comment and
`tests/test_build_pipeline_b8.py::test_the_committed_firmware_carries_its_real_literal_lab_credentials`.
The registry below is therefore empty, and every panel (Panel 1
included) resolves to `NULL_PROVISIONING_STRATEGY`, exactly as an
unregistered scenario id always has (`ProvisioningStrategyRegistry.create`
in `app/build/provisioning.py`). A future panel that needs real
compile-time credentials would register its own strategy in this table.

SELECTION IS A REGISTRY LOOKUP, NOT AN ACTION. Nothing here reads a fixture,
touches a filesystem, or compiles anything — choosing a strategy never runs
one. `app/build/provisioning.py` documents why the engine
(`app/build/service.py`) must never branch on a panel id itself; this module
is where that branch-free choice is made, exactly as
`select_build_validation` does for validators.
"""

from __future__ import annotations

from app.build.provisioning import ProvisioningPlan, ProvisioningStrategyRegistry
from app.build_project_selection import BuildProjectSelection

#: Empty on purpose — see the module docstring. Held at module scope (like
#: `app.build.validation.default_validation_registry`) so every session
#: without an injected `registry=` shares one instance rather than building
#: a fresh empty table per selection.
default_provisioning_registry = ProvisioningStrategyRegistry()


def select_build_provisioning(
    selection: BuildProjectSelection,
    registry: ProvisioningStrategyRegistry | None = None,
) -> ProvisioningPlan:
    """The plan for a session whose panel has already been resolved.

    Takes the `BuildProjectSelection` the connection lifecycle already made,
    so the panel chain is resolved once per connection and this adds no
    lookup of its own. A selection with no package (no board, unidentified,
    unregistered, no courseware, a broken package) yields the same no-op
    plan an ordinary registered-but-unprovisioned panel gets.

    `registry` is injectable for the reason every table in this codebase is
    — a test registers its own strategy without touching the process-wide
    one.
    """
    table = registry if registry is not None else default_provisioning_registry
    package = selection.package
    if package is None:
        return ProvisioningPlan(
            strategy=table.create(None),
            source=f"no-package:{selection.panel_status.value}",
        )
    return ProvisioningPlan(
        strategy=table.create(package.scenario_id),
        source=f"package:{package.panel_id}",
    )
