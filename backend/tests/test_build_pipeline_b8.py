"""Phase B8 verification: Panel 1's remediation contract, end to end.

B8 answers four questions that B2 and B7 deliberately left open, and this
file is about each of them being answered by DATA the panel declares rather
than by code that recognises a panel:

    which sections may a student touch?    the interaction policy
    which one is the remediation?          `security_region_id`
    what counts as fixed?                  the machine-readable criterion
    how do we know?                        the evidence channel

WHAT IS FAKED, AND WHAT THAT COSTS. The evidence channel is faked, because
the real one is an authenticated MQTT session against the training Raspberry
Pi's Mosquitto broker and a physically wired ESP32 driving a motor — neither
of which exists in CI. What is NOT faked is the contract: `FakePanel`
implements the same `AuthorizationEvidence` protocol `MqttStateTopicEvidence`
does, it is driven through the real `SmartHomeAuthorizationValidator` reading
the real shipped `panel.json`, and the firmware behaviours it models are the
ones the real committed sketch has (`vulnerable_firmware` obeys exactly the
bare command words the real `applyCommand` compares against).

NOTHING HERE CLAIMS HARDWARE PASSED. A fake device proves the validator
reaches the right verdict for a given device behaviour; it proves nothing
about a real board. The real-hardware path is `tests/test_build_pipeline_hil.py`
and is gated on `TRAINER_HIL_ALLOW_FLASH`; the shipped validator has no way
to produce SUCCESS without a real evidence channel, which
`test_no_shipped_module_simulates_a_device` asserts statically.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import pathlib

import pytest

from app.build import (
    BuildWorkspace,
    InteractionPolicy,
    board_info_from_fqbn,
    build_project_policy,
    create_default_workspace,
    load_sketch_project,
)
from app.build.discovery import analyze_source
from app.build.events import BuildEventType
from app.build.models import BuildProject, CompileStatus, FlashStatus, RegionKind, ValidationStatus
from app.build.policy import ProjectPolicy, ProjectPolicyError
from app.build.program_source import (
    LockedRegionChangedError,
    ProgramStructureChangedError,
    regenerated,
    source_for_program,
)
from app.build.records import BuildAttemptType
from app.build.validation import (
    BUILT_IN_VALIDATORS,
    SMART_HOME_MQTT_CONTROL,
    AuthorizationEvidence,
    DeclaredRequirementValidator,
    EvidenceChannel,
    EvidenceChannelError,
    SmartHomeAuthorizationValidator,
    TokenUse,
    ValidationContext,
    ValidationOutcome,
    ValidationPlan,
    ValidationResult,
    default_validation_registry,
)
from app.build.sketch_source import SketchSourceError
from app.build.workspace import ProgramApplyError, RegionNotEditableError
from app.build_project_selection import BuildProjectSelection, BuildProjectSource
from app.build_validation_selection import remediation_spec_for, select_build_validation
from app.metrics import MetricStatus, compute_aid, compute_ttr
from app.panels import default_panel_package_loader
from app.panels.models import EvidenceChannel as PanelEvidenceChannel
from app.panels.models import TokenUse as PanelTokenUse
from app.panels.service import PanelResourceStatus

from tests.test_build_pipeline_b7 import (
    FakeCompilerAdapter,
    FakeFlasherAdapter,
    compiled_session,
)
from tests.test_build_project_materialization import (
    panel_one_identification,
    selector_over,
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = BACKEND / "panels" / PANEL_ONE / "firmware" / "smart_home_mqtt_control"
PANEL_MANIFEST = BACKEND / "panels" / PANEL_ONE / "panel.json"
SKETCH_NAME = "smart_home_mqtt_control.ino"

#: The provisioned lab fixtures a deployment sets. Synthetic throughout — the
#: passwords are obviously fake and the token is the one the courseware's own
#: `validation_requirement` quotes as an example.
FIXTURES = {
    "TRAINER_LAB_PANEL1_VALIDATOR_PASSWORD": "lab-validator-not-a-real-password",
    "TRAINER_LAB_PANEL1_GUEST_PASSWORD": "lab-guest-not-a-real-password",
    "TRAINER_LAB_PANEL1_COMMAND_TOKEN": "PANEL1-CMD-AUTH-K7",
}


def run(coro):
    return asyncio.run(coro)


def package():
    return default_panel_package_loader().load(PANEL_ONE)


def declaration():
    return package().remediation


# =============================================================================
# Doubles: a panel whose firmware behaves a declared way
# =============================================================================


def vulnerable_firmware(payload: str, token: str, separator: str) -> str | None:
    """The COMMITTED sketch: `applyCommand` compares the whole message.

    `message == "START"` obeys a bare START and nothing else — so a payload
    carrying any token, right or wrong, is ignored, and a bare command from
    any authenticated client is obeyed. That asymmetry is the vulnerability,
    and modelling it faithfully is what makes the failure test meaningful.
    """
    return payload if payload in ("START", "STOP") else None


def remediated_firmware(payload: str, token: str, separator: str) -> str | None:
    """A correct fix: obey only a command carrying the provisioned token."""
    for command in ("START", "STOP"):
        if payload == f"{command}{separator}{token}":
            return command
    return None


def deaf_firmware(payload: str, token: str, separator: str) -> str | None:
    """A student who "fixed" it by ignoring MQTT entirely. Not a fix."""
    return None


def token_shaped_firmware(payload: str, token: str, separator: str) -> str | None:
    """Checks that SOMETHING follows the command, not that it is the token.

    The mistake the `forged-start-wrong-token` probe exists to catch.
    """
    parts = payload.split(separator, 1)
    if len(parts) == 2 and parts[1] and parts[0] in ("START", "STOP"):
        return parts[0]
    return None


def stop_forgeable_firmware(payload: str, token: str, separator: str) -> str | None:
    """Checks the token for START but obeys ANY stop, forged or not.

    Passes `baseline-stop` through `authorized-start` and fails exactly at
    `forged-stop-running` — a later probe than the committed vulnerability's
    `forged-start-no-token`, used to prove the cleanup restoration triggers
    on an early failure regardless of WHICH probe caught it.
    """
    if payload.startswith("STOP"):
        return "STOP"
    if payload == f"START{separator}{token}":
        return "START"
    return None


class FakePanel:
    """A device on the far end of the evidence channel. No broker, no board.

    Implements the same `AuthorizationEvidence` protocol the real MQTT
    channel does, so the validator under test is the production one running
    its production code path.
    """

    def __init__(
        self,
        criterion,
        secrets,
        *,
        firmware=remediated_firmware,
        state: str = "STOPPED",
        reports_state: bool = True,
    ) -> None:
        self.criterion = criterion
        self.secrets = dict(secrets)
        self._firmware = firmware
        self._state = state
        self._reports = reports_state
        self.published: list[tuple[str, str, str]] = []
        self.settled: list[float] = []
        self.closed = False

    # --- the protocol ---
    def state(self) -> str | None:
        return self._state if self._reports else None

    def await_state(self, expected: str, timeout: float) -> str | None:
        return self.state()

    def await_any_state(self, timeout: float) -> str | None:
        return self.state()

    def publish(self, identity_id: str, topic: str, payload: str) -> None:
        self.published.append((identity_id, topic, payload))
        obeyed = self._firmware(
            payload,
            self.secrets[self.criterion.token_env],
            self.criterion.token_separator,
        )
        if obeyed == "START":
            self._state = "RUNNING"
        elif obeyed == "STOP":
            self._state = "STOPPED"

    def settle(self, seconds: float) -> None:
        self.settled.append(seconds)

    def close(self) -> None:
        self.closed = True


def panel_factory(**kwargs):
    """An `EvidenceFactory` that builds a `FakePanel`, and remembers it."""
    built: list[FakePanel] = []

    def factory(criterion, secrets):
        panel = FakePanel(criterion, secrets, **kwargs)
        built.append(panel)
        return panel

    factory.built = built  # type: ignore[attr-defined]
    return factory


def broken_factory(message: str = "connection refused by the broker"):
    def factory(criterion, secrets):
        raise EvidenceChannelError(message)

    return factory


def validator(*, factory=None, fixtures=None) -> SmartHomeAuthorizationValidator:
    provisioned = FIXTURES if fixtures is None else fixtures
    return SmartHomeAuthorizationValidator(
        evidence_factory=factory if factory is not None else panel_factory(),
        secret_reader=lambda name: provisioned.get(name, ""),
    )


def context_for(spec=None) -> ValidationContext:
    return ValidationContext(
        session_id="b8",
        project_id="smart-home-mqtt-control-firmware",
        scenario_id=PANEL_ONE,
        module_id=PANEL_ONE,
        board_fqbn="esp32:esp32:esp32",
        firmware_fingerprint="fingerprint-under-test",
        panel_id=PANEL_ONE,
        remediation=spec if spec is not None else remediation_spec_for(package()),
    )


def check(**kwargs) -> ValidationResult:
    """Run the real validator against a fake panel and return its verdict."""
    return run(validator(**kwargs).validate(context_for()))


# =============================================================================
# Workspace helpers
# =============================================================================

#: A correct remediation, authored as text in the one editable region.
#:
#: It begins with the newline the discovered section begins with: a region's
#: source includes the whitespace that separates it from its neighbours, and
#: an edit that drops it would splice `static void applyCommand(...)` onto
#: the end of the preceding `//` comment. See
#: `test_an_edit_that_breaks_the_files_structure_is_refused_not_applied`.
REMEDIATED_REGION = '''
static void applyCommand(const String &message) {
  // PER-COMMAND AUTHORIZATION. Broker authentication only proved this client
  // may connect; it never proved it may actuate the motor. A command is
  // obeyed only when it carries this panel's provisioned token.
  static const String AUTH_TOKEN = "PANEL1-CMD-AUTH-K7";
  int split = message.indexOf(' ');
  if (split < 0) {
    return;
  }
  if (message.substring(split + 1) != AUTH_TOKEN) {
    return;
  }
  String command = message.substring(0, split);
  if (command == "START") {
    motorStart();
  } else if (command == "STOP") {
    motorStop();
  }
}'''


def panel_one_project(package_=None) -> BuildProject:
    """Panel 1's real firmware, materialized under its own declared policy."""
    declared = (package_ or package()).remediation
    return load_sketch_project(
        PANEL_SKETCH,
        project_id="smart-home-mqtt-control-firmware",
        scenario_id=PANEL_ONE,
        module_id=PANEL_ONE,
        firmware_name="Smart Home MQTT Control System",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=declared.editable_section_ids,
        explore_section_ids=declared.explore_section_ids,
        security_region_id=declared.security_section_id,
    )


