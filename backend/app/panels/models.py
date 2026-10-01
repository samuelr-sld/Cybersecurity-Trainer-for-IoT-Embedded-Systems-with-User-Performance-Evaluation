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

#: A discovered C++ section id, as `app/build/discovery/` mints them
#: (`setup`, `loop`, `callback_onMessage`, `helper_applyCommand`,
#: `global_3`). Restated here rather than imported because `app/panels/` may
#: not import `app/build/` (asserted by `tests/test_panel_packages.py`); the
#: test suite is what proves a declared id names a section the analyzer
#: actually produces for this panel's firmware, exactly as it proves a
#: `WorkflowStep.command` names a real command.
_SECTION_ID_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"

#: Environment variable names a remediation criterion may point at for a
#: provisioned lab secret. The prefix is load-bearing: a manifest names
#: WHERE a lab fixture lives, never what it is, and constraining the shape
#: means a package cannot address an arbitrary variable of the process it
#: runs in. See `app/config.py::lab_secret`, the one reader.
_LAB_SECRET_ENV_PATTERN = r"TRAINER_LAB_[A-Z][A-Z0-9_]*"

#: One command word a device accepts on a control topic (`START`, `STOP`).
#: Uppercase and unbroken, because this panel's own firmware uppercases and
#: trims what it receives before comparing.
_COMMAND_WORD_PATTERN = r"[A-Z][A-Z0-9_]*"

#: An MQTT topic filter a criterion may name. Deliberately excludes the
#: wildcards `+` and `#`: a criterion states the ONE topic a command is
#: published to and the ONE topic evidence is read from, never a subscription
#: pattern that could fan out across a broker.
_TOPIC_PATTERN = r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*"


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


