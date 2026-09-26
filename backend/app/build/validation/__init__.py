"""Build Mode remediation validation — the generic contract (Phase B7).

    app/build/service.py          orchestrates (gates, status, events, evidence)
    app/build/validation/         states WHAT a validation is and WHO performs it
    app/build_validation_selection.py   composes a panel package into a plan

Modules, one idea each:

    models.py         `ValidationOutcome` / `ValidationResult` /
                      `RemediationSpec` / `ValidationContext` — passive,
                      serialisable, stdlib-only.
    criteria.py       (B8) `AuthorizationCriterion` and friends — a
                      remediation requirement stated as DATA a check can run
                      against, projected from the panel package's own
                      declaration.
    strategy.py       the `ValidationStrategy` contract, the `ValidationPlan`
                      a session carries, and `DeclaredRequirementValidator` —
                      the honest default, which runs nothing and says so.
    mqtt_evidence.py  (B8) the evidence channel a check OBSERVES a device
                      through. Real paho-mqtt; injectable, so the decision
                      logic is testable without a broker or a board.
    smart_home.py     (B8) `SmartHomeAuthorizationValidator` — Panel 1's real
                      per-command authorization check.
    registry.py       `scenario_id -> validator`, one explicit table.

WHAT B7 ESTABLISHED. The lifecycle is real: a validation is requested, gated
behind a successful flash of the current workspace, run through an injected
strategy, and recorded as a `BuildAttemptRecord` that `app/metrics/ttr.py`,
`aid.py` and `dei.py` already know how to read. What B7 could not supply was
any panel's actual security check, because no package declared a
machine-checkable criterion.

WHAT B8 ADDED. Panel 1 declares one, and `SmartHomeAuthorizationValidator`
runs it against the real training broker and the real board. The other four
panels still declare prose only and still resolve to the default validator,
which refuses and quotes the requirement. Nothing in this package infers a
verdict from a compile, a flash, or the prose of a requirement, and nothing
here simulates a device: an unreachable broker or an unprovisioned lab
credential is reported, never passed.

DELIBERATELY NOT RE-EXPORTED FROM `app/build/__init__.py`. Callers import
`app.build.validation` by its own path, the same convention
`app/build/recorder.py` follows: importing the `app.build` package must stay
as cheap and as free of onward dependencies as it is today.
"""

from __future__ import annotations

from app.build.validation.criteria import (
    AuthorizationCriterion,
    AuthorizationProbe,
    CommandAuthorization,
    EvidenceChannel,
    LabIdentity,
    TokenUse,
)
from app.build.validation.models import (
    RemediationSpec,
    ValidationContext,
    ValidationOutcome,
    ValidationResult,
)
from app.build.validation.mqtt_evidence import (
    AuthorizationEvidence,
    EvidenceChannelError,
)
from app.build.validation.registry import (
    BUILT_IN_VALIDATORS,
    SMART_HOME_MQTT_CONTROL,
    ValidationStrategyFactory,
    ValidationStrategyRegistry,
    build_default_validation_registry,
    default_validation_registry,
)
from app.build.validation.smart_home import SmartHomeAuthorizationValidator
from app.build.validation.strategy import (
    DeclaredRequirementValidator,
    ValidationPlan,
    ValidationStrategy,
    default_validation_plan,
)

__all__ = [
    "BUILT_IN_VALIDATORS",
    "SMART_HOME_MQTT_CONTROL",
    "AuthorizationCriterion",
    "AuthorizationEvidence",
    "AuthorizationProbe",
    "CommandAuthorization",
    "DeclaredRequirementValidator",
    "EvidenceChannel",
    "EvidenceChannelError",
    "LabIdentity",
    "RemediationSpec",
    "SmartHomeAuthorizationValidator",
    "TokenUse",
    "ValidationContext",
    "ValidationOutcome",
    "ValidationPlan",
    "ValidationResult",
    "ValidationStrategy",
    "ValidationStrategyFactory",
    "ValidationStrategyRegistry",
    "build_default_validation_registry",
    "default_validation_plan",
    "default_validation_registry",
]