def panel_one_workspace() -> BuildWorkspace:
    return BuildWorkspace(panel_one_project())


def remediated_workspace() -> BuildWorkspace:
    workspace = panel_one_workspace()
    workspace.update_region(SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION)
    return workspace


def selection_for(package_=None) -> BuildProjectSelection:
    return BuildProjectSelection(
        workspace=create_default_workspace(),
        source=BuildProjectSource.PANEL_PACKAGE,
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE,
        package=package_ or package(),
    )


# =============================================================================
# 1-2. The Panel 1 declaration, and its machine-readable criterion
# =============================================================================


def test_panel_one_declares_a_remediation_activity() -> None:
    declared = declaration()
    assert declared is not None
    assert declared.vulnerability
    assert declared.remediation_goal
    assert declared.validation_requirement
    assert declared.checkable is True


def test_the_criterion_states_everything_a_validator_needs() -> None:
    """Every fact §6 requires, present as data rather than as prose."""
    criterion = declaration().criterion
    assert criterion.control_topic == "cybertrainer/smart-home/motor/control"
    assert criterion.state_topic == "cybertrainer/smart-home/motor/state"
    assert criterion.broker_host == "192.168.50.1"
    assert criterion.broker_port == 1883
    assert set(criterion.accepted_commands) == {"START", "STOP"}
    assert set(criterion.observed_states) == {"RUNNING", "STOPPED"}
    assert criterion.evidence is PanelEvidenceChannel.MQTT_STATE_TOPIC
    # An authorized identity and an unauthorized one, both authenticated.
    assert criterion.authorized.username != criterion.unauthorized.username
    assert criterion.probes


def test_the_criterion_distinguishes_an_accepted_from_a_rejected_command() -> None:
    probes = declaration().criterion.probes
    accepted = [p for p in probes if p.expect_accepted]
    rejected = [p for p in probes if not p.expect_accepted]
    assert accepted, "no command a remediated device must obey"
    assert rejected, "no command a remediated device must ignore"
    # Rejection is proved from BOTH resting states, so firmware that happens
    # to sit in the expected state cannot pass by accident.
    assert {p.expect_state for p in rejected} == {"STOPPED", "RUNNING"}
    # And a wrong token is exercised, not only a missing one.
    assert {p.token for p in rejected} >= {PanelTokenUse.NONE, PanelTokenUse.INVALID}


def test_the_criterion_survives_the_projection_into_the_engines_view() -> None:
    declared = declaration().criterion
    spec = remediation_spec_for(package())
    assert spec.checkable is True
    carried = spec.criterion
    assert carried.criterion_id == declared.criterion_id
    assert carried.control_topic == declared.control_topic
    assert carried.evidence.value == declared.evidence.value
    assert [p.probe_id for p in carried.probes] == list(declared.probe_ids)
    assert carried.authorized.password_env == declared.authorized.password_env


def test_a_criterion_may_not_declare_an_unauthorized_command_acceptable() -> None:
    """The one rule that would invert the whole lesson if it were allowed."""
    from app.panels.models import AuthorizationProbe, CommandAuthorization

    with pytest.raises(ValueError, match="vulnerability rather than the remediation"):
        AuthorizationProbe(
            probe_id="inverted",
            description="x",
            authorization=CommandAuthorization.UNAUTHORIZED,
            command="START",
            token=PanelTokenUse.NONE,
            expect_accepted=True,
            expect_state="RUNNING",
        )


def test_a_criterion_must_distinguish_acceptance_from_rejection() -> None:
    from app.panels.models import AuthorizationCriterion

    criterion = declaration().criterion
    rejections_only = [p for p in criterion.probes if not p.expect_accepted]
    with pytest.raises(ValueError, match="must ACCEPT"):
        dataclasses.replace(criterion, probes=tuple(rejections_only))
    acceptances_only = [p for p in criterion.probes if p.expect_accepted]
    with pytest.raises(ValueError, match="must REJECT"):
        dataclasses.replace(criterion, probes=tuple(acceptances_only))
    assert isinstance(criterion, AuthorizationCriterion)


# =============================================================================
# 3-8. The interaction policy: security region, editable, explore, locked
# =============================================================================


def test_the_security_region_is_the_function_that_makes_the_decision() -> None:
    """Confirmed from the real source, not assumed — see the B8 report.

    `applyCommand` is the function that turns a received message into
    actuation with no authorization check; `onMessage` only routes by topic
    and normalises the payload.
    """
    assert declaration().security_section_id == "helper_applyCommand"
    source = (PANEL_SKETCH / SKETCH_NAME).read_text(encoding="utf-8")
    body = analyze_source(source).section("helper_applyCommand").text
    assert "motorStart();" in body and "motorStop();" in body
    assert panel_one_project().security_region_id == "helper_applyCommand"


#: Every section the no-device/Blockly-integration correction widened Panel
#: 1's policy to open — every HELPER_FUNCTION/CALLBACK/SETUP/LOOP section, now
#: that all of them have a container form (`functions.implementation` — see
#: `app/build/semantic/operations.py`). Section 10 of the correction is
#: explicit: the old "only the security region is editable" restriction was
#: a courseware choice, not an engine limit, and it is corrected here at the
#: courseware layer (`panel.json`), not by widening the engine further.
PANEL_ONE_EDITABLE_SECTIONS = (
    "callback_onMessage",
    "helper_applyCommand",
    "helper_applyMotorState",
    "helper_chirpBuzzer",
    "helper_ensureConnected",
    "helper_motorStart",
    "helper_motorStop",
    "helper_pollButtons",
    "helper_setMotorOutputs",
    "loop",
    "setup",
)