class WorkflowPhase(str, Enum):
    """Which side of the reconnaissance/exploitation boundary a step is on.

    Phase 2E.2 seam. Reconnaissance Efficiency (RE) is scoped to "commands
    issued during the reconnaissance phase" (the final manuscript, Section
    3.10.1), and nothing already in the package/workflow schema draws that
    line: `CommandCategory` (app/commands/base.py) groups tools by KIND
    (firmware/network/mqtt/...) and puts `mosquitto_sub` and `mosquitto_pub`
    in the same MQTT bucket, so it cannot separate "observe" from "publish".
    Workflow-step ORDER hints at it but is not a declared fact a metric
    should infer by position — a later step could still be reconnaissance.

    So the package declares it explicitly, per step, the same way it already
    declares `required_events` on a `SuccessCondition`: data an evaluator
    reads, not a rule an engine hardcodes. `RECONNAISSANCE` is the default —
    the common case for a workflow step — so only the step(s) that actually
    constitute the exploit/attack action need to say otherwise.
    """

    RECONNAISSANCE = "reconnaissance"
    EXPLOITATION = "exploitation"


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

    `phase` is the Phase 2E.2 RE seam described on `WorkflowPhase`.
    """

    step_id: str
    title: str
    command: str | None = None
    description: str = ""
    phase: WorkflowPhase = WorkflowPhase.RECONNAISSANCE

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
        if not isinstance(self.phase, WorkflowPhase):
            raise ValueError(f"workflow step {self.step_id!r} phase must be a WorkflowPhase")


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
class ObjectiveDeclaration:
    """One measurable ACTIVITY objective for the guided Hack Mode exercise,
    used to compute Attack Completion Rate.

    Phase 2E.2 seam, corrected after an initial modelling mistake (see
    below). ACR's formula (final manuscript, Section 3.10.1) is "Objectives
    Completed in Session / Total Objectives for Module", where a completion
    is "the count of distinct objective-completion events logged during the
    session". Nothing already in the package established that:
    `LearningContent.objectives` (that class's own docstring) is
    human-readable, often CONCEPTUAL prose ("explain why X") aimed at a
    student, with no identifier and no link to the recorded event log; and
    `SuccessCondition` is a DIFFERENT concept again — an evaluator's overall
    pass/fail conjunction, not a per-objective breakdown. Conflating either
    with ACR's objectives is exactly the "two sources of truth" mistake the
    package architecture avoids elsewhere, so ACR needs its own declaration,
    deliberately separate from both.

    `evaluation.objectives` ARE NOT `learning.objectives`. The former are
    the guided activity's own MEASURABLE milestones — "extract the
    firmware", "observe the MQTT communication" — each naturally backed by
    one or more scenario events; the latter are broader academic learning
    outcomes a package may declare purely for display, which can legitimately
    include something no terminal command could ever produce evidence for.
    A correctly authored package's `evaluation.objectives` should therefore
    all be completable from recorded activity — see
    `backend/panels/smart-home-mqtt-control/panel.json` for the corrected
    five, none of which duplicate the academic phrasing of
    `learning.objectives` verbatim.

    `objective_id` is the identity ACR counts distinct completions against —
    "objective_1 completed twice" must not inflate the count, which requires
    an id to de-duplicate against in the first place. `required_events`
    mirrors `SuccessCondition.required_events` exactly (the scenario's own
    canonical event vocabulary): an objective is complete once every one of
    its required events has been recorded for the session.

    `required_events == ()` REMAINS SUPPORTED AS A GENERIC FALLBACK, not
    because any shipped package should use it. If a future package's
    objective genuinely has no technical detection method, declaring it this
    way is honest: `app/metrics/acr.py::compute_acr` counts it in "Total
    Objectives for Module" (the module really does have that many declared
    objectives) but never marks it complete on its own — surfaced plainly as
    a capped ceiling rather than a guessed completion or a silently shrunk
    denominator.
    """

    objective_id: str
    description: str
    required_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.objective_id, "objective id")
        _require_text(self.description, f"objective {self.objective_id!r} description")
        for event in self.required_events:
            if not isinstance(event, str) or not re.fullmatch(_EVENT_NAME_PATTERN, event):
                raise ValueError(
                    f"objective {self.objective_id!r} names an invalid event: {event!r}"
                )


@dataclass(frozen=True)
class EvaluationDeclaration:
    """WHICH events and metrics an evaluator should care about.

    A declaration, not an evaluator. No score, weight, threshold or formula
    appears here or anywhere else in the backend.
    """

    success_conditions: tuple[SuccessCondition, ...] = ()
    objectives: tuple[ObjectiveDeclaration, ...] = ()
    metrics: tuple[EvaluationMetric, ...] = ()

    def __post_init__(self) -> None:
        for condition in self.success_conditions:
            if not isinstance(condition, SuccessCondition):
                raise ValueError(f"not a SuccessCondition: {condition!r}")
        seen_objectives: set[str] = set()
        for objective in self.objectives:
            if not isinstance(objective, ObjectiveDeclaration):
                raise ValueError(f"not an ObjectiveDeclaration: {objective!r}")
            if objective.objective_id in seen_objectives:
                raise ValueError(f"duplicate objective id: {objective.objective_id!r}")
            seen_objectives.add(objective.objective_id)
        seen: set[EvaluationMetric] = set()
        for metric in self.metrics:
            if not isinstance(metric, EvaluationMetric):
                raise ValueError(f"not an EvaluationMetric: {metric!r}")
            if metric in seen:
                raise ValueError(f"duplicate metric declared: {metric.value}")
            seen.add(metric)


class EvidenceChannel(str, Enum):
    """Where a validator OBSERVES what the remediated device actually did.

    Phase B8. A closed vocabulary, because "how do we know?" is the question
    a validation result stands or falls on, and free text would let a package
    name an observation nobody implemented. Each member is a channel some
    validator strategy knows how to read; a package declaring one the
    resolved strategy cannot use gets an honest UNAVAILABLE, never a guess.

    MQTT_STATE_TOPIC  the device's own retained state publication — the
                      channel Panel 1 uses. It is the device reporting its
                      physical actuator state, which is exactly the evidence
                      "was the command obeyed?" needs, and it is observable
                      from the same authenticated broker session the probe
                      publishes through.
    DEVICE_SERIAL     the board's USB serial output. Declared because it is
                      the other channel this trainer already has a transport
                      for (`app/hardware/serial_transport.py`); no shipped
                      package uses it, and no strategy reads it yet.
    """

    MQTT_STATE_TOPIC = "mqtt_state_topic"
    DEVICE_SERIAL = "device_serial"


class CommandAuthorization(str, Enum):
    """Which declared identity issues a probe's command.

    AUTHORIZED and UNAUTHORIZED are BOTH authenticated to the broker — that
    is the whole point of Panel 1's lesson, and the reason this vocabulary is
    about authorization rather than authentication. The difference is whether
    the identity is entitled to actuate, not whether it may connect.
    """

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"


class TokenUse(str, Enum):
    """What per-command authorization evidence a probe attaches, if any.

    NONE     the bare command, exactly as the VULNERABLE firmware accepts it.
             A remediated device must ignore this.
    VALID    the provisioned command-authorization token. A remediated device
             must still obey this — a fix that breaks legitimate control is
             not a fix.
    INVALID  a well-formed but wrong token. This is what separates "the
             student checks for THE token" from "the student checks for A
             suffix", and it is why the criterion carries a declared wrong
             value rather than only a right one.
    """

    NONE = "none"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class LabIdentity:
    """One synthetic lab client a validator may connect to the broker as.

    IT HOLDS NO SECRET, AND CANNOT. `username` is a synthetic training-lab
    account name (the same class of value the committed firmware already
    carries in plain sight), and the password is addressed only by the NAME
    of the environment variable a deployment provisions it into. There is no
    field here a password could be written into, which is what makes
    committing a manifest safe by construction rather than by review.

    A deployment that has not provisioned the variable makes the validator
    UNAVAILABLE — an honest refusal — rather than letting it run with a
    guessed or blank credential.
    """

    identity_id: str
    username: str
    password_env: str

    def __post_init__(self) -> None:
        _require_identifier(self.identity_id, "lab identity id")
        _require_text(self.username, f"lab identity {self.identity_id!r} username")
        if any(char.isspace() for char in self.username):
            raise ValueError(
                f"lab identity {self.identity_id!r} username must not contain whitespace"
            )
        if not isinstance(self.password_env, str) or not re.fullmatch(
            _LAB_SECRET_ENV_PATTERN, self.password_env
        ):
            raise ValueError(
                f"lab identity {self.identity_id!r} password_env must be a TRAINER_LAB_* "
                f"environment variable name, got {self.password_env!r}"
            )


@dataclass(frozen=True)
class AuthorizationProbe:
    """One command a validator sends, and what a FIXED device must do with it.

    This is the machine-checkable half of a remediation requirement: a
    sentence like "reject a command from a client that is not authorized"
    cannot be executed, while "publish STOP with no token as
    `unauthorized-client` and observe that the state topic still reports
    STOPPED after the settle window" can.

    `expect_accepted` and `expect_state` are stated separately on purpose.
    "The command was obeyed" and "the actuator ended up in this state" are
    different facts: a STOP that is correctly obeyed by an already-stopped
    device leaves the same observable state as a STOP that was correctly
    ignored, so a validator needs the declared state to compare against AND
    the declared acceptance to order its probes by.
    """

    probe_id: str
    description: str
    authorization: CommandAuthorization
    command: str
    token: TokenUse
    expect_accepted: bool
    expect_state: str

    def __post_init__(self) -> None:
        _require_identifier(self.probe_id, "authorization probe id")
        _require_text(self.description, f"probe {self.probe_id!r} description")
        if not isinstance(self.authorization, CommandAuthorization):
            raise ValueError(f"probe {self.probe_id!r} authorization must be a CommandAuthorization")
        if not isinstance(self.token, TokenUse):
            raise ValueError(f"probe {self.probe_id!r} token must be a TokenUse")
        if not isinstance(self.command, str) or not re.fullmatch(
            _COMMAND_WORD_PATTERN, self.command
        ):
            raise ValueError(
                f"probe {self.probe_id!r} command must be one upper-case word, got "
                f"{self.command!r}"
            )
        if not isinstance(self.expect_accepted, bool):
            raise ValueError(f"probe {self.probe_id!r} expect_accepted must be a boolean")
        if not isinstance(self.expect_state, str) or not re.fullmatch(
            _COMMAND_WORD_PATTERN, self.expect_state
        ):
            raise ValueError(
                f"probe {self.probe_id!r} expect_state must be one upper-case word, got "
                f"{self.expect_state!r}"
            )
        if self.authorization is CommandAuthorization.UNAUTHORIZED and self.expect_accepted:
            # Declaring that an unauthorized command SHOULD be obeyed would
            # be declaring the vulnerability as correct behaviour, and a
            # validator built from it would pass the broken firmware.
            raise ValueError(
                f"probe {self.probe_id!r} expects an unauthorized command to be accepted, "
                "which is the vulnerability rather than the remediation"
            )
        if self.authorization is CommandAuthorization.AUTHORIZED and (
            self.expect_accepted is not (self.token is TokenUse.VALID)
        ):
            # An authorized identity is still only entitled *per command*:
            # with no token or a wrong one it must be refused, and with the
            # provisioned token it must be obeyed. Anything else states a
            # rule the lesson does not teach.
            raise ValueError(
                f"probe {self.probe_id!r}: an authorized identity's command is accepted "
                "exactly when it carries the valid token"
            )


@dataclass(frozen=True)
class AuthorizationCriterion:
    """A per-command authorization remediation, stated so a machine can check it.

    PHASE B8 — WHY THIS EXISTS BESIDE THE PROSE. `validation_requirement`
    says what a fix must achieve, in English, for a human to read; it is not
    and cannot be a check (see `app/build/validation/models.py::
    RemediationSpec`, which documents why interpreting that prose would be
    inventing security behaviour). This declares the same requirement as
    DATA: which topic carries commands, which commands exist, which identity
    is entitled to issue them, which is not, what a fixed device must do with
    each, and where the answer is OBSERVED. A validator built from it asserts
    only what the courseware declared.

    IT IS STILL DATA, AND STILL NOT CODE. There is no expression, script,
    template, path, executable, flag or callable here; no field is evaluated,
    imported, formatted or turned into a process argument; and nothing in
    `app/panels/` acts on it. A command payload is assembled by a validator
    from `command`, `token_separator` and a provisioned token — three plain
    values — not from a format string this package carries.

    IT CARRIES NO SECRET. See `LabIdentity`. The only credential-shaped
    fields are the NAMES of `TRAINER_LAB_*` environment variables and one
    deliberately WRONG token, which is safe precisely because it is wrong.
    """

    criterion_id: str
    broker_host: str
    broker_port: int
    control_topic: str
    state_topic: str
    accepted_commands: tuple[str, ...]
    observed_states: tuple[str, ...]
    authorized: LabIdentity
    unauthorized: LabIdentity
    token_env: str
    token_separator: str
    invalid_token: str
    evidence: EvidenceChannel
    probes: tuple[AuthorizationProbe, ...]
    settle_seconds: float = 2.0
    response_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        _require_identifier(self.criterion_id, "criterion id")
        what = f"criterion {self.criterion_id!r}"
        _require_text(self.broker_host, f"{what} broker_host")
        if any(char.isspace() for char in self.broker_host):
            raise ValueError(f"{what} broker_host must not contain whitespace")
        if (
            not isinstance(self.broker_port, int)
            or isinstance(self.broker_port, bool)
            or not 1 <= self.broker_port <= 65535
        ):
            raise ValueError(f"{what} broker_port must be a TCP port, got {self.broker_port!r}")
        for name in ("control_topic", "state_topic"):
            topic = getattr(self, name)
            if not isinstance(topic, str) or not re.fullmatch(_TOPIC_PATTERN, topic):
                raise ValueError(
                    f"{what} {name} must be a wildcard-free MQTT topic, got {topic!r}"
                )
        if self.control_topic == self.state_topic:
            # A command channel and an evidence channel that are the same
            # topic would let a validator read back its own publication and
            # call it the device's answer.
            raise ValueError(f"{what} publishes commands to the topic it reads evidence from")
        for name in ("accepted_commands", "observed_states"):
            words = getattr(self, name)
            if not words:
                raise ValueError(f"{what} {name} must name at least one value")
            for word in words:
                if not isinstance(word, str) or not re.fullmatch(_COMMAND_WORD_PATTERN, word):
                    raise ValueError(f"{what} {name} contains an invalid value: {word!r}")
            if len(set(words)) != len(words):
                raise ValueError(f"{what} {name} repeats a value")
        for name in ("authorized", "unauthorized"):
            if not isinstance(getattr(self, name), LabIdentity):
                raise ValueError(f"{what} {name} must be a LabIdentity")
        if self.authorized.username == self.unauthorized.username:
            # Two identities that are the same account cannot demonstrate an
            # authorization boundary at all.
            raise ValueError(f"{what} authorized and unauthorized identities are the same account")
        if not isinstance(self.token_env, str) or not re.fullmatch(
            _LAB_SECRET_ENV_PATTERN, self.token_env
        ):
            raise ValueError(
                f"{what} token_env must be a TRAINER_LAB_* environment variable name, got "
                f"{self.token_env!r}"
            )
        if (
            not isinstance(self.token_separator, str)
            or not self.token_separator
            or any(char in "\r\n" for char in self.token_separator)
        ):
            raise ValueError(f"{what} token_separator must be a single-line, non-empty string")
        if (
            not isinstance(self.invalid_token, str)
            or not self.invalid_token.strip()
            or any(char.isspace() for char in self.invalid_token)
        ):
            raise ValueError(f"{what} invalid_token must be a non-empty word")
        if not isinstance(self.evidence, EvidenceChannel):
            raise ValueError(f"{what} evidence must be an EvidenceChannel")
        seen: set[str] = set()
        for probe in self.probes:
            if not isinstance(probe, AuthorizationProbe):
                raise ValueError(f"{what}: not an AuthorizationProbe: {probe!r}")
            if probe.probe_id in seen:
                raise ValueError(f"{what} declares a duplicate probe id: {probe.probe_id!r}")
            seen.add(probe.probe_id)
            if probe.command not in self.accepted_commands:
                raise ValueError(
                    f"probe {probe.probe_id!r} sends {probe.command!r}, which is not one of "
                    f"this criterion's accepted commands"
                )
            if probe.expect_state not in self.observed_states:
                raise ValueError(
                    f"probe {probe.probe_id!r} expects state {probe.expect_state!r}, which is "
                    f"not one of this criterion's observed states"
                )
        # THE MINIMUM A CRITERION MUST DISTINGUISH. Without both halves there
        # is no authorization boundary to check: one proves a fix does not
        # break legitimate control, the other proves it actually blocks a
        # forged command. A criterion missing either would let firmware that
        # obeys everything, or firmware that obeys nothing, pass.
        if not any(probe.expect_accepted for probe in self.probes):
            raise ValueError(f"{what} declares no command a remediated device must ACCEPT")
        if not any(
            probe.authorization is CommandAuthorization.UNAUTHORIZED
            and not probe.expect_accepted
            for probe in self.probes
        ):
            raise ValueError(f"{what} declares no unauthorized command a device must REJECT")
        for name in ("settle_seconds", "response_timeout_seconds"):
            seconds = getattr(self, name)
            if (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not 0 < seconds <= 60
            ):
                raise ValueError(f"{what} {name} must be a positive number of seconds under 60")

    @property
    def probe_ids(self) -> tuple[str, ...]:
        """Every probe id, in declared order — the order a validator runs them."""
        return tuple(probe.probe_id for probe in self.probes)


@dataclass(frozen=True)
class RemediationDeclaration:
    """WHAT a panel's Build Mode remediation activity is, for TTR/AID/DEI.

    Phase 2E.3 seam — the Build Mode analogue of `workflow`/`evaluation` for
    Hack Mode. It answers three questions purely as courseware TEXT, never
    as an executable check: what vulnerability the student is fixing, what
    a correct fix must achieve, and what validating that fix would need to
    confirm. This module never invents a technical validation mechanism
    (see `app/build/service.py::BuildService.record_validation_attempt`):
    whether the remediated firmware is ACTUALLY secure is decided by a real
    check that does not exist yet in this codebase, and this declaration
    does not pretend otherwise.

    THERE IS NO "WHICH EVENT MEANS SUCCESS" FIELD HERE, DELIBERATELY. Unlike
    `SuccessCondition`/`ObjectiveDeclaration`, which each cite specific Hack
    Mode `ScenarioEventType` values from an open-ended vocabulary, Build
    Mode's generic attempt vocabulary (`app/build/records.py::
    BuildAttemptType`) has exactly one validation category, full stop — a
    successful remediation is structurally "a recorded VALIDATION attempt
    with `success=True`" for every panel, the same way `attack_completed`
    is Hack Mode's one generic success signal (see `app/metrics/eac.py`).
    There is nothing for a package to select between, so nothing is
    declared here that the generic metric layer would need to read.

    `vulnerability` and `remediation_goal` restate — never contradict — the
    package's own established Hack Mode facts (`learning`/`scenario`); they
    exist here so Build Mode can show a student what they are fixing
    without importing Hack Mode's courseware fields into a Build-side view.

    PHASE B8 ADDS THE TWO THINGS A REMEDIATION ACTIVITY NEEDS BEYOND PROSE,
    and both are the PANEL's decision rather than the engine's:

    the interaction policy   `security_section_id`, `editable_section_ids`
                             and `explore_section_ids` name discovered B1
                             sections of this panel's own firmware (see
                             `_SECTION_ID_PATTERN`). B1 says what the code IS;
                             only the panel's activity can say what a student
                             may DO with each part of it, and saying it here
                             is what keeps that decision out of the generic
                             build layer and out of the frontend. The three
                             are turned into one `ProjectPolicy`
                             (`app/build/policy.py`) by
                             `app/build_project_selection.py`.

    the criterion            `criterion` is the machine-checkable statement of
                             the same requirement `validation_requirement`
                             states in English — see `AuthorizationCriterion`.
                             It is optional, and None is the honest state for
                             a panel whose remediation has been described but
                             not yet made checkable.

    STILL NO "WHICH EVENT MEANS SUCCESS" FIELD, for the reason above: Build
    Mode's success signal is structurally "a recorded VALIDATION attempt with
    success=True", and B8 does not change that. What B8 adds is the evidence
    such an attempt can now be based on.
    """

    vulnerability: str
    remediation_goal: str
    validation_requirement: str
    security_section_id: str | None = None
    editable_section_ids: tuple[str, ...] = ()
    explore_section_ids: tuple[str, ...] = ()
    criterion: AuthorizationCriterion | None = None

    def __post_init__(self) -> None:
        _require_text(self.vulnerability, "remediation vulnerability")
        _require_text(self.remediation_goal, "remediation goal")
        _require_text(self.validation_requirement, "remediation validation_requirement")
        for name in ("editable_section_ids", "explore_section_ids"):
            ids = getattr(self, name)
            for section_id in ids:
                if not isinstance(section_id, str) or not re.fullmatch(
                    _SECTION_ID_PATTERN, section_id
                ):
                    raise ValueError(
                        f"remediation {name} contains an invalid section id: {section_id!r}"
                    )
            if len(set(ids)) != len(ids):
                raise ValueError(f"remediation {name} repeats a section id")
        overlap = sorted(set(self.editable_section_ids) & set(self.explore_section_ids))
        if overlap:
            raise ValueError(
                "remediation declares section(s) as both editable and explore: "
                + ", ".join(overlap)
            )
        if self.security_section_id is not None:
            if not isinstance(self.security_section_id, str) or not re.fullmatch(
                _SECTION_ID_PATTERN, self.security_section_id
            ):
                raise ValueError(
                    f"remediation security_section_id is not a section id: "
                    f"{self.security_section_id!r}"
                )
            if self.security_section_id not in self.editable_section_ids:
                # The remediation region is by definition one the student may
                # write to. Declaring one that is not editable would describe
                # an activity nobody could complete.
                raise ValueError(
                    f"remediation security_section_id {self.security_section_id!r} is not "
                    "one of its editable_section_ids"
                )
        if self.criterion is not None and not isinstance(self.criterion, AuthorizationCriterion):
            raise ValueError("remediation criterion must be an AuthorizationCriterion")

    @property
    def checkable(self) -> bool:
        """Whether this declaration states a criterion a validator can run."""
        return self.criterion is not None

    @property
    def declares_section_policy(self) -> bool:
        """Whether this remediation names any Build Mode section policy.

        A remediation described in prose only names none, and then the panel's
        policy (if it has one) lives in `PanelPackage.build` instead. Asked in
        one place so `PanelPackage` (which forbids two policies) and
        `BuildProjectSelector` (which reads the one that exists) cannot disagree
        about what "this remediation carries a policy" means.
        """
        return bool(
            self.editable_section_ids
            or self.explore_section_ids
            or self.security_section_id is not None
        )


@dataclass(frozen=True)
class BuildDeclaration:
    """WHICH of a panel's own firmware sections Build Mode opens for editing.

    A panel's decision, stated as data, and independent of any remediation
    activity. Phase B8 put the section policy inside `RemediationDeclaration`
    because the one panel that had a policy also had a vulnerability to fix,
    and the policy was written for that activity. A panel can still need the
    policy first: a FOUNDATION panel (Panel 2, the Environmental Monitoring
    System) ships real firmware that a student can open as Blockly, edit,
    compile and flash, while defining no vulnerability and no remediation. The
    only way to say "these sections are the editable ones" through
    `RemediationDeclaration` would be to write a vulnerability, a goal and a
    validation requirement the panel does not have — invented courseware, and
    exactly what a foundation package must not carry. So the policy gets its
    own optional block, and the remediation keeps meaning remediation.

    SAME VOCABULARY, SAME RULES. The ids are B1's own discovered section ids
    (`setup`, `loop`, `helper_updateDisplay`, `global`), checked against this
    panel's firmware downstream where the document is available
    (`app/build/document_project.py` rejects an id the firmware lacks). Neither
    list names a security region, because a panel with no remediation has none:
    `security_section_id` stays a remediation field. Anything unnamed is LOCKED,
    the same conservative default every project has (`app/build/policy.py`).

    ONE SOURCE OF TRUTH PER PANEL. A package declares its section policy in
    this block or in `remediation`, never both (`PanelPackage` enforces it), so
    there are never two lists that could disagree about whether a section is
    editable. A panel that later gains a remediation moves its ids there.
    """

    editable_section_ids: tuple[str, ...] = ()
    explore_section_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("editable_section_ids", "explore_section_ids"):
            ids = getattr(self, name)
            for section_id in ids:
                if not isinstance(section_id, str) or not re.fullmatch(
                    _SECTION_ID_PATTERN, section_id
                ):
                    raise ValueError(f"build {name} contains an invalid section id: {section_id!r}")
            if len(set(ids)) != len(ids):
                raise ValueError(f"build {name} repeats a section id")
        overlap = sorted(set(self.editable_section_ids) & set(self.explore_section_ids))
        if overlap:
            raise ValueError(
                "build declares section(s) as both editable and explore: " + ", ".join(overlap)
            )
        if not self.editable_section_ids and not self.explore_section_ids:
            # A block that classifies nothing says nothing; leaving the block
            # out is the honest way to declare no policy.
            raise ValueError("build declares no editable or explore sections")


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

    `remediation` is the Phase 2E.3 Build Mode analogue: None for a panel
    whose remediation activity has not been declared yet, the same honest
    "not yet provisioned" state `firmware` uses.

    `build` is the section policy of a panel that has firmware to open in
    Build Mode but no remediation activity to declare it through — see
    `BuildDeclaration`. None for every panel that declares its policy in
    `remediation` (Panel 1) or declares none.
    """

    schema_version: int
    panel_id: str
    scenario: ScenarioDefinition
    learning: LearningContent = field(default_factory=LearningContent)
    workflow: tuple[WorkflowStep, ...] = ()
    evaluation: EvaluationDeclaration = field(default_factory=EvaluationDeclaration)
    firmware: FirmwareConfiguration | None = None
    remediation: RemediationDeclaration | None = None
    build: BuildDeclaration | None = None
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
        if self.remediation is not None and not isinstance(
            self.remediation, RemediationDeclaration
        ):
            raise ValueError("package remediation must be a RemediationDeclaration")
        if self.build is not None:
            if not isinstance(self.build, BuildDeclaration):
                raise ValueError("package build must be a BuildDeclaration")
            if self.remediation is not None and self.remediation.declares_section_policy:
                # One list per panel: two declarations of which sections are
                # editable could disagree, and the loser would silently not
                # apply. See `BuildDeclaration`.
                raise ValueError(
                    "package declares its Build Mode section policy in both `build` and "
                    "`remediation`; declare it in exactly one"
                )
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
