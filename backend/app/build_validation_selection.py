"""Which validator a new Build Mode session gets (Phase B7).

THE THIRD HALF OF THE SAME BRIDGE. `app/build_panel_resolution.py` answers
"which panel is attached, and what package does it declare?";
`app/build_project_selection.py` turns that package's firmware into the
workspace a session edits. This module takes the same already-resolved
package and answers the remaining question — who checks whether the student's
remediation actually worked, and against what declared requirement:

    BuildProjectSelection.package        the panel's own PanelPackage
              |
              v
    package.remediation                  RemediationDeclaration (prose)
              |                           app/panels/models.py
              v
    RemediationSpec                      the generic engine's plain-text view
              |
              v
    ValidationStrategyRegistry.create(package.scenario_id)
              |
              v
    ValidationPlan -> BuildSession -> BuildService.validate_workspace

IT EXISTS BECAUSE THE TWO HALVES MAY NOT IMPORT EACH OTHER. `app/build/` is
panel-agnostic by standing rule (`tests/test_build_project_materialization.py`
asserts no module under it imports `app.panels`), so the validation contract
cannot name a `PanelPackage` and the package cannot name a strategy. Their
composition therefore needs a third place, exactly as `app/scenario_selection.py`
is the third place where a package meets a `Scenario`.

SELECTION IS A READ, NOT AN ACTION. Everything here is already in memory: the
package was loaded when the connection resolved its panel, and building a
plan constructs a validator object. Nothing compiles, flashes, opens a port,
probes a MAC, contacts a broker, emits an event or records a row — and above
all, choosing a validator never runs one.

NO PANEL BRANCH, AND NO PANEL-SPECIFIC VALIDATION LOGIC. There is no `if
panel_id == ...` here and none is permitted; the package names its
experiment, the registry maps that id to a validator, and both are data.

PHASE B8 FILLED THE SEAM THIS MODULE ALREADY POINTED AT. B7 could only report
that every panel's remediation was prose, so every panel resolved to
`DeclaredRequirementValidator`. Panel 1 now declares a machine-checkable
`AuthorizationCriterion` in its package, the registry has one row for that
experiment, and this module's job grew by exactly one thing: carrying the
criterion across the same declaration -> spec projection the three prose
fields already went through. The four panels that still declare prose only
resolve exactly as they did, to a validator that refuses and says why.

THE CONVERSION IS MECHANICAL, AND IT IS WHY IT LIVES HERE. `app/build/` may
not import `app.panels` and `app.panels` may not import `app.build`, so a
type both sides name has to be projected across in the one module allowed to
see both. `_criterion_for` copies field for field and interprets nothing —
every rule about what a criterion may say was enforced when the package built
one (`app/panels/models.py`), and re-deriving those rules here would create
the second source of truth this whole arrangement exists to avoid.
"""

from __future__ import annotations

import logging

from app.build.validation import (
    RemediationSpec,
    ValidationPlan,
    ValidationStrategyRegistry,
    default_validation_registry,
)
from app.build.validation.criteria import (
    AuthorizationCriterion,
    AuthorizationProbe,
    CommandAuthorization,
    EvidenceChannel,
    LabIdentity,
    TokenUse,
)
from app.build_project_selection import BuildProjectSelection
from app.panels import models as panel_models
from app.panels.models import PanelPackage

logger = logging.getLogger(__name__)


def _identity_for(declared: panel_models.LabIdentity) -> LabIdentity:
    """One declared lab identity as the engine's own. Carries no secret."""
    return LabIdentity(
        identity_id=declared.identity_id,
        username=declared.username,
        password_env=declared.password_env,
    )


def _probe_for(declared: panel_models.AuthorizationProbe) -> AuthorizationProbe:
    """One declared probe as the engine's own. Nothing is re-decided here."""
    return AuthorizationProbe(
        probe_id=declared.probe_id,
        description=declared.description,
        authorization=CommandAuthorization(declared.authorization.value),
        command=declared.command,
        token=TokenUse(declared.token.value),
        expect_accepted=declared.expect_accepted,
        expect_state=declared.expect_state,
    )


def _criterion_for(
    declared: panel_models.AuthorizationCriterion | None,
) -> AuthorizationCriterion | None:
    """The package's machine-checkable criterion, as the engine's view.

    None when the package declares none — the honest state for a panel whose
    remediation is described but not yet checkable, which is four of the
    five. The enum values are carried across BY VALUE rather than by object,
    because the two vocabularies are deliberately separate declarations of
    the same closed set; a member one side has and the other does not is a
    loud `ValueError` here rather than a silent mismatch later.
    """
    if declared is None:
        return None
    return AuthorizationCriterion(
        criterion_id=declared.criterion_id,
        broker_host=declared.broker_host,
        broker_port=declared.broker_port,
        control_topic=declared.control_topic,
        state_topic=declared.state_topic,
        accepted_commands=declared.accepted_commands,
        observed_states=declared.observed_states,
        authorized=_identity_for(declared.authorized),
        unauthorized=_identity_for(declared.unauthorized),
        token_env=declared.token_env,
        token_separator=declared.token_separator,
        invalid_token=declared.invalid_token,
        evidence=EvidenceChannel(declared.evidence.value),
        probes=tuple(_probe_for(probe) for probe in declared.probes),
        settle_seconds=declared.settle_seconds,
        response_timeout_seconds=declared.response_timeout_seconds,
    )


def remediation_spec_for(package: PanelPackage) -> RemediationSpec | None:
    """The package's remediation declaration, as the engine's plain view.

    None when the package declares no remediation activity — the same honest
    "not yet provisioned" state `PanelPackage.remediation` itself uses, kept
    rather than flattened into empty fields so a validator can tell "this
    panel has no remediation" from "this panel declared one".

    The three prose fields are copied verbatim; nothing is parsed,
    summarised, or turned into a criterion. The criterion, when there is one,
    is copied verbatim too — and it is a DECLARATION the package made, never
    something derived from the prose beside it.
    """
    declaration = package.remediation
    if declaration is None:
        return None
    return RemediationSpec(
        vulnerability=declaration.vulnerability,
        remediation_goal=declaration.remediation_goal,
        validation_requirement=declaration.validation_requirement,
        criterion=_criterion_for(declaration.criterion),
    )


def select_build_validation(
    selection: BuildProjectSelection,
    registry: ValidationStrategyRegistry | None = None,
) -> ValidationPlan:
    """The plan for a session whose panel has already been resolved.

    Takes the `BuildProjectSelection` the connection lifecycle already made,
    so the panel chain is resolved once per connection and this adds no
    lookup of its own. A selection with no package (no board, unidentified,
    unregistered, no courseware, a broken package — every fallback the
    project selector documents) yields the default plan, which is the same
    plan a registered panel with no registered validator gets: one that
    declines, with the reason.

    `registry` is injectable for the reason every table in this codebase is —
    a test registers its own validator without touching the process-wide one.
    """
    table = registry if registry is not None else default_validation_registry
    package = selection.package
    if package is None:
        return ValidationPlan(
            strategy=table.create(None),
            source=f"no-package:{selection.panel_status.value}",
        )
    plan = ValidationPlan(
        strategy=table.create(package.scenario_id),
        remediation=remediation_spec_for(package),
        parameters=package.parameters,
        source=f"package:{package.panel_id}",
    )
    logger.debug(
        "build validation selected for panel %s: %s", package.panel_id, plan.describe()
    )
    return plan