def test_many_sections_are_editable_not_just_the_security_region() -> None:
    project = panel_one_project()
    editable = {
        s.region_id for s in project.file(SKETCH_NAME).segments if s.kind is RegionKind.EDITABLE
    }
    assert editable == set(PANEL_ONE_EDITABLE_SECTIONS)
    assert project.policy.editable_section_ids == tuple(sorted(PANEL_ONE_EDITABLE_SECTIONS))
    # The security region is still exactly one of them — widening editability
    # never narrowed it.
    assert "helper_applyCommand" in project.policy.editable_section_ids


def test_the_explore_sections_are_the_ones_with_no_container_form() -> None:
    # CORRECTED: the five function sections `explore_section_ids` used to
    # hold (read-only "context") are now representable containers and moved
    # to EDITABLE above. Only the two GLOBAL_DECLARATIONS comment sections —
    # which no container could ever represent — remain EXPLORE.
    project = panel_one_project()
    assert set(project.policy.explore_section_ids) == {"global", "global_3"}
    for section_id in project.policy.explore_section_ids:
        assert project.section_policy(section_id) is InteractionPolicy.EXPLORE


def test_everything_else_is_locked() -> None:
    project = panel_one_project()
    classified = set(project.policy.editable_section_ids) | set(
        project.policy.explore_section_ids
    )
    sections = {s.region_id for s in project.file(SKETCH_NAME).segments}
    for region_id in sections - classified:
        assert project.section_policy(region_id) is InteractionPolicy.LOCKED
    # Only the un-narrated GLOBAL_DECLARATIONS runs remain — every function
    # section is now either EDITABLE or, for the two with relevant context,
    # EXPLORE.
    assert sections - classified == {"global_2", "global_4", "global_5"}


def test_an_explore_section_is_read_only_exactly_like_a_locked_one() -> None:
    """EXPLORE is a classification, not a third permission.

    The workspace has no EXPLORE branch and needs none: an explore section is
    a LOCKED segment, so the existing rejection covers it unchanged.
    """
    workspace = panel_one_workspace()
    project = workspace.project
    for region_id in ("global", "global_3"):
        segment = project.file(SKETCH_NAME).segment(region_id)
        assert segment.kind is RegionKind.LOCKED
        assert project.section_policy(region_id) is InteractionPolicy.EXPLORE
        # ...and readable, which is the whole point of the classification.
        assert workspace.region_source(SKETCH_NAME, region_id)
        with pytest.raises(RegionNotEditableError):
            workspace.update_region(SKETCH_NAME, region_id, "// tampered")


def test_a_locked_section_cannot_be_edited() -> None:
    workspace = panel_one_workspace()
    for region_id in ("global_2", "global_4", "global_5"):
        with pytest.raises(RegionNotEditableError):
            workspace.update_region(SKETCH_NAME, region_id, "// tampered")


def test_the_editable_section_accepts_an_edit() -> None:
    workspace = panel_one_workspace()
    workspace.update_region(SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION)
    assert "AUTH_TOKEN" in workspace.region_source(SKETCH_NAME, "helper_applyCommand")


def test_policy_resolution_is_deterministic() -> None:
    """Same declaration, same answer — every time, for every section."""
    first, second = panel_one_project(), panel_one_project()
    sections = [s.region_id for s in first.file(SKETCH_NAME).segments]
    assert [first.section_policy(s) for s in sections] == [
        second.section_policy(s) for s in sections
    ]
    assert first.policy == second.policy
    assert first.policy.snapshot() == second.policy.snapshot()


def test_policy_is_addressed_by_stable_section_id_not_by_function_name() -> None:
    """The ids are B1's, and they are what the wire carries."""
    snapshot = panel_one_workspace().snapshot()
    segments = snapshot["files"][SKETCH_NAME]["segments"]
    by_id = {s["region_id"]: s for s in segments}
    assert by_id["helper_applyCommand"]["policy"] == "editable"
    assert by_id["callback_onMessage"]["policy"] == "editable"
    assert by_id["setup"]["policy"] == "editable"
    assert by_id["global"]["policy"] == "explore"
    assert by_id["global_2"]["policy"] == "locked"
    # `kind` did not change meaning, and both travel together.
    assert by_id["callback_onMessage"]["kind"] == "editable"
    assert by_id["global_2"]["kind"] == "locked"
    assert snapshot["project"]["security_region_id"] == "helper_applyCommand"
    assert snapshot["project"]["policy"]["editable_section_ids"] == sorted(
        PANEL_ONE_EDITABLE_SECTIONS
    )


def test_a_policy_cannot_name_a_section_the_firmware_does_not_have() -> None:
    with pytest.raises(ProjectPolicyError):
        build_project_policy(("setup", "loop"), editable_section_ids=("no_such_section",))
    with pytest.raises(SketchSourceError):
        load_sketch_project(
            PANEL_SKETCH,
            project_id="p",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="n",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            editable_section_ids=("helper_thatDoesNotExist",),
        )


def test_a_section_cannot_be_both_editable_and_explore() -> None:
    with pytest.raises(ProjectPolicyError):
        build_project_policy(
            ("setup", "loop"),
            editable_section_ids=("loop",),
            explore_section_ids=("loop",),
        )


def test_a_security_region_the_policy_does_not_open_is_refused() -> None:
    """A remediation region a student cannot write to is a broken contract."""
    policy = build_project_policy(("setup", "loop"), editable_section_ids=("loop",))
    with pytest.raises(ValueError, match="security region"):
        BuildProject(
            project_id="p",
            scenario_id="s",
            module_id="m",
            firmware_name="f",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            files=panel_one_project().files,
            security_region_id="setup",
            policy=policy,
        )


def test_the_declaration_refuses_a_security_section_it_did_not_open() -> None:
    with pytest.raises(ValueError, match="not\n?\\s*one of its editable_section_ids"):
        dataclasses.replace(declaration(), editable_section_ids=("helper_pollButtons",))


# =============================================================================
# 9. Panel package integration — the selection path reads the declaration
# =============================================================================


def test_the_connection_path_materializes_panel_one_under_its_own_policy() -> None:
    """The real selector, over a fake identification — no board, no CLI.

    This is the path `/ws/build` takes at connect: resolve the panel, read
    its package, materialize its firmware. B8's only addition to it is that
    the package's declared policy comes through with everything else.
    """
    selection = selector_over(panel_one_identification()).select()
    assert selection.source is BuildProjectSource.PANEL_PACKAGE
    project = selection.workspace.project
    declared = declaration()
    assert project.security_region_id == declared.security_section_id
    # `ProjectPolicy.editable_section_ids`/`explore_section_ids` are always
    # sorted (see `app/build/policy.py::ProjectPolicy._ids_with`); the
    # package's own declaration order (panel.json's array order) is not —
    # compared as sets/sorted rather than as tuples for that reason.
    assert set(project.policy.editable_section_ids) == set(declared.editable_section_ids)
    assert project.policy.explore_section_ids == tuple(sorted(declared.explore_section_ids))
    assert project.section_policy("helper_applyCommand") is InteractionPolicy.EDITABLE
    assert project.section_policy("callback_onMessage") is InteractionPolicy.EDITABLE
    assert project.section_policy("setup") is InteractionPolicy.EDITABLE
    assert project.section_policy("global") is InteractionPolicy.EXPLORE
    assert project.section_policy("global_2") is InteractionPolicy.LOCKED


def test_a_package_with_a_prose_only_remediation_opens_nothing() -> None:
    prose_only = dataclasses.replace(
        package(),
        remediation=dataclasses.replace(
            declaration(),
            security_section_id=None,
            editable_section_ids=(),
            explore_section_ids=(),
            criterion=None,
        ),
    )
    project = panel_one_project(prose_only)
    assert project.security_region_id is None
    assert project.policy is None
    assert {s.kind for s in project.file(SKETCH_NAME).segments} == {RegionKind.LOCKED}


# =============================================================================
# 10-15. Locked-region safety, generated source, apply_program, fingerprints
# =============================================================================


