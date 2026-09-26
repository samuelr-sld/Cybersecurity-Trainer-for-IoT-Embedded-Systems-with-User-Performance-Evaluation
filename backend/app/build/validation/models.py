"""What a remediation validation IS, as passive data (Phase B7).

    BuildService.validate_workspace
              |
              v
    ValidationContext          <- built from the session, never from the wire
              |
              v
    ValidationStrategy.validate(context)
              |
              v
    ValidationResult           <- this module
              |
              v
    BuildService -> ValidationStatus + BuildEvent + BuildAttemptRecord

COMPILING IS NOT FLASHING AND FLASHING IS NOT VALIDATION. This module exists
because B7 needed a result type that can say "the check ran and the fix does
not work" and "no check ran at all" as two different things — neither of which
`CompileOutcome` or `FlashOutcome` can express, and neither of which may ever
be inferred from either. `arduino-cli` exiting 0 twice is evidence that source
became a binary and a binary reached a board; it is evidence of nothing about
whether the vulnerability is fixed.

FOUR OUTCOMES, AND THE FOURTH IS THE HONEST ONE. `NOT_RUN` is not a failure
mode, it is the absence of a verdict: for every panel shipped today there is
no executable check to run (see `strategy.py::DeclaredRequirementValidator`),
and saying so plainly is the whole reason this vocabulary has a member for it
rather than collapsing into a boolean.

THIS PACKAGE IS A LEAF. Nothing here imports `app.panels`, `app.hardware`,
`app.scenarios`, `app.events`, `app.build_sessions`, `app.build.service`,
`arduino-cli`, a socket or a clock — `app/build/` is panel-agnostic by
standing rule (`tests/test_build_project_materialization.py::
test_the_build_package_does_not_depend_on_the_panel_layer`), and a validator
contract that a panel could not implement would be no contract at all. The
courseware facts a validator needs arrive as plain text on `RemediationSpec`,
assembled one level up by `app/build_validation_selection.py`, exactly as
`BuildWorkspace` arrives already built rather than resolved from a MAC.

TIMING IS STAMPED BY THE ORCHESTRATOR, NOT BY THE VALIDATOR. A strategy
returns an outcome and says why; `BuildService` measures how long that took
and when it finished, because it already owns the session clock the attempt
record is written with. A strategy that fabricated its own timestamps could
disagree with the row TTR is computed from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from app.build.validation.criteria import AuthorizationCriterion


class ValidationOutcome(str, Enum):
    """The verdict of one validation attempt. A closed, generic vocabulary."""

    #: The check ran and confirmed the remediation requirement was met.
    SUCCESS = "success"
    #: The check ran and the requirement was NOT met. A real, negative
    #: verdict about the student's firmware — the outcome that must survive a
    #: successful compile and a successful flash unchanged.
    FAILURE = "failure"
    #: No check ran, so there is no verdict. Not a failure of the student's
    #: fix, and never recorded as one having succeeded.
    NOT_RUN = "not_run"
    #: A check was attempted and the check itself broke — a lost device, a
    #: transport error, an exception inside a strategy. Distinct from
    #: FAILURE because the firmware was never actually judged.
    ERROR = "error"


def _frozen(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(data or {}))


@dataclass(frozen=True)
class RemediationSpec:
    """The courseware statement of what a fix must achieve.

    The generic Build engine's view of a panel's `RemediationDeclaration`
    (`app/panels/models.py`), so nothing under `app/build/` has to import the
    panel layer. It says what the vulnerability is, what a correct fix
    achieves, and what validating that fix would have to confirm.

    THE PROSE IS STILL NOT A CHECK, AND STILL CANNOT BE TURNED INTO ONE. The
    three text fields carry no assertion, topic, address, expected value or
    pass/fail rule, and none is ever inferred from them: a validator that
    "interpreted" `validation_requirement` would be inventing security
    behaviour the courseware never declared. That prohibition is unchanged by
    B8 and is the reason the next field exists.

    PHASE B7 SHIPPED ONLY THE PROSE, AND SAID WHY. Its `strategy.py` recorded
    the finding plainly: Panel 1's requirement was "a sentence, not a
    machine-checkable criterion — there is no declared authorized/unauthorized
    credential pair, no expected broker response, no observable the backend
    could compare, and inventing one would be inventing the security model the
    courseware has not defined yet". B8 is the phase that DEFINES it, in the
    package, as data: `criterion` is that declaration
    (`app/build/validation/criteria.py`), and it is what a real validator
    reads. It is None for every panel that has not declared one — which is
    four of the five — and `DeclaredRequirementValidator` keeps answering for
    those exactly as it did.
    """

    vulnerability: str = ""
    remediation_goal: str = ""
    validation_requirement: str = ""
    #: The machine-checkable half of the same requirement, when the package
    #: declared one. Never derived from the prose above.
    criterion: AuthorizationCriterion | None = None

    def __post_init__(self) -> None:
        if self.criterion is not None and not isinstance(self.criterion, AuthorizationCriterion):
            raise ValueError(f"not an AuthorizationCriterion: {self.criterion!r}")

    @property
    def declared(self) -> bool:
        """Whether the package actually said anything about remediation."""
        return bool(
            self.vulnerability.strip()
            or self.remediation_goal.strip()
            or self.validation_requirement.strip()
            or self.criterion is not None
        )

    @property
    def checkable(self) -> bool:
        """Whether a criterion exists for a validator to run against."""
        return self.criterion is not None


@dataclass(frozen=True)
class ValidationContext:
    """Everything a validator is told about the session it is judging.

    Assembled by `BuildService` from state it already holds; every field is
    backend data. Nothing a client sent reaches this object, and there is no
    port, executable, path or argument here that a strategy could use to
    widen what the backend runs — `flashed_port` is the port the backend's
    own discovery selected for the upload that just happened, carried so a
    future serial-based check can talk to the board that was actually
    written to rather than re-guessing one.
    """

    session_id: str
    project_id: str
    scenario_id: str
    module_id: str
    board_fqbn: str
    #: The workspace fingerprint of the firmware that was flashed — the same
    #: content hash `CompiledArtifact` carries, so a check can state exactly
    #: which build it judged.
    firmware_fingerprint: str
    panel_id: str | None = None
    flashed_port: str | None = None
    remediation: RemediationSpec | None = None
    #: The connected panel's static package parameters, verbatim. Scalars
    #: only (the package schema enforces that); data for a validator to read,
    #: never something this layer executes or interpolates into a command.
    parameters: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _frozen(self.parameters))


@dataclass(frozen=True)
class ValidationResult:
    """One validation attempt's verdict, serialisable and deterministic.

    `details` is a read-only mapping of whatever the strategy wants an
    evaluator to see — measurements, observed values, the command it ran.
    It is diagnostic evidence, never parsed by anything downstream, the same
    discipline `BuildAttemptRecord.detail` follows.
    """

    outcome: ValidationOutcome
    message: str = ""
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    duration_seconds: float = 0.0
    #: When the attempt finished, stamped by `BuildService` (see the module
    #: docstring). None on a result a strategy has just returned and the
    #: orchestrator has not yet stamped.
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ValidationOutcome):
            raise ValueError(f"not a ValidationOutcome: {self.outcome!r}")
        if not isinstance(self.message, str):
            raise ValueError("validation message must be a string")
        object.__setattr__(self, "details", _frozen(self.details))

    @property
    def succeeded(self) -> bool:
        """True ONLY for a check that ran and confirmed the fix.

        The single place the codebase answers "was this a validated
        successful fix?", so no caller has to re-derive it and get it
        subtly wrong — `NOT_RUN` and `ERROR` are emphatically not success.
        """
        return self.outcome is ValidationOutcome.SUCCESS

    @property
    def ran(self) -> bool:
        """Whether a check actually produced a verdict about the firmware."""
        return self.outcome in (ValidationOutcome.SUCCESS, ValidationOutcome.FAILURE)

    @classmethod
    def success(cls, message: str = "", **details: Any) -> "ValidationResult":
        return cls(ValidationOutcome.SUCCESS, message, details)

    @classmethod
    def failure(cls, message: str = "", **details: Any) -> "ValidationResult":
        return cls(ValidationOutcome.FAILURE, message, details)

    @classmethod
    def not_run(cls, message: str = "", **details: Any) -> "ValidationResult":
        return cls(ValidationOutcome.NOT_RUN, message, details)

    @classmethod
    def error(cls, message: str = "", **details: Any) -> "ValidationResult":
        return cls(ValidationOutcome.ERROR, message, details)

    def snapshot(self) -> dict[str, Any]:
        """A JSON-serialisable view for the Build Mode `state` frame."""
        return {
            "outcome": self.outcome.value,
            "message": self.message,
            "details": dict(self.details),
            "duration_seconds": round(self.duration_seconds, 2),
            "occurred_at": None if self.occurred_at is None else self.occurred_at.isoformat(),
        }
