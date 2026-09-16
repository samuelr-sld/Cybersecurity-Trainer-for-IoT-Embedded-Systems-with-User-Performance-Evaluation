"""What a training panel's experiment IS — passive, validated resources.

Position in the architecture (Phase 2D.1):

    ESP32 MAC -> PanelRegistry -> PanelDefinition -> PanelPackage -> FirmwareConfiguration
     (hardware/  (hardware/       (hardware/        (this module,    (hardware/
      identity)   panels.py)       panels.py)        loaded by        firmware.py)
                                                     loader.py)

TWO MODULES ARE CALLED "PANELS", AND THEY ANSWER DIFFERENT QUESTIONS.
`app/hardware/panels.py` answers *which* panel is plugged in — a pure,
dependency-free MAC lookup that must stay cheap enough to run behind a 10s
header poll. This package answers *what that panel's experiment is*: the
scenario it runs, what the student is meant to learn and do, which findings
and events matter to an evaluator, and which firmware belongs to it. The
first is hardware identity; the second is courseware. Keeping them apart is
what lets the header name a board without reading a single file from disk.

THE ENGINE PROVIDES THE TOOLS; THE CONNECTED PANEL PROVIDES THE EXPERIMENT.
Nothing in this package is executable. There is no handler, no command, no
state machine and no scenario logic here — a `PanelPackage` is a validated
description that a later phase *reads*. The generic Hack Engine
(`app/commands/`) keeps offering the same global toolbox to every panel; a
package never adds, removes, filters or renames a command.

CONFIGURATION IS DATA, NEVER CODE. A manifest is JSON parsed by `loader.py`
into these frozen dataclasses. No field is ever evaluated, imported,
executed, or turned into a process argument by this package, and there is
deliberately no "run this" or "do that" shape anywhere in the schema. The
only field that names something on disk is the firmware source, which is a
`FirmwareConfiguration` — the same validated, traversal-rejecting type Phase
2C.5 already defined, reused rather than re-declared.

WHAT IS DELIBERATELY *NOT* HERE, TO AVOID TWO SOURCES OF TRUTH:

    the target's network facts   The broker address, port, topic and sensor
                                 readings a student must discover are owned
                                 by the executable scenario's own
                                 `ScenarioState` (`app/scenarios/state.py`).
                                 A package declares WHICH findings a student
                                 must recover (`ExpectedFinding`), never
                                 their values — two copies of an IP address
                                 could disagree, and the one the commands
                                 actually compare against would win silently.

    the scenario's behaviour     Which command causes which transition is
                                 `Scenario` implementation logic. A package
                                 declares the expected workflow as guidance
                                 and the events that constitute success, not
                                 the rules that emit them.

    the panel's display name     Owned by `PanelDefinition`, because the
                                 hardware header must render it without
                                 loading a package. A manifest repeats only
                                 `panel_id`, and solely as an integrity check
                                 that a package is the one that was asked for.

    metric formulas              A package declares which of the seven
                                 established metrics are relevant. It carries
                                 no weights, thresholds or formulas:
                                 computation is a later Evaluation phase and
                                 does not exist anywhere in this codebase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.hardware.firmware import (
    IDENTIFIER_PATTERN,
    FirmwareConfiguration,
    FirmwareSourceKind,
)

#: The manifest schema this code understands. A manifest must declare it, so
#: a future incompatible shape is rejected loudly instead of being read with
#: the wrong meaning. Bumped only when the schema changes incompatibly.
SCHEMA_VERSION = 1

#: Domain event values are snake_case (`spoof_succeeded`), unlike the
#: hyphenated identifiers used for ids, so they get their own shape.
_EVENT_NAME_PATTERN = r"[a-z0-9]+(?:_[a-z0-9]+)*"

#: Characters that would let a bare tool name read as a command LINE. None
#: of these can reach a shell — nothing executes this field — but keeping
#: the shape unable to carry an argument vector is the same defence in depth
#: `FirmwareConfiguration` applies to flag-shaped values.
_COMMAND_LINE_CHARACTERS = "|&;<>$`\\\"'()"


class EvaluationMetric(str, Enum):
    """One of the seven established trainer metrics — declaration only.

    Four belong to Hack Mode and three to Build Mode. A closed vocabulary,
    so a manifest cannot declare a metric the project does not have.
    Nothing here computes, weights or scores any of them, and no metric
    formula exists anywhere in this backend.
    """

    # Hack Mode
    ACR = "ACR"
    RE = "RE"
    EAC = "EAC"
    TTE = "TTE"
    # Build Mode
    TTR = "TTR"
    AID = "AID"
    DEI = "DEI"


def _require_identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(IDENTIFIER_PATTERN, value):
        raise ValueError(f"{what} must be a lower-case hyphenated identifier, got {value!r}")
    return value


def _require_text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ScenarioDefinition:
    """WHICH experiment this panel runs.

    `scenario_id` is a KEY, in exactly the sense a MAC is a key: it selects
    an implementation, it does not describe one. A future scenario selector
    (`app/scenarios/__init__.py::create_default_scenario`, which already
    marks itself as the place such a branch belongs) will map this id to a
    `Scenario` class. Nothing maps it today — Phase 2D.1 integrates
    resources, not activities — so the id is a forward reference on purpose,
    and `create_default_scenario()` is untouched.
    """

    scenario_id: str
    title: str
    summary: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.scenario_id, "scenario id")
        _require_text(self.title, f"scenario {self.scenario_id!r} title")
        if not isinstance(self.summary, str):
            raise ValueError(f"scenario {self.scenario_id!r} summary must be a string")


@dataclass(frozen=True)
class ExpectedFinding:
    """One thing the student is expected to discover during the activity.

    Identity and meaning only. The VALUE — an address, a port, a topic — is
    owned by the executable scenario's state, for the reason in the module
    docstring: a second copy could disagree with the one the commands
    actually compare against.
    """

    finding_id: str
    description: str

    def __post_init__(self) -> None:
        _require_identifier(self.finding_id, "finding id")
        _require_text(self.description, f"finding {self.finding_id!r} description")


@dataclass(frozen=True)
class LearningContent:
    """WHAT the student should learn and do. Human-readable guidance."""

    objectives: tuple[str, ...] = ()
    activity_instructions: tuple[str, ...] = ()
    expected_findings: tuple[ExpectedFinding, ...] = ()

    def __post_init__(self) -> None:
        for name in ("objectives", "activity_instructions"):
            for entry in getattr(self, name):
                _require_text(entry, f"each {name} entry")
        for finding in self.expected_findings:
            if not isinstance(finding, ExpectedFinding):
                raise ValueError(f"not an ExpectedFinding: {finding!r}")


@dataclass(frozen=True)
class WorkflowStep:
    """One step of the expected student workflow.

    `command` names the REAL tool the step is performed with — one of the
    Hack Engine's existing global commands. It is guidance rendered to a
    student or an instructor, never an instruction the backend executes:
    nothing in this codebase reads this field and dispatches it, the command
    registry is unchanged and still closed, and a manifest naming a command
    that does not exist is caught by the test suite rather than reaching a
    router. None for a step performed outside the terminal.
    """

    step_id: str
    title: str
    command: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.step_id, "workflow step id")
        _require_text(self.title, f"workflow step {self.step_id!r} title")
        if self.command is not None:
            _require_text(self.command, f"workflow step {self.step_id!r} command")
            if any(char.isspace() for char in self.command) or any(
                char in _COMMAND_LINE_CHARACTERS for char in self.command
            ):
                raise ValueError(
                    f"workflow step {self.step_id!r} command must be a bare tool name, "
                    f"got {self.command!r}"
                )
        if not isinstance(self.description, str):
            raise ValueError(f"workflow step {self.step_id!r} description must be a string")


@dataclass(frozen=True)
class SuccessCondition:
    """One condition that constitutes success for the activity.

    `required_events` names domain events from the scenario engine's own
    canonical vocabulary (`ScenarioEventType`, Phase 2B). Declaring them
    here is what lets a later Evaluation phase decide success from the
    RECORDED EVENT LOG rather than by scraping terminal text — the same
    principle Phase 2B was built on. Phase 2D.1 evaluates nothing.
    """

    condition_id: str
    description: str
    required_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.condition_id, "success condition id")
        _require_text(self.description, f"success condition {self.condition_id!r} description")
        for event in self.required_events:
            if not isinstance(event, str) or not re.fullmatch(_EVENT_NAME_PATTERN, event):
                raise ValueError(
                    f"success condition {self.condition_id!r} names an invalid event: {event!r}"
                )


@dataclass(frozen=True)
class EvaluationDeclaration:
    """WHICH events and metrics an evaluator should care about.

    A declaration, not an evaluator. No score, weight, threshold or formula
    appears here or anywhere else in the backend.
    """

    success_conditions: tuple[SuccessCondition, ...] = ()
    metrics: tuple[EvaluationMetric, ...] = ()

    def __post_init__(self) -> None:
        for condition in self.success_conditions:
            if not isinstance(condition, SuccessCondition):
                raise ValueError(f"not a SuccessCondition: {condition!r}")
        seen: set[EvaluationMetric] = set()
        for metric in self.metrics:
            if not isinstance(metric, EvaluationMetric):
                raise ValueError(f"not an EvaluationMetric: {metric!r}")
            if metric in seen:
                raise ValueError(f"duplicate metric declared: {metric.value}")
            seen.add(metric)


#: What a `parameters` value may be. Scalars only: a static parameter is one
#: configured fact, and forbidding containers keeps the field from growing
#: into a nested mini-language that starts to look like code.
_SCALAR_TYPES = (str, int, float, bool)


@dataclass(frozen=True)
class PanelPackage:
    """One panel's complete experiment definition, as loaded from disk.

    Frozen and validated at construction, so an invalid manifest cannot
    produce a half-usable package — `loader.py` either returns one of these
    fully formed or raises.

    `firmware` is the SAME `FirmwareConfiguration` type Phase 2C.5 defined,
    reused rather than re-declared, which is what keeps one description of
    "which firmware, built how, for what board, talking at what line rate".
    It is None for a panel whose firmware does not exist yet — the honest
    "not yet provisioned" state, not a dangling reference.
    """

    schema_version: int
    panel_id: str
    scenario: ScenarioDefinition
    learning: LearningContent = field(default_factory=LearningContent)
    workflow: tuple[WorkflowStep, ...] = ()
    evaluation: EvaluationDeclaration = field(default_factory=EvaluationDeclaration)
    firmware: FirmwareConfiguration | None = None
    parameters: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    #: The directory this package was loaded from. Set by the loader; it is
    #: how `firmware_sketch_path` resolves a relative sketch reference. A
    #: location, never a capability: nothing here opens, compiles or flashes.
    directory: Path | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported panel package schema version {self.schema_version!r} "
                f"(this backend understands {SCHEMA_VERSION})"
            )
        _require_identifier(self.panel_id, "panel id")
        for name, expected in (
            ("scenario", ScenarioDefinition),
            ("learning", LearningContent),
            ("evaluation", EvaluationDeclaration),
        ):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"package {name} must be a {expected.__name__}")
        step_ids: set[str] = set()
        for step in self.workflow:
            if not isinstance(step, WorkflowStep):
                raise ValueError(f"not a WorkflowStep: {step!r}")
            if step.step_id in step_ids:
                raise ValueError(f"duplicate workflow step id: {step.step_id!r}")
            step_ids.add(step.step_id)
        if self.firmware is not None and not isinstance(self.firmware, FirmwareConfiguration):
            raise ValueError("package firmware must be a FirmwareConfiguration")
        for key, value in self.parameters.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"parameter name must be a non-empty string: {key!r}")
            if not isinstance(value, _SCALAR_TYPES):
                raise ValueError(f"parameter {key!r} must be a string, number or boolean")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def scenario_id(self) -> str:
        return self.scenario.scenario_id

    @property
    def title(self) -> str:
        return self.scenario.title

    @property
    def firmware_sketch_path(self) -> Path | None:
        """Where this package's sketch source lives, for a later phase.

        None unless the firmware is a `SKETCH_DIRECTORY` reference and this
        package knows its own directory. Returning a path is not an action:
        nothing in Phase 2D.1 reads, compiles, uploads or otherwise touches
        it, and `loader.py` has already verified it exists and stays inside
        this package.
        """
        if self.firmware is None or self.directory is None:
            return None
        if self.firmware.source.kind is not FirmwareSourceKind.SKETCH_DIRECTORY:
            return None
        return self.directory / self.firmware.source.reference