def test_an_unauthorized_edit_cannot_reach_locked_firmware_through_a_program() -> None:
    """B7's locked-region safeguard, re-proved on Panel 1's real (widened) policy.

    `setup()` targets a real editable region now — the earlier version of
    this test tampered with a line inside it, which the widened policy would
    accept. A genuinely LOCKED global declaration is what proves the
    safeguard still holds.
    """
    workspace = panel_one_workspace()
    firmware_file = workspace.project.file(SKETCH_NAME)
    program = workspace.program(SKETCH_NAME)

    tampered_source = source_for_program(program).replace(
        'static const char *MQTT_BROKER = "192.168.50.1";',
        'static const char *MQTT_BROKER = "10.0.0.1";',
    )
    from app.build.program_source import apply_program_to_file, program_for_source

    with pytest.raises(LockedRegionChangedError):
        apply_program_to_file(firmware_file, program_for_source(tampered_source))


def test_an_unauthorized_edit_cannot_add_or_remove_a_function() -> None:
    workspace = panel_one_workspace()
    firmware_file = workspace.project.file(SKETCH_NAME)
    from app.build.program_source import apply_program_to_file, program_for_source

    extended = source_for_program(workspace.program(SKETCH_NAME)) + (
        "\n\nstatic void backdoor() {\n  motorStart();\n}\n"
    )
    with pytest.raises(ProgramStructureChangedError):
        apply_program_to_file(firmware_file, program_for_source(extended))


def test_a_rejected_edit_leaves_the_workspace_byte_for_byte_unchanged() -> None:
    # `setup` is a real editable region under the widened policy now —
    # `global_2` (a run of GLOBAL_DECLARATIONS text) is the genuinely LOCKED
    # target this test needs.
    workspace = panel_one_workspace()
    before = workspace.full_source(SKETCH_NAME)
    fingerprint = workspace.fingerprint()
    with pytest.raises(RegionNotEditableError):
        workspace.update_region(SKETCH_NAME, "global_2", "// tampered")
    assert workspace.full_source(SKETCH_NAME) == before
    assert workspace.fingerprint() == fingerprint


def test_a_remediating_edit_changes_only_the_security_region() -> None:
    original = panel_one_workspace()
    edited = remediated_workspace()
    for segment in original.project.file(SKETCH_NAME).segments:
        after = edited.project.file(SKETCH_NAME).segment(segment.region_id)
        if segment.region_id == "helper_applyCommand":
            assert after.text != segment.text
        else:
            assert after.text == segment.text


def test_the_generated_source_carries_the_remediation_and_preserves_the_rest() -> None:
    workspace = remediated_workspace()
    generated = source_for_program(workspace.program(SKETCH_NAME))
    assert "AUTH_TOKEN" in generated
    assert "PANEL1-CMD-AUTH-K7" in generated
    # Every fact the panel must keep is still there, verbatim.
    for preserved in (
        '"192.168.50.1"' if '"192.168.50.1"' in generated else "192.168.50.1",
        "cybertrainer/smart-home/motor/control",
        "cybertrainer/smart-home/motor/state",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
        "MOTOR_IN1",
        "START_BUTTON",
        "GREEN_LED",
        "BUZZER",
        "WiFi.begin(WIFI_SSID, WIFI_PASSWORD)",
    ):
        assert preserved in generated, preserved


def test_applying_a_remediated_program_keeps_every_locked_region() -> None:
    workspace = remediated_workspace()
    before = regenerated(workspace.project.file(SKETCH_NAME))
    workspace.apply_program(SKETCH_NAME, workspace.program(SKETCH_NAME))
    after = workspace.project.file(SKETCH_NAME)
    assert [s.region_id for s in after.segments] == [s.region_id for s in before.segments]
    for segment in before.segments:
        if segment.kind is RegionKind.LOCKED:
            assert after.segment(segment.region_id).text == segment.text
    assert "AUTH_TOKEN" in after.segment("helper_applyCommand").text


def test_an_edit_that_breaks_the_files_structure_is_refused_not_applied() -> None:
    """Region protection is not structure protection, and the IR catches it.

    Submitting a region body without the newline that separates it from the
    preceding comment splices the function into that comment. The edit is
    accepted (the region really is editable), but the semantic path refuses
    to read or rewrite the file rather than producing something that is
    neither the student's code nor the panel's.
    """
    workspace = panel_one_workspace()
    workspace.update_region(
        SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION.lstrip("\n")
    )
    with pytest.raises(ProgramApplyError):
        workspace.program(SKETCH_NAME)


def test_editing_the_security_region_invalidates_a_green_build() -> None:
    compiler = FakeCompilerAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(
            compiler, workspace=panel_one_workspace()
        )
        assert session.compile_status is CompileStatus.SUCCEEDED
        assert session.flash_ready is True
        fingerprint = session.workspace.fingerprint()

        result = await service.edit_region(
            session, SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION
        )
        assert result.success is True
        assert session.workspace.fingerprint() != fingerprint
        assert session.flash_ready is False

        flash = await service.flash_workspace(session)
        assert flash.success is False
        assert "compile" in flash.error

    run(scenario())


def test_editing_the_security_region_is_recorded_as_such() -> None:
    compiler = FakeCompilerAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(
            compiler, workspace=panel_one_workspace()
        )
        result = await service.edit_region(
            session, SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION
        )
        kinds = [event.type for event in result.events]
        assert BuildEventType.CODE_EDITED in kinds
        assert BuildEventType.SECURITY_REGION_EDITED in kinds

    run(scenario())


def test_the_remediated_source_is_what_reaches_the_compiler() -> None:
    compiler = FakeCompilerAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(
            compiler, workspace=panel_one_workspace()
        )
        await service.edit_region(
            session, SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION
        )
        await service.compile_workspace(session)
        assert "AUTH_TOKEN" in compiler.primary_source
        assert compiler.primary_source == session.workspace.full_source(SKETCH_NAME)

    run(scenario())


# =============================================================================
# 16. The validator resolves through the registry, never through a branch
# =============================================================================


def test_panel_one_resolves_to_its_own_validator() -> None:
    plan = select_build_validation(selection_for())
    assert isinstance(plan.strategy, SmartHomeAuthorizationValidator)
    assert plan.remediation.checkable is True


def test_the_registry_has_exactly_one_row_and_it_is_a_registration() -> None:
    assert [scenario_id for scenario_id, _ in BUILT_IN_VALIDATORS] == [
        SMART_HOME_MQTT_CONTROL
    ]
    assert default_validation_registry.scenario_ids == (SMART_HOME_MQTT_CONTROL,)
    assert default_validation_registry.registered("environmental-monitoring") is False


def test_each_session_gets_its_own_validator_instance() -> None:
    first = select_build_validation(selection_for()).strategy
    second = select_build_validation(selection_for()).strategy
    assert first is not second


def test_an_unregistered_experiment_still_declines_rather_than_borrowing_one() -> None:
    other = dataclasses.replace(
        package(),
        scenario=dataclasses.replace(package().scenario, scenario_id="some-other-panel"),
    )
    plan = select_build_validation(selection_for(other))
    assert isinstance(plan.strategy, DeclaredRequirementValidator)


# =============================================================================
# 17-21. The verdicts: what the validator concludes about a given firmware
# =============================================================================


def test_remediated_firmware_passes() -> None:
    factory = panel_factory(firmware=remediated_firmware)
    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.SUCCESS
    assert result.succeeded is True
    panel = factory.built[0]
    assert panel.closed is True
    # Every declared probe was actually sent.
    assert len(panel.published) == len(declaration().criterion.probes)


def test_an_authorized_command_is_accepted_and_moves_the_motor() -> None:
    factory = panel_factory(firmware=remediated_firmware)
    result = check(factory=factory)
    probes = {row["probe_id"]: row for row in result.details["probes"]}
    start = probes["authorized-start"]
    assert start["authorization"] == "authorized"
    assert start["token"] == "valid"
    assert (start["state_before"], start["state_after"]) == ("STOPPED", "RUNNING")
    assert start["state_change_observed"] is True
    assert result.details["observed_acceptances"] >= 1


