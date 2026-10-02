"""Locate, validate and load a trusted panel package. Nothing else.

Position in the architecture (Phase 2D.1):

    PanelDefinition.package_id ──> PanelPackageLoader ──> PanelPackage
       (hardware/panels.py)           (this module)        (models.py)
                                            |
                                    <root>/<package-id>/panel.json

ONE ALGORITHM, NO PANEL BRANCHES. Loading is identical for every panel: take
a validated identifier, join it to the configured root, read `panel.json`,
validate it, return a `PanelPackage`. There is no `if panel_id ==` here and
no per-panel code path anywhere — a sixth panel is a new directory, not a
new branch. That is the whole point of the package architecture.

WHAT THIS DELIBERATELY CANNOT DO:

    execute configuration        A manifest is parsed with `json.load` into
                                 frozen dataclasses. Nothing is `eval`d,
                                 `exec`d, imported, or turned into process
                                 arguments. This module imports no
                                 `subprocess`, no `os.system`/`os.popen`, no
                                 shell of any kind — `tests/test_panel_packages.py`
                                 asserts that statically.

    run a command                Loading a package dispatches nothing,
                                 touches no command registry, and emits no
                                 scenario event. The Hack Engine's global
                                 toolbox is not consulted, filtered or
                                 changed.

    compile, flash or provision  Resolving a firmware reference checks that
                                 the resource EXISTS. It does not read it,
                                 build it, upload it, or open a serial port.
                                 Nothing here imports `app.build`.

    accept a path from anywhere  The only input is a `package_id` that has
                                 already passed `IDENTIFIER_PATTERN` (lower-
                                 case, hyphenated, no separators, no dots),
                                 so it cannot name a parent directory or an
                                 absolute path. It comes from backend source
                                 (`BUILT_IN_PANELS`), never from a WebSocket
                                 frame, a student, or a MAC address. The
                                 resolved directory is then re-checked to be
                                 inside the root, which also closes the
                                 symlink escape the pattern alone cannot.

STRICT BY DEFAULT. Unknown keys are rejected rather than ignored, at every
level of the manifest. A typo in a trusted resource file must fail loudly
during the test run, not silently drop a learning objective a professor
believes is being shown to students.

STATELESS ON PURPOSE. There is no cache: nothing in this phase puts package
loading on a hot path, and a cache would introduce a staleness trap while a
manifest is being authored. If a later phase polls this, memoise it there —
the packages are immutable at runtime, so that stays safe.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.hardware.firmware import (
    IDENTIFIER_PATTERN,
    BoardConfiguration,
    CompilationSettings,
    FirmwareConfiguration,
    FirmwareSource,
    FirmwareSourceKind,
    FlashSettings,
    SerialSettings,
)
from app.hardware.panels import PanelDefinition
from app.panels.models import (
    AuthorizationCriterion,
    AuthorizationProbe,
    BuildDeclaration,
    CommandAuthorization,
    EvaluationDeclaration,
    EvaluationMetric,
    EvidenceChannel,
    ExpectedFinding,
    HackDeclaration,
    HackGuideSection,
    HackHint,
    LabIdentity,
    LearningContent,
    ObjectiveDeclaration,
    PanelPackage,
    RemediationDeclaration,
    ScenarioDefinition,
    SuccessCondition,
    TokenUse,
    WorkflowPhase,
    WorkflowStep,
)

#: The manifest file every package directory must contain.
MANIFEST_NAME = "panel.json"


class PanelPackageError(Exception):
    """Base class for every way loading a package can fail."""


class PanelPackageNotFoundError(PanelPackageError):
    """No package directory or manifest for this id.

    Distinct from `PanelPackageInvalidError` on purpose: "this panel has no
    package integrated yet" is an ordinary, expected state of the trainer,
    while "this package is broken" is an authoring mistake. A caller that
    conflated them would report unfinished courseware as a corrupt install.
    """


class PanelPackageInvalidError(PanelPackageError):
    """The manifest exists but is not a valid package."""


class FirmwareResourceMissingError(PanelPackageError):
    """The manifest declares firmware whose resource is not on disk.

    Its own class because it is the one failure that is about the resource
    rather than the description: the JSON is well formed and the firmware
    configuration is valid, but the sketch it points at is absent. A later
    provisioning phase needs to tell that apart from a malformed manifest.
    """


def _as_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PanelPackageInvalidError(f"{what} must be a JSON object")
    for key in value:
        if not isinstance(key, str):
            raise PanelPackageInvalidError(f"{what} has a non-string key: {key!r}")
    return value


def _reject_unknown(
    block: Mapping[str, Any], allowed: tuple[str, ...], what: str
) -> None:
    unknown = sorted(set(block) - set(allowed))
    if unknown:
        raise PanelPackageInvalidError(f"{what} has unknown field(s): {', '.join(unknown)}")


def _require(block: Mapping[str, Any], key: str, what: str) -> Any:
    if key not in block:
        raise PanelPackageInvalidError(f"{what} is missing required field {key!r}")
    return block[key]


def _as_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise PanelPackageInvalidError(f"{what} must be a JSON array")
    return value


def _as_str_tuple(value: Any, what: str) -> tuple[str, ...]:
    entries = _as_list(value, what)
    for entry in entries:
        if not isinstance(entry, str):
            raise PanelPackageInvalidError(f"{what} must contain only strings")
    return tuple(entries)


def _scenario(block: Any) -> ScenarioDefinition:
    data = _as_mapping(block, "scenario")
    _reject_unknown(data, ("scenario_id", "title", "summary"), "scenario")
    return ScenarioDefinition(
        scenario_id=_require(data, "scenario_id", "scenario"),
        title=_require(data, "title", "scenario"),
        summary=data.get("summary", ""),
    )


def _learning(block: Any) -> LearningContent:
    data = _as_mapping(block, "learning")
    _reject_unknown(
        data, ("objectives", "activity_instructions", "expected_findings"), "learning"
    )
    findings = []
    for entry in _as_list(data.get("expected_findings", []), "learning.expected_findings"):
        finding = _as_mapping(entry, "expected finding")
        _reject_unknown(finding, ("finding_id", "description"), "expected finding")
        findings.append(
            ExpectedFinding(
                finding_id=_require(finding, "finding_id", "expected finding"),
                description=_require(finding, "description", "expected finding"),
            )
        )
    return LearningContent(
        objectives=_as_str_tuple(data.get("objectives", []), "learning.objectives"),
        activity_instructions=_as_str_tuple(
            data.get("activity_instructions", []), "learning.activity_instructions"
        ),
        expected_findings=tuple(findings),
    )


def _workflow_phase(data: Mapping[str, Any]) -> WorkflowPhase:
    """Parse `phase`, defaulting to reconnaissance — see `WorkflowPhase`."""
    raw = data.get("phase", WorkflowPhase.RECONNAISSANCE.value)
    if not isinstance(raw, str):
        raise PanelPackageInvalidError("workflow step phase must be a string")
    try:
        return WorkflowPhase(raw)
    except ValueError as error:
        raise PanelPackageInvalidError(
            f"workflow step names an unknown phase {raw!r}; expected one of "
            + ", ".join(member.value for member in WorkflowPhase)
        ) from error


def _workflow(block: Any) -> tuple[WorkflowStep, ...]:
    steps = []
    for entry in _as_list(block, "workflow"):
        data = _as_mapping(entry, "workflow step")
        _reject_unknown(
            data, ("step_id", "title", "command", "description", "phase"), "workflow step"
        )
        steps.append(
            WorkflowStep(
                step_id=_require(data, "step_id", "workflow step"),
                title=_require(data, "title", "workflow step"),
                command=data.get("command"),
                description=data.get("description", ""),
                phase=_workflow_phase(data),
            )
        )
    return tuple(steps)


def _evaluation(block: Any) -> EvaluationDeclaration:
    data = _as_mapping(block, "evaluation")
    _reject_unknown(data, ("success_conditions", "objectives", "metrics"), "evaluation")
    conditions = []
    for entry in _as_list(data.get("success_conditions", []), "evaluation.success_conditions"):
        condition = _as_mapping(entry, "success condition")
        _reject_unknown(
            condition, ("condition_id", "description", "required_events"), "success condition"
        )
        conditions.append(
            SuccessCondition(
                condition_id=_require(condition, "condition_id", "success condition"),
                description=_require(condition, "description", "success condition"),
                required_events=_as_str_tuple(
                    condition.get("required_events", []), "success condition required_events"
                ),
            )
        )
    objectives = []
    for entry in _as_list(data.get("objectives", []), "evaluation.objectives"):
        objective = _as_mapping(entry, "objective")
        _reject_unknown(
            objective, ("objective_id", "description", "required_events"), "objective"
        )
        objectives.append(
            ObjectiveDeclaration(
                objective_id=_require(objective, "objective_id", "objective"),
                description=_require(objective, "description", "objective"),
                required_events=_as_str_tuple(
                    objective.get("required_events", []), "objective required_events"
                ),
            )
        )
    metrics = []
    for name in _as_str_tuple(data.get("metrics", []), "evaluation.metrics"):
        try:
            metrics.append(EvaluationMetric(name))
        except ValueError as error:
            raise PanelPackageInvalidError(
                f"evaluation declares an unknown metric {name!r}; the established metrics are "
                + ", ".join(metric.value for metric in EvaluationMetric)
            ) from error
    return EvaluationDeclaration(
        success_conditions=tuple(conditions),
        objectives=tuple(objectives),
        metrics=tuple(metrics),
    )


def _firmware(block: Any) -> FirmwareConfiguration:
    data = _as_mapping(block, "firmware")
    _reject_unknown(
        data,
        ("firmware_id", "source", "board", "compilation", "flashing", "serial"),
        "firmware",
    )

    source_block = _as_mapping(_require(data, "source", "firmware"), "firmware.source")
    _reject_unknown(source_block, ("kind", "reference"), "firmware.source")
    kind_name = _require(source_block, "kind", "firmware.source")
    if not isinstance(kind_name, str):
        raise PanelPackageInvalidError("firmware.source.kind must be a string")
    try:
        kind = FirmwareSourceKind(kind_name)
    except ValueError as error:
        raise PanelPackageInvalidError(
            f"unknown firmware source kind {kind_name!r}; expected one of "
            + ", ".join(member.value for member in FirmwareSourceKind)
        ) from error

    board_block = _as_mapping(_require(data, "board", "firmware"), "firmware.board")
    _reject_unknown(board_block, ("fqbn",), "firmware.board")

    compilation_block = _as_mapping(data.get("compilation", {}), "firmware.compilation")
    _reject_unknown(compilation_block, ("build_properties",), "firmware.compilation")

    flashing_block = _as_mapping(data.get("flashing", {}), "firmware.flashing")
    _reject_unknown(flashing_block, ("verify",), "firmware.flashing")
    verify = flashing_block.get("verify", False)
    if not isinstance(verify, bool):
        raise PanelPackageInvalidError("firmware.flashing.verify must be a boolean")

    serial_block = _as_mapping(data.get("serial", {}), "firmware.serial")
    _reject_unknown(serial_block, ("baud_rate",), "firmware.serial")

    # `FirmwareConfiguration` and its parts do their own validation — path
    # traversal, flag-shaped build properties, malformed FQBNs, bad baud
    # rates. Those `ValueError`s are re-raised as package errors so a caller
    # sees one exception family for "this manifest is not acceptable".
    try:
        return FirmwareConfiguration(
            firmware_id=_require(data, "firmware_id", "firmware"),
            source=FirmwareSource(
                kind=kind, reference=_require(source_block, "reference", "firmware.source")
            ),
            board=BoardConfiguration(fqbn=_require(board_block, "fqbn", "firmware.board")),
            compilation=CompilationSettings(
                build_properties=_as_str_tuple(
                    compilation_block.get("build_properties", []),
                    "firmware.compilation.build_properties",
                )
            ),
            flashing=FlashSettings(verify=verify),
            serial=SerialSettings(baud_rate=serial_block.get("baud_rate")),
        )
    except ValueError as error:
        raise PanelPackageInvalidError(f"invalid firmware configuration: {error}") from error


def _enum(value: Any, enum_type: type, what: str):
    """One closed-vocabulary value, or a named error listing the alternatives.

    The same treatment `_workflow_phase` and `evaluation.metrics` already
    give their enums, factored out because B8's criterion has three.
    """
    if not isinstance(value, str):
        raise PanelPackageInvalidError(f"{what} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise PanelPackageInvalidError(
            f"{what} is {value!r}, which is not one of: "
            + ", ".join(member.value for member in enum_type)
        ) from error


def _lab_identity(block: Any, what: str) -> LabIdentity:
    data = _as_mapping(block, what)
    _reject_unknown(data, ("identity_id", "username", "password_env"), what)
    return LabIdentity(
        identity_id=_require(data, "identity_id", what),
        username=_require(data, "username", what),
        password_env=_require(data, "password_env", what),
    )


def _authorization_probe(block: Any) -> AuthorizationProbe:
    data = _as_mapping(block, "authorization probe")
    _reject_unknown(
        data,
        (
            "probe_id",
            "description",
            "authorization",
            "command",
            "token",
            "expect_accepted",
            "expect_state",
        ),
        "authorization probe",
    )
    accepted = _require(data, "expect_accepted", "authorization probe")
    if not isinstance(accepted, bool):
        raise PanelPackageInvalidError("authorization probe expect_accepted must be a boolean")
    return AuthorizationProbe(
        probe_id=_require(data, "probe_id", "authorization probe"),
        description=_require(data, "description", "authorization probe"),
        authorization=_enum(
            _require(data, "authorization", "authorization probe"),
            CommandAuthorization,
            "authorization probe authorization",
        ),
        command=_require(data, "command", "authorization probe"),
        token=_enum(
            _require(data, "token", "authorization probe"), TokenUse, "authorization probe token"
        ),
        expect_accepted=accepted,
        expect_state=_require(data, "expect_state", "authorization probe"),
    )


def _seconds(data: Mapping[str, Any], key: str, default: float, what: str) -> float:
    raw = data.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PanelPackageInvalidError(f"{what} {key} must be a number of seconds")
    return float(raw)


def _criterion(block: Any) -> AuthorizationCriterion | None:
    """Parse the optional `remediation.criterion` — see `AuthorizationCriterion`.

    Absent for a panel whose remediation is described in prose but not yet
    made machine-checkable — the honest state, and the one every panel but
    Panel 1 is in. Every field is read as plain JSON data and handed to the
    frozen model, which does all the validating; nothing here interprets,
    formats or evaluates a value.
    """
    if block is None:
        return None
    data = _as_mapping(block, "remediation.criterion")
    _reject_unknown(
        data,
        (
            "criterion_id",
            "broker_host",
            "broker_port",
            "control_topic",
            "state_topic",
            "accepted_commands",
            "observed_states",
            "authorized",
            "unauthorized",
            "token_env",
            "token_separator",
            "invalid_token",
            "evidence",
            "probes",
            "settle_seconds",
            "response_timeout_seconds",
        ),
        "remediation.criterion",
    )
    what = "remediation.criterion"
    port = _require(data, "broker_port", what)
    if isinstance(port, bool) or not isinstance(port, int):
        raise PanelPackageInvalidError(f"{what} broker_port must be an integer")
    return AuthorizationCriterion(
        criterion_id=_require(data, "criterion_id", what),
        broker_host=_require(data, "broker_host", what),
        broker_port=port,
        control_topic=_require(data, "control_topic", what),
        state_topic=_require(data, "state_topic", what),
        accepted_commands=_as_str_tuple(
            _require(data, "accepted_commands", what), f"{what}.accepted_commands"
        ),
        observed_states=_as_str_tuple(
            _require(data, "observed_states", what), f"{what}.observed_states"
        ),
        authorized=_lab_identity(_require(data, "authorized", what), f"{what}.authorized"),
        unauthorized=_lab_identity(
            _require(data, "unauthorized", what), f"{what}.unauthorized"
        ),
        token_env=_require(data, "token_env", what),
        token_separator=_require(data, "token_separator", what),
        invalid_token=_require(data, "invalid_token", what),
        evidence=_enum(_require(data, "evidence", what), EvidenceChannel, f"{what}.evidence"),
        probes=tuple(
            _authorization_probe(entry)
            for entry in _as_list(_require(data, "probes", what), f"{what}.probes")
        ),
        settle_seconds=_seconds(data, "settle_seconds", 2.0, what),
        response_timeout_seconds=_seconds(data, "response_timeout_seconds", 5.0, what),
    )


def _remediation(block: Any) -> RemediationDeclaration | None:
    """Parse the optional `remediation` block — see `RemediationDeclaration`.

    Absent entirely for a panel whose Build Mode remediation activity has
    not been declared yet (the honest "not yet provisioned" state, same as
    a package with no `firmware` block).

    PHASE B8 added four optional fields. They stay optional so the four
    panels that declare only prose keep loading exactly as they did, and so
    a package can describe a remediation before anyone has decided which
    sections it touches or how to check it.
    """
    if block is None:
        return None
    data = _as_mapping(block, "remediation")
    _reject_unknown(
        data,
        (
            "vulnerability",
            "remediation_goal",
            "validation_requirement",
            "security_section_id",
            "editable_section_ids",
            "explore_section_ids",
            "criterion",
        ),
        "remediation",
    )
    try:
        return RemediationDeclaration(
            vulnerability=_require(data, "vulnerability", "remediation"),
            remediation_goal=_require(data, "remediation_goal", "remediation"),
            validation_requirement=_require(
                data, "validation_requirement", "remediation"
            ),
            security_section_id=data.get("security_section_id"),
            editable_section_ids=_as_str_tuple(
                data.get("editable_section_ids", []), "remediation.editable_section_ids"
            ),
            explore_section_ids=_as_str_tuple(
                data.get("explore_section_ids", []), "remediation.explore_section_ids"
            ),
            criterion=_criterion(data.get("criterion")),
        )
    except ValueError as error:
        raise PanelPackageInvalidError(f"invalid remediation declaration: {error}") from error


def _build(block: Any) -> BuildDeclaration | None:
    """Parse the optional `build` block — see `BuildDeclaration`.

    Absent for every panel that declares its Build Mode section policy in
    `remediation` (Panel 1) or declares none. Present for a panel that ships
    real firmware to open as Blockly but defines no remediation to carry the
    policy (Panel 2's foundation package). Both lists are optional inside the
    block, but an empty block is refused: it would classify nothing.
    """
    if block is None:
        return None
    data = _as_mapping(block, "build")
    _reject_unknown(data, ("editable_section_ids", "explore_section_ids"), "build")
    try:
        return BuildDeclaration(
            editable_section_ids=_as_str_tuple(
                data.get("editable_section_ids", []), "build.editable_section_ids"
            ),
            explore_section_ids=_as_str_tuple(
                data.get("explore_section_ids", []), "build.explore_section_ids"
            ),
        )
    except ValueError as error:
        raise PanelPackageInvalidError(f"invalid build declaration: {error}") from error


def _hack(block: Any) -> HackDeclaration | None:
    """Parse the optional `hack` block — see `HackDeclaration`.

    Absent for a panel that declares no Hack Mode guidance. Every field is
    plain display text read as data; nothing here interprets a hint, and the
    block has no field that could name something to run.
    """
    if block is None:
        return None
    data = _as_mapping(block, "hack")
    _reject_unknown(data, ("guide", "hints"), "hack")
    sections = []
    for entry in _as_list(data.get("guide", []), "hack.guide"):
        section = _as_mapping(entry, "hack guide section")
        _reject_unknown(section, ("heading", "paragraphs"), "hack guide section")
        sections.append(
            (
                _require(section, "heading", "hack guide section"),
                _as_str_tuple(
                    _require(section, "paragraphs", "hack guide section"),
                    "hack guide section paragraphs",
                ),
            )
        )
    hints = []
    for entry in _as_list(data.get("hints", []), "hack.hints"):
        hint = _as_mapping(entry, "hack hint")
        _reject_unknown(hint, ("hint_id", "text", "objective_id"), "hack hint")
        hints.append(
            (
                _require(hint, "hint_id", "hack hint"),
                _require(hint, "text", "hack hint"),
                hint.get("objective_id"),
            )
        )
    try:
        return HackDeclaration(
            guide=tuple(HackGuideSection(heading=h, paragraphs=p) for h, p in sections),
            hints=tuple(
                HackHint(hint_id=i, text=t, objective_id=o) for i, t, o in hints
            ),
        )
    except ValueError as error:
        raise PanelPackageInvalidError(f"invalid hack declaration: {error}") from error


class PanelPackageLoader:
    """Loads panel packages from one trusted, backend-owned root.

    `root` defaults to `config.PANEL_PACKAGE_ROOT`. It is application
    configuration, never a value from a client: a frontend cannot name a
    root, a package, or a file, and no code path turns a MAC address into a
    filesystem path — a MAC resolves to a `PanelDefinition`, and only that
    definition's validated `package_id` selects a directory.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root if root is not None else config.PANEL_PACKAGE_ROOT)

    @property
    def root(self) -> Path:
        return self._root

    def available(self) -> tuple[str, ...]:
        """Package ids present under the root, sorted. Reads no manifest.

        Directories whose name is not a valid identifier are ignored rather
        than reported: scratch folders and editor droppings in a resource
        tree are not package errors.
        """
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name
                for entry in self._root.iterdir()
                if entry.is_dir()
                and re.fullmatch(IDENTIFIER_PATTERN, entry.name)
                and (entry / MANIFEST_NAME).is_file()
            )
        )

    def _directory(self, package_id: str) -> Path:
        if not isinstance(package_id, str) or not re.fullmatch(IDENTIFIER_PATTERN, package_id):
            raise PanelPackageInvalidError(
                f"package id must be a lower-case hyphenated identifier, got {package_id!r}"
            )
        root = self._root.resolve()
        directory = (root / package_id).resolve()
        # Defence in depth. The identifier pattern already forbids `.`, `/`
        # and `\`, so this can only trip on a symlink out of the root — which
        # is exactly the case the pattern cannot see.
        if root not in directory.parents:
            raise PanelPackageInvalidError(
                f"package {package_id!r} resolves outside the package root"
            )
        return directory

    def load(self, package_id: str) -> PanelPackage:
        """The package with this id. Raises rather than returning a partial.

        Every failure mode is explicit: a missing directory or manifest is
        `PanelPackageNotFoundError`, anything malformed is
        `PanelPackageInvalidError`, and declared-but-absent firmware is
        `FirmwareResourceMissingError`.
        """
        directory = self._directory(package_id)
        manifest = directory / MANIFEST_NAME
        if not manifest.is_file():
            raise PanelPackageNotFoundError(
                f"no panel package {package_id!r}: {manifest} does not exist"
            )

        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PanelPackageInvalidError(f"{manifest} is not valid JSON: {error}") from error
        except OSError as error:
            raise PanelPackageInvalidError(f"{manifest} could not be read: {error}") from error

        data = _as_mapping(raw, f"package {package_id!r}")
        _reject_unknown(
            data,
            (
                "schema_version",
                "panel_id",
                "scenario",
                "learning",
                "workflow",
                "evaluation",
                "firmware",
                "remediation",
                "build",
                "hack",
                "parameters",
            ),
            f"package {package_id!r}",
        )

        firmware_block = data.get("firmware")
        firmware = None if firmware_block is None else _firmware(firmware_block)

        try:
            package = PanelPackage(
                schema_version=_require(data, "schema_version", f"package {package_id!r}"),
                panel_id=_require(data, "panel_id", f"package {package_id!r}"),
                scenario=_scenario(_require(data, "scenario", f"package {package_id!r}")),
                learning=_learning(data.get("learning", {})),
                workflow=_workflow(data.get("workflow", [])),
                evaluation=_evaluation(data.get("evaluation", {})),
                firmware=firmware,
                remediation=_remediation(data.get("remediation")),
                build=_build(data.get("build")),
                hack=_hack(data.get("hack")),
                parameters=_as_mapping(data.get("parameters", {}), "parameters"),
                directory=directory,
            )
        except ValueError as error:
            raise PanelPackageInvalidError(f"{manifest} is not a valid package: {error}") from error

        self._verify_firmware_resource(package)
        return package

    def load_for_panel(self, panel: PanelDefinition) -> PanelPackage:
        """The package the given panel definition references.

        The registry stays the only thing that maps a MAC to a panel; this
        only follows the `package_id` that panel already declares. The
        manifest's own `panel_id` is cross-checked against it — not as a
        second source of truth for identity, but so a package copied into
        the wrong directory is caught instead of silently teaching the wrong
        experiment.
        """
        if panel.package_id is None:
            raise PanelPackageNotFoundError(
                f"panel {panel.panel_id!r} references no package"
            )
        package = self.load(panel.package_id)
        if package.panel_id != panel.panel_id:
            raise PanelPackageInvalidError(
                f"package {panel.package_id!r} declares panel_id {package.panel_id!r}, "
                f"but it is referenced by panel {panel.panel_id!r}"
            )
        return package

    def _verify_firmware_resource(self, package: PanelPackage) -> None:
        """Check that declared firmware exists. Never reads or runs it.

        Only a `SKETCH_DIRECTORY` names something in this package's own
        tree, so only that kind is checkable here. A `BUILD_PROJECT`
        reference names a project defined in backend source
        (`app/build/`); resolving it would mean importing the build layer
        into the loader, which is precisely the dependency this phase must
        not create — the test suite guards those references instead, the
        same way Phase 2C.5 guarded them.
        """
        sketch = package.firmware_sketch_path
        if sketch is None:
            return
        directory = package.directory.resolve()
        resolved = sketch.resolve()
        if directory not in resolved.parents:
            raise PanelPackageInvalidError(
                f"package {package.panel_id!r} firmware source resolves outside the package"
            )
        if not resolved.is_dir():
            raise FirmwareResourceMissingError(
                f"package {package.panel_id!r} declares firmware source "
                f"{package.firmware.source.reference!r}, but {resolved} is not a directory"
            )
        if not any(resolved.glob("*.ino")):
            raise FirmwareResourceMissingError(
                f"package {package.panel_id!r} firmware source {resolved} contains no .ino sketch"
            )


def default_panel_package_loader() -> PanelPackageLoader:
    """A loader over the configured resource root.

    Built at call time rather than at import, for the same reason
    `default_panel_registry()` is: a changed `config.PANEL_PACKAGE_ROOT` is
    honoured without reloading the import graph, and a test can point the
    whole architecture at a temporary root without touching this module.
    Construction performs no I/O — it only remembers a path.
    """
    return PanelPackageLoader()
