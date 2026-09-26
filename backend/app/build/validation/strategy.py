"""The validation contract, and the only validator B7 ships (Phase B7).

    BuildService                 orchestrates: gates, status, events, evidence
        |
        v
    ValidationStrategy           performs: the actual check, and nothing else
        |
        v
    ValidationResult

WHY THE TWO ARE SEPARATE. `BuildService` already owns the session lifecycle,
the `BuildEvent` log and the `BuildAttemptRecord` evidence every metric reads.
If it also owned the check, then the first panel to need a real one would put
panel-specific security behaviour inside the generic engine — the same mistake
`app/commands/` avoids by keeping one global toolbox and letting a `Scenario`
say what it means. So the engine here knows only "ask the strategy, record
what it said"; what "validated" means for a panel lives in a strategy, and a
future panel supplies its own without this module changing.

TWO METHODS, AND THE SECOND ONE IS THE HONEST PART. A strategy is asked
`unavailable_reason(context)` BEFORE anything starts. Returning a reason means
"there is no check to run here", and `BuildService` rejects the request
outright — no `RUNNING` status, no `validation_started` event, and above all no
`BuildAttemptRecord`, because nothing was attempted. That is what keeps AID
from counting phantom attempts and keeps TTR from ever seeing a fabricated
successful fix. Returning None means the check is real and the orchestrator may
run it.

THE STRATEGY IN THIS MODULE DOES NOT VALIDATE ANYTHING, ON PURPOSE, AND IT IS
STILL THE RIGHT ANSWER FOR MOST PANELS. When B7 wrote this, every panel
package declared its remediation as TEXT only
(`app/panels/models.py::RemediationDeclaration` — `vulnerability`,
`remediation_goal`, `validation_requirement`), so there was no declared
credential pair, no expected response, and no observable the backend could
compare. Inventing one would have been inventing the security model the
courseware had not defined.

PHASE B8 DEFINED IT FOR PANEL 1, AND ONLY FOR PANEL 1. That panel's package
now carries an `AuthorizationCriterion` — control topic, accepted commands, an
authorized and an unauthorized lab identity, the probes a fixed device must
accept and reject, and the channel the answer is observed on — and
`smart_home.py` is the strategy that runs it. The other four panels declare
prose only and still resolve here, where `DeclaredRequirementValidator`
reports exactly that and runs nothing: the truthful state of that courseware,
not a stub waiting to be forgotten about.

`DeclaredRequirementValidator` NEVER BECOMES A REAL CHECK. It has no path to
SUCCESS and gains none; a panel gets a real check by declaring a criterion and
registering a strategy, not by this one growing an interpretation of prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from app.build.validation.models import (
    RemediationSpec,
    ValidationContext,
    ValidationResult,
)


@runtime_checkable
class ValidationStrategy(Protocol):
    """What `BuildService` needs from a validator — real or a test double."""

    def unavailable_reason(self, context: ValidationContext) -> str | None:
        """Why this strategy cannot run here, or None when it can.

        Consulted before any state changes. A reason is caller-safe text
        shown to the student as the reason the request was refused.
        """
        ...

    async def validate(self, context: ValidationContext) -> ValidationResult:
        """Perform the check and report what it found. Never raises for a
        negative verdict — a fix that does not work is a `FAILURE` result,
        not an exception."""
        ...


class DeclaredRequirementValidator:
    """Reports that a panel declares its validation requirement as prose only.

    The default for every panel, because that is the state of every shipped
    package (see the module docstring). It always refuses to run, and it
    quotes the package's own `validation_requirement` when there is one so
    the refusal names what would have to be confirmed rather than being a
    bare "not implemented".

    `validate` exists only to satisfy the protocol and returns `NOT_RUN`; the
    orchestrator never reaches it, because `unavailable_reason` always
    answers first.
    """

    #: What the student is told when the package said nothing at all about
    #: remediation. Kept separate from the quoted-requirement message so the
    #: two genuinely different situations do not read the same.
    UNDECLARED = (
        "this panel declares no remediation activity, so there is nothing to validate"
    )

    def unavailable_reason(self, context: ValidationContext) -> str | None:
        remediation = context.remediation
        if remediation is None or not remediation.declared:
            return self.UNDECLARED
        requirement = remediation.validation_requirement.strip()
        if not requirement:
            return self.UNDECLARED
        return (
            "no executable validation check exists for this panel yet; its package "
            f"states the requirement as text only: {requirement}"
        )

    async def validate(self, context: ValidationContext) -> ValidationResult:
        reason = self.unavailable_reason(context)
        return ValidationResult.not_run(reason or self.UNDECLARED)


@dataclass(frozen=True)
class ValidationPlan:
    """The static half of a session's validation: who checks, and against what.

    Injected into a `BuildSession` at connect exactly as its `BuildWorkspace`
    is (`app/build_sessions.py`), and chosen one level up by
    `app/build_validation_selection.py` from the panel resolution that
    connection already performed. The session never resolves one itself, and
    `BuildService` never reaches for a global registry mid-action — which is
    what keeps two concurrent sessions on different panels from sharing a
    validator or observing each other's.

    The dynamic half — session id, fingerprint, flashed port — is the
    `ValidationContext` the service builds per attempt.
    """

    strategy: ValidationStrategy
    remediation: RemediationSpec | None = None
    parameters: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: Where this plan came from, for logging only. Never sent to a client.
    source: str = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ValidationStrategy):
            raise ValueError(f"not a ValidationStrategy: {self.strategy!r}")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def describe(self) -> str:
        return f"validator={type(self.strategy).__name__} source={self.source}"


def default_validation_plan() -> ValidationPlan:
    """The plan a session gets when nothing resolved one for it.

    A fresh `DeclaredRequirementValidator` per call, so no two sessions share
    validator state, matching `create_default_workspace()`'s guarantee.
    """
    return ValidationPlan(strategy=DeclaredRequirementValidator())