def test_an_unauthorized_command_is_rejected_and_the_motor_does_not_move() -> None:
    factory = panel_factory(firmware=remediated_firmware)
    result = check(factory=factory)
    probes = {row["probe_id"]: row for row in result.details["probes"]}
    for probe_id in ("forged-start-no-token", "forged-start-wrong-token"):
        row = probes[probe_id]
        assert row["authorization"] == "unauthorized"
        assert row["state_before"] == row["state_after"] == "STOPPED"
        assert row["passed"] is True
    running = probes["forged-stop-running"]
    assert running["state_before"] == running["state_after"] == "RUNNING"


def test_the_unauthorized_client_publishes_as_a_different_identity() -> None:
    factory = panel_factory(firmware=remediated_firmware)
    check(factory=factory)
    criterion = declaration().criterion
    identities = {identity for identity, _, _ in factory.built[0].published}
    assert identities == {
        criterion.authorized.identity_id,
        criterion.unauthorized.identity_id,
    }


def test_an_unauthorized_command_carries_no_token_on_the_wire() -> None:
    factory = panel_factory(firmware=remediated_firmware)
    check(factory=factory)
    token = FIXTURES["TRAINER_LAB_PANEL1_COMMAND_TOKEN"]
    guest = declaration().criterion.unauthorized.identity_id
    for identity, _, payload in factory.built[0].published:
        if identity == guest:
            assert token not in payload


def test_the_committed_vulnerable_firmware_fails() -> None:
    """The check must catch the very firmware the panel ships today."""
    result = check(factory=panel_factory(firmware=vulnerable_firmware))
    assert result.outcome is ValidationOutcome.FAILURE
    assert result.succeeded is False
    assert "unauthorized" in result.message
    assert result.details["failed_probe"] == "forged-start-no-token"


def test_firmware_that_ignores_everything_fails() -> None:
    """"Reject everything" is not a fix — it breaks legitimate control."""
    result = check(factory=panel_factory(firmware=deaf_firmware, state="RUNNING"))
    assert result.outcome is ValidationOutcome.FAILURE
    assert "did not obey a properly authorized" in result.message


def test_firmware_that_checks_for_any_suffix_fails() -> None:
    """Which is exactly why the criterion declares a WRONG token as well."""
    result = check(factory=panel_factory(firmware=token_shaped_firmware))
    assert result.outcome is ValidationOutcome.FAILURE
    assert result.details["failed_probe"] == "forged-start-wrong-token"


def test_a_device_that_never_changes_state_is_not_a_pass() -> None:
    """Every probe "matches", but no acceptance was ever witnessed."""

    class StuckPanel(FakePanel):
        def publish(self, identity_id, topic, payload):
            self.published.append((identity_id, topic, payload))

    def factory(criterion, secrets):
        return StuckPanel(criterion, secrets, state="STOPPED")

    # With the panel stuck at STOPPED, the two probes expecting RUNNING fail
    # first — which is the right answer for a different reason, so drive the
    # narrower case directly by declaring only probes that expect STOPPED.
    spec = remediation_spec_for(package())
    stopped = tuple(p for p in spec.criterion.probes if p.expect_state == "STOPPED")
    spec = dataclasses.replace(
        spec, criterion=dataclasses.replace(spec.criterion, probes=stopped)
    )
    result = run(validator(factory=factory).validate(context_for(spec)))
    assert result.outcome is ValidationOutcome.FAILURE
    assert "never demonstrated" in result.message


# =============================================================================
# 21b. Guaranteed physical-state restoration: a failure must not leave the
#      motor running just because `restore-stop` was never reached.
# =============================================================================


class FlakyPanel(FakePanel):
    """A `FakePanel` whose Nth `publish` raises, then behaves normally again.

    Models an evidence channel that drops mid-probe (`EvidenceChannelError`,
    exactly like `DroppingPanel` elsewhere in this file) but only once, so
    the cleanup restoration's OWN publish call — issued right after — can
    still be observed succeeding.
    """

    def __init__(self, *args, fail_on_call: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_on_call = fail_on_call
        self._calls = 0

    def publish(self, identity_id: str, topic: str, payload: str) -> None:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise EvidenceChannelError("the broker closed the connection mid-probe")
        super().publish(identity_id, topic, payload)


class CleanupFailsPanel(FakePanel):
    """A `FakePanel` whose cleanup publish call (and only that one) fails."""

    def __init__(self, *args, fail_on_call: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fail_on_call = fail_on_call
        self._calls = 0

    def publish(self, identity_id: str, topic: str, payload: str) -> None:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise EvidenceChannelError("cleanup broker rejected the STOP")
        super().publish(identity_id, topic, payload)


def _cleanup_publish(panel: FakePanel) -> tuple[str, str, str]:
    """The last thing the panel saw published — the cleanup attempt, by contract."""
    return panel.published[-1]


def test_an_early_forged_start_failure_triggers_a_cleanup_stop() -> None:
    """The committed vulnerable firmware fails at probe 2, motor left RUNNING."""
    factory = panel_factory(firmware=vulnerable_firmware)
    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.FAILURE
    assert result.details["failed_probe"] == "forged-start-no-token"

    panel = factory.built[0]
    criterion = declaration().criterion
    engine_criterion = remediation_spec_for(package()).criterion
    # 2 probes were sent before the failure (baseline-stop, forged-start-
    # no-token), plus exactly one cleanup STOP — never a second one.
    assert len(panel.published) == 3
    identity, topic, payload = _cleanup_publish(panel)
    assert identity == criterion.authorized.identity_id
    assert topic == criterion.control_topic
    assert payload == engine_criterion.probes[-1].payload(
        valid_token=FIXTURES["TRAINER_LAB_PANEL1_COMMAND_TOKEN"],
        invalid_token=engine_criterion.invalid_token,
        separator=engine_criterion.token_separator,
    )
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is None


def test_the_p0_case_a_forged_start_left_running_by_an_untouched_baseline_is_not_confirmed_stopped() -> None:
    """THE P0 THIS FIX CLOSES. The committed vulnerable baseline recognises
    only the bare command word "STOP" (see `vulnerable_firmware`), so the
    tokenized STOP the cleanup restoration sends is silently ignored: the
    broker still accepts the publish (`cleanup_stop_error` stays None), but
    the motor the `forged-start-no-token` probe already started stays
    RUNNING. Before this fix, "the publish did not raise" was the only
    signal reported and that read as an unqualified cleanup success; now the
    validator reads the same evidence topic every probe reads and reports
    the true, unconfirmed physical state instead."""
    factory = panel_factory(firmware=vulnerable_firmware)
    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.FAILURE

    # The publish itself succeeded...
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is None
    # ...but the panel never actually stopped, and that must be visible.
    assert result.details["cleanup_confirmed"] is False
    assert result.details["cleanup_observed_state"] == "RUNNING"
    assert result.details["cleanup_outcome"] == "attempted_unconfirmed"

    panel = factory.built[0]
    assert panel.state() == "RUNNING"


def test_an_early_forged_stop_failure_triggers_a_cleanup_stop() -> None:
    """A later probe (5 of 6) fails; cleanup must not depend on which one."""
    factory = panel_factory(firmware=stop_forgeable_firmware)
    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.FAILURE
    assert result.details["failed_probe"] == "forged-stop-running"

    panel = factory.built[0]
    criterion = declaration().criterion
    # 5 probes were sent before the failure, plus exactly one cleanup STOP.
    assert len(panel.published) == 6
    identity, topic, payload = _cleanup_publish(panel)
    assert identity == criterion.authorized.identity_id
    assert topic == criterion.control_topic
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is None
    # This firmware DOES obey the tokenized restore STOP (it accepts any
    # STOP, forged or not — that is its own, different bug), so the panel
    # is genuinely observed at rest afterwards: a confirmed cleanup.
    assert result.details["cleanup_confirmed"] is True
    assert result.details["cleanup_observed_state"] == "STOPPED"
    assert result.details["cleanup_outcome"] == "confirmed"


def test_a_probe_exception_triggers_cleanup_before_propagating() -> None:
    """A dropped evidence channel must still be cleaned up, not just reported."""
    built: list[FlakyPanel] = []

    def factory(criterion, secrets):
        panel = FlakyPanel(criterion, secrets, fail_on_call=2)
        built.append(panel)
        return panel

    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.ERROR
    assert result.details["stage"] == "probe"

    panel = built[0]
    # Call 1 (baseline-stop) succeeded, call 2 (forged-start-no-token) raised
    # and is never recorded, call 3 is the cleanup STOP that followed it.
    assert len(panel.published) == 2
    criterion = declaration().criterion
    identity, topic, payload = _cleanup_publish(panel)
    assert identity == criterion.authorized.identity_id
    assert topic == criterion.control_topic
    assert FIXTURES["TRAINER_LAB_PANEL1_COMMAND_TOKEN"] in payload
    # A validator exception is not a verdict, but the cleanup it triggered
    # is still reported — and the default (remediated) firmware genuinely
    # obeys the restore STOP, so this recovers to a confirmed cleanup.
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is None
    assert result.details["cleanup_confirmed"] is True
    assert result.details["cleanup_outcome"] == "confirmed"


def test_a_cleanup_failure_does_not_replace_the_original_failure() -> None:
    """The verdict the probes reached must survive even if cleanup itself fails."""

    def factory(criterion, secrets):
        return CleanupFailsPanel(
            criterion, secrets, fail_on_call=3, firmware=vulnerable_firmware
        )

    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.FAILURE
    assert result.details["failed_probe"] == "forged-start-no-token"
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is not None
    assert "cleanup broker rejected" in result.details["cleanup_stop_error"]
    # A publish that never reached the panel cannot possibly be confirmed.
    assert result.details["cleanup_confirmed"] is False
    assert result.details["cleanup_observed_state"] is None
    assert result.details["cleanup_outcome"] == "attempted_unconfirmed"


def test_a_successful_run_sends_no_extra_cleanup_stop() -> None:
    """Requirement 9: a passing run must not send a redundant restore STOP."""
    factory = panel_factory(firmware=remediated_firmware)
    result = check(factory=factory)
    assert result.outcome is ValidationOutcome.SUCCESS
    panel = factory.built[0]
    assert len(panel.published) == len(declaration().criterion.probes)
    assert "cleanup_stop_attempted" not in result.details
    # No cleanup ran at all, so none of the tri-state cleanup keys appear —
    # the run's own `restore-stop` probe (already in `result.details["probes"]`)
    # is the evidence that the panel is at rest, and it is not duplicated.
    assert "cleanup_confirmed" not in result.details
    assert "cleanup_outcome" not in result.details


def test_cleanup_uses_the_authorized_identity_and_valid_token() -> None:
    factory = panel_factory(firmware=vulnerable_firmware)
    check(factory=factory)
    criterion = declaration().criterion
    panel = factory.built[0]
    identity, topic, payload = _cleanup_publish(panel)
    assert identity == criterion.authorized.identity_id
    assert identity != criterion.unauthorized.identity_id
    assert topic == criterion.control_topic
    assert FIXTURES["TRAINER_LAB_PANEL1_COMMAND_TOKEN"] in payload
    assert criterion.invalid_token not in payload


# =============================================================================
# 22-25. Unavailable, error, and the evidence a verdict carries
# =============================================================================


def test_a_panel_with_no_criterion_is_unavailable_not_failed() -> None:
    spec = dataclasses.replace(remediation_spec_for(package()), criterion=None)
    reason = validator().unavailable_reason(context_for(spec))
    assert reason is not None and "nothing to check" in reason
    result = run(validator().validate(context_for(spec)))
    assert result.outcome is ValidationOutcome.NOT_RUN


def test_an_unprovisioned_deployment_is_unavailable_and_names_the_variables() -> None:
    reason = validator(fixtures={}).unavailable_reason(context_for())
    assert reason is not None
    for name in FIXTURES:
        assert name in reason
    assert "credential" in reason


def test_one_missing_fixture_is_enough_to_be_unavailable() -> None:
    partial = dict(FIXTURES)
    del partial["TRAINER_LAB_PANEL1_COMMAND_TOKEN"]
    reason = validator(fixtures=partial).unavailable_reason(context_for())
    assert reason is not None
    assert "TRAINER_LAB_PANEL1_COMMAND_TOKEN" in reason


def test_a_fully_provisioned_deployment_is_available() -> None:
    assert validator().unavailable_reason(context_for()) is None


def test_an_unreadable_evidence_channel_is_unavailable() -> None:
    criterion = dataclasses.replace(
        remediation_spec_for(package()).criterion, evidence=EvidenceChannel.DEVICE_SERIAL
    )
    spec = dataclasses.replace(remediation_spec_for(package()), criterion=criterion)
    reason = validator().unavailable_reason(context_for(spec))
    assert reason is not None and "cannot read" in reason


def test_an_unreachable_broker_is_an_error_not_a_failure() -> None:
    result = check(factory=broken_factory())
    assert result.outcome is ValidationOutcome.ERROR
    assert result.succeeded is False
    assert result.ran is False
    assert result.details["stage"] == "open"


def test_a_channel_lost_mid_check_is_an_error() -> None:
    class DroppingPanel(FakePanel):
        def publish(self, identity_id, topic, payload):
            raise EvidenceChannelError("the broker closed the connection")

    result = check(factory=lambda c, s: DroppingPanel(c, s))
    assert result.outcome is ValidationOutcome.ERROR
    assert result.details["stage"] == "probe"
    # The channel that broke the check is the same channel cleanup needs, so
    # the best-effort restoration also fails here — reported, not hidden,
    # and the outcome it attaches to is still ERROR, never FAILURE.
    assert result.details["cleanup_stop_attempted"] is True
    assert result.details["cleanup_stop_error"] is not None
    assert result.details["cleanup_confirmed"] is False
    assert result.details["cleanup_outcome"] == "attempted_unconfirmed"


def test_a_device_that_reports_no_state_is_an_error_not_a_verdict() -> None:
    result = check(factory=panel_factory(reports_state=False))
    assert result.outcome is ValidationOutcome.ERROR
    assert "never reported a state" in result.message
    assert result.details["stage"] == "baseline"


def test_the_verdict_carries_evidence_an_evaluator_can_read() -> None:
    result = check(factory=panel_factory(firmware=remediated_firmware))
    assert result.details["criterion"] == "per-command-authorization"
    assert result.details["evidence"] == "mqtt_state_topic"
    assert result.details["firmware_fingerprint"] == "fingerprint-under-test"
    rows = result.details["probes"]
    assert [row["probe_id"] for row in rows] == list(declaration().criterion.probe_ids)
    for row in rows:
        assert set(row) == {
            "probe_id",
            "authorization",
            "command",
            "token",
            "expected_accepted",
            "expected_state",
            "state_before",
            "state_after",
            "passed",
            "state_change_observed",
        }
    assert json.dumps(result.snapshot())  # serialisable for the state frame


def test_evidence_rows_carry_no_credential_or_token() -> None:
    """A stored evidence row must be safe to show an instructor."""
    result = check(factory=panel_factory(firmware=remediated_firmware))
    rendered = json.dumps(result.snapshot())
    for secret in FIXTURES.values():
        assert secret not in rendered


# =============================================================================
# 26-27. The engine records it, and the metrics see it
# =============================================================================


async def _validated_session(strategy, workspace=None):
    plan = ValidationPlan(
        strategy=strategy,
        remediation=remediation_spec_for(package()),
        source="test",
    )
    flasher = FakeFlasherAdapter()
    session, service = await compiled_session(
        FakeCompilerAdapter(),
        flasher,
        workspace=workspace or panel_one_workspace(),
        validation=plan,
    )
    await service.flash_workspace(session)
    assert session.flash_status is FlashStatus.SUCCEEDED
    return session, service


def test_a_successful_check_is_recorded_as_a_validation_attempt() -> None:
    async def scenario() -> None:
        session, service = await _validated_session(
            validator(factory=panel_factory(firmware=remediated_firmware))
        )
        result = await service.validate_workspace(session)
        assert result.success is True
        assert session.validation_status is ValidationStatus.SUCCEEDED
        kinds = [event.type for event in result.events]
        assert BuildEventType.VALIDATION_STARTED in kinds
        assert BuildEventType.VALIDATION_SUCCEEDED in kinds
        rows = [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert [a.success for a in rows] == [True]

    run(scenario())


def test_a_failed_check_is_recorded_as_a_failed_attempt() -> None:
    async def scenario() -> None:
        session, service = await _validated_session(
            validator(factory=panel_factory(firmware=vulnerable_firmware))
        )
        result = await service.validate_workspace(session)
        assert result.success is True  # the ACTION succeeded; the fix did not
        assert session.validation_status is ValidationStatus.FAILED
        assert BuildEventType.VALIDATION_FAILED in [e.type for e in result.events]
        rows = [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert [a.success for a in rows] == [False]

    run(scenario())


def test_an_unavailable_check_records_no_attempt_at_all() -> None:
    async def scenario() -> None:
        session, service = await _validated_session(validator(fixtures={}))
        result = await service.validate_workspace(session)
        assert result.success is False
        assert "TRAINER_LAB_" in result.error
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ] == []

    run(scenario())


def test_metrics_read_a_real_panel_one_validation(isolated_event_store) -> None:
    async def scenario() -> None:
        session, service = await _validated_session(
            validator(factory=panel_factory(firmware=remediated_firmware))
        )
        await service.validate_workspace(session)
        await service.end_session(session)
        header = isolated_event_store.build_session(session.session_id)
        attempts = isolated_event_store.build_attempts_for_session(session.session_id)
        assert compute_ttr(header, attempts).status is MetricStatus.COMPUTED
        assert compute_aid(header, attempts).status is MetricStatus.COMPUTED

    run(scenario())


def test_a_failed_remediation_is_not_a_validated_successful_fix(
    isolated_event_store,
) -> None:
    async def scenario() -> None:
        session, service = await _validated_session(
            validator(factory=panel_factory(firmware=vulnerable_firmware))
        )
        await service.validate_workspace(session)
        await service.end_session(session)
        header = isolated_event_store.build_session(session.session_id)
        attempts = isolated_event_store.build_attempts_for_session(session.session_id)
        assert compute_ttr(header, attempts).status is MetricStatus.NOT_APPLICABLE
        assert compute_aid(header, attempts).status is MetricStatus.COMPUTED

    run(scenario())


def test_validation_is_still_gated_behind_a_matching_flash() -> None:
    """B7's gates are unchanged by a validator that can actually run."""

    async def scenario() -> None:
        session, service = await _validated_session(
            validator(factory=panel_factory(firmware=remediated_firmware))
        )
        await service.edit_region(
            session, SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION
        )
        result = await service.validate_workspace(session)
        assert result.success is False
        assert "changed after the firmware that was flashed" in result.error
        assert session.validation_status is ValidationStatus.NOT_STARTED

    run(scenario())


# =============================================================================
# The whole chain, in one place
# =============================================================================


def test_the_backend_path_from_package_to_verdict_runs_end_to_end() -> None:
    """PanelPackage -> project -> policy -> edit -> C++ -> compile -> flash -> verdict.

    Every link B8 had to close, walked once in order, with only the toolchain
    and the evidence channel faked. No frontend is involved: this is the
    backend path the Build Mode redesign will later drive.
    """
    compiler = FakeCompilerAdapter()

    async def scenario() -> None:
        # 1. PanelPackage -> BuildProject, under the package's own policy.
        selection = selector_over(panel_one_identification()).select()
        assert selection.source is BuildProjectSource.PANEL_PACKAGE
        workspace = selection.workspace
        project = workspace.project
        assert project.section_policy("helper_applyCommand") is InteractionPolicy.EDITABLE
        assert project.security_region_id == "helper_applyCommand"

        # 2. The session gets that workspace and that panel's validator.
        plan = select_build_validation(selection)
        assert isinstance(plan.strategy, SmartHomeAuthorizationValidator)
        plan = ValidationPlan(
            strategy=validator(factory=panel_factory(firmware=remediated_firmware)),
            remediation=plan.remediation,
            parameters=plan.parameters,
            source="test",
        )
        session, service = await compiled_session(
            compiler, FakeFlasherAdapter(), workspace=workspace, validation=plan
        )
        original = session.workspace.fingerprint()

        # 3. The student edits the one editable region.
        edit = await service.edit_region(
            session, SKETCH_NAME, "helper_applyCommand", REMEDIATED_REGION
        )
        assert edit.success is True
        assert BuildEventType.SECURITY_REGION_EDITED in [e.type for e in edit.events]

        # 4. The edit survives the semantic round trip and becomes the source.
        session.workspace.apply_program(
            SKETCH_NAME, session.workspace.program(SKETCH_NAME)
        )
        assert "AUTH_TOKEN" in session.workspace.full_source(SKETCH_NAME)

        # 5. The fingerprint moved, so the green build no longer authorises a flash.
        assert session.workspace.fingerprint() != original
        assert session.flash_ready is False
        assert (await service.flash_workspace(session)).success is False

        # 6. Recompile: the generated source is what the compiler receives.
        await service.compile_workspace(session)
        assert session.compile_status is CompileStatus.SUCCEEDED
        assert "AUTH_TOKEN" in compiler.primary_source
        assert compiler.primary_source == session.workspace.full_source(SKETCH_NAME)
        assert session.flash_ready is True

        # 7. Flash, then validate — and only then.
        await service.flash_workspace(session)
        assert session.flash_status is FlashStatus.SUCCEEDED
        result = await service.validate_workspace(session)
        assert result.success is True
        assert session.validation_status is ValidationStatus.SUCCEEDED
        assert session.validation_result.outcome is ValidationOutcome.SUCCESS

    run(scenario())


# =============================================================================
# 28-32. Boundaries: no panel logic in the engine, no secrets, nothing faked
# =============================================================================


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _code_without_docstring(path: pathlib.Path) -> str:
    source = path.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    return code.split('"""', 2)[-1]


def test_the_generic_build_service_still_knows_nothing_about_panel_one() -> None:
    body = _code_without_docstring(BACKEND / "app" / "build" / "service.py")
    for term in ("smart-home", "smart_home", "mqtt", "MQTT", "motor", "broker", "panel_id =="):
        assert term not in body, f"build/service.py code mentions {term!r}"


def test_the_generic_policy_and_workspace_layers_name_no_panel() -> None:
    for module in ("policy.py", "workspace.py", "document_project.py", "models.py"):
        body = _code_without_docstring(BACKEND / "app" / "build" / module)
        for term in ("smart-home", "smart_home", "applyCommand", "onMessage", "motor"):
            assert term not in body, f"build/{module} code mentions {term!r}"


def test_the_policy_layer_is_a_leaf() -> None:
    """It classifies sections; it knows no panel, no block, no I/O."""
    assert _imports(BACKEND / "app" / "build" / "policy.py") == {
        "__future__",
        "dataclasses",
        "enum",
        "types",
        "typing",
    }


def test_the_validation_package_still_imports_no_panel_or_hardware_layer() -> None:
    banned = (
        "app.panels",
        "app.hardware",
        "app.scenarios",
        "app.build_sessions",
        "app.build.service",
        "app.build.compiler",
        "app.build.flasher",
        "app.build.process",
        "subprocess",
        "os",
        "fastapi",
    )
    for path in sorted((BACKEND / "app" / "build" / "validation").glob("*.py")):
        offenders = sorted(
            name
            for name in _imports(path)
            for bad in banned
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_the_panel_layer_still_imports_no_build_layer() -> None:
    for module in ("models.py", "loader.py", "service.py", "__init__.py"):
        offenders = sorted(
            name
            for name in _imports(BACKEND / "app" / "panels" / module)
            for bad in ("app.build", "subprocess", "socket", "serial")
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"panels/{module} imports {offenders}"


def test_no_b8_module_spawns_a_process_or_evaluates_anything() -> None:
    banned_calls = {"system", "popen", "spawn", "Popen", "eval", "exec", "__import__"}
    modules = [
        BACKEND / "app" / "build" / "policy.py",
        BACKEND / "app" / "build_validation_selection.py",
        *(BACKEND / "app" / "build" / "validation").glob("*.py"),
    ]
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in banned_calls, f"{path.name} calls {name}"


def test_the_manifest_carries_no_credential_only_the_name_of_one() -> None:
    raw = json.loads(PANEL_MANIFEST.read_text(encoding="utf-8"))
    criterion = raw["remediation"]["criterion"]
    for identity in (criterion["authorized"], criterion["unauthorized"]):
        assert set(identity) == {"identity_id", "username", "password_env"}
        assert identity["password_env"].startswith("TRAINER_LAB_")
        assert "password" not in {k for k in identity if k != "password_env"}
    assert criterion["token_env"].startswith("TRAINER_LAB_")
    # No field anywhere in the criterion is spelled like a stored secret.
    rendered = json.dumps(criterion)
    for forbidden in ('"password"', '"passwd"', '"secret"', '"psk"', '"credential"'):
        assert forbidden not in rendered, forbidden
    # The only token-shaped VALUES are the env var's name and a deliberately
    # wrong token, which is safe precisely because it is wrong.
    assert criterion["invalid_token"] == "WRONG-TOKEN-0000"


def test_the_manifest_carries_no_wifi_credential() -> None:
    rendered = PANEL_MANIFEST.read_text(encoding="utf-8")
    for forbidden in ("WIFI_PASSWORD", "wifi_password", "psk", "wpa"):
        assert forbidden not in rendered, forbidden


def test_the_committed_firmware_carries_its_real_literal_lab_credentials() -> None:
    """Panel 1's firmware is self-contained by design: the real CyberTrainer
    Wi-Fi/Mosquitto credentials for this isolated lab network are literals
    in the committed .ino (see its file header), not placeholders rewritten
    at compile time — Option A provisioning is retired for this panel (see
    `app/build_provisioning_selection.py`'s empty `default_provisioning_registry`)."""
    source = (PANEL_SKETCH / SKETCH_NAME).read_text(encoding="utf-8")
    assert 'WIFI_PASSWORD = "CyberTrainer2026"' in source
    assert 'MQTT_PASSWORD = "cybertrainer"' in source
    assert "CHANGE_ME_LAB" not in source


def test_panel_one_selection_resolves_to_noop_provisioning() -> None:
    """Panel 1's `scenario_id` resolves through `select_build_provisioning`
    to the same shared no-op every unregistered panel gets, exactly like
    `select_build_validation` resolving Panel 1's validator elsewhere in
    this file — nothing here injects a real Wi-Fi/MQTT credential into
    anything."""
    from app.build.provisioning import NULL_PROVISIONING_STRATEGY
    from app.build_provisioning_selection import select_build_provisioning

    selection = selector_over(panel_one_identification()).select()
    plan = select_build_provisioning(selection)
    assert plan.strategy is NULL_PROVISIONING_STRATEGY


def test_lab_fixtures_are_only_readable_through_the_prefixed_lookup() -> None:
    from app import config

    assert config.lab_secret("PATH") == ""
    assert config.lab_secret("AWS_SECRET_ACCESS_KEY") == ""
    assert config.lab_secret("") == ""
    assert config.lab_secret(None) == ""  # type: ignore[arg-type]


def test_lab_fixtures_are_unset_in_this_environment(monkeypatch) -> None:
    """Which is why the shipped validator is UNAVAILABLE in CI, not passing."""
    from app import config

    for name in FIXTURES:
        monkeypatch.delenv(name, raising=False)
    real = SmartHomeAuthorizationValidator()
    reason = real.unavailable_reason(context_for())
    assert reason is not None
    assert config.lab_secret("TRAINER_LAB_PANEL1_COMMAND_TOKEN") == ""


def test_no_shipped_module_simulates_a_device() -> None:
    """There is no fake panel in `app/`; the only one is in this test file.

    This is what makes "the validator cannot pass without real hardware" a
    checked fact rather than a claim: the production evidence channel either
    talks to a broker or raises.
    """
    for path in sorted((BACKEND / "app" / "build" / "validation").glob("*.py")):
        body = _code_without_docstring(path)
        for term in ("FakePanel", "simulate", "Simulated", "fake_", "stub_"):
            assert term not in body, f"{path.name} mentions {term!r}"


def test_the_real_evidence_channel_refuses_rather_than_pretending() -> None:
    """With no paho-mqtt and no broker, opening one raises — it never returns
    a channel that would report a state nobody observed."""
    from app.build.validation import mqtt_evidence

    criterion = remediation_spec_for(package()).criterion
    if mqtt_evidence.available():  # pragma: no cover - depends on the environment
        with pytest.raises(EvidenceChannelError):
            mqtt_evidence.open_mqtt_evidence(criterion, FIXTURES)
    else:
        with pytest.raises(EvidenceChannelError, match="paho-mqtt"):
            mqtt_evidence.open_mqtt_evidence(criterion, FIXTURES)


def test_the_fake_panel_really_implements_the_production_protocol() -> None:
    panel = FakePanel(remediation_spec_for(package()).criterion, FIXTURES)
    assert isinstance(panel, AuthorizationEvidence)


# =============================================================================
# The semantic question B8 had to answer: are five operations enough?
# =============================================================================


def test_the_semantic_operation_set_now_includes_the_no_device_correction() -> None:
    """CORRECTED: B8 left the operation set at five; the no-device/Blockly-
    integration correction deliberately grew it by exactly one —
    `functions.implementation`, the generic named-function container that
    makes `helper_applyCommand` (and every other HELPER_FUNCTION/CALLBACK)
    representable at all. `ConditionalStatement`/`ComparisonValue`/
    `CallStatement` are structural IR additions beside the operation table,
    not new registry rows — see `app/build/semantic/models.py`.

    `helper_applyCommand` is representable now
    (`test_panel_one_remediation_is_now_representable_through_dedicated_blocks`
    in `test_build_section_blockly.py`), and the legacy `edit_region` text
    path remains available beside it — this test only pins the operation
    table itself.

    The token-parsing hardening then added exactly three VALUE operations
    (`text.index_of`/`text.substring`/`text.length`) — the string queries the
    documented `"<COMMAND> <TOKEN>"` remediation needs. Local declarations,
    `return;`, `<=` and `+` are structural IR additions, again not rows.
    """
    from app.build.semantic import default_semantic_operations

    assert default_semantic_operations.operation_ids == (
        "program.setup",
        "program.loop",
        "functions.implementation",
        "gpio.pin_mode",
        "gpio.digital_write",
        "time.delay",
        "text.index_of",
        "text.substring",
        "text.length",
    )


def test_the_security_region_round_trips_verbatim_through_the_ir() -> None:
    """Nothing is LOST on a construct the IR only partially understands.

    CORRECTED: `applyCommand` is a parameterised helper, and the IR now DOES
    have a container form for it (`functions.implementation`) — but its
    signature is fixed text (`SemanticSection.signature`) and most of this
    particular edit's body (a local declaration, `.indexOf`/`.substring`
    calls, an early `return`) is still outside the tiny recognized subset, so
    it survives as several ordered `UnsupportedStatement`s rather than one.
    Concatenating them in order reconstructs the edit exactly — the
    preservation guarantee this test exists to pin, not an argument that text
    is the right interface.
    """
    workspace = remediated_workspace()
    edited = workspace.region_source(SKETCH_NAME, "helper_applyCommand")
    program = workspace.program(SKETCH_NAME)
    section = program.section("helper_applyCommand")
    assert section.supported is True
    assert section.operation_id == "functions.implementation"
    assert section.signature == "static void applyCommand(const String &message)"
    # Every statement's own text, concatenated with the generator's own
    # indent/brace convention, reconstructs the student's edit exactly.
    from app.build.semantic.generator import generate_cpp

    regenerated = generate_cpp(program)
    start = regenerated.index("static void applyCommand")
    end = regenerated.index("\n}\n", start) + len("\n}")
    for fragment in (
        'AUTH_TOKEN = "PANEL1-CMD-AUTH-K7"',
        "message.indexOf(' ')",
        "message.substring(split + 1) != AUTH_TOKEN",
        "message.substring(0, split)",
        'command == "START"',
        "motorStart();",
        'command == "STOP"',
        "motorStop();",
    ):
        assert fragment in regenerated[start:end], fragment
    assert edited.strip()  # the student's edit was non-empty to begin with
