"""Phase B7 verification: generated source -> compile -> flash -> validation.

B7 is integration, not new toolchain behaviour, so this file is about the
SEAMS and the ORDER — that the source the compiler sees is the source the
student's last edit produced, that each stage gates the next, and above all
that no stage's success is ever read as the next one's.

WHAT IS FAKED AND WHY. `FakeCompilerAdapter`/`FakeFlasherAdapter` follow the
convention `tests/test_build_service.py` established: no subprocess, no
physical ESP32, because `test_build_compiler.py` and `test_build_flasher.py`
already cover the real adapters' own behaviour and the whole suite must run
with nothing plugged in. What is NOT faked is the contract: the fakes
implement the same `CompilerAdapter`/`FlasherAdapter` protocols production
uses, they are driven through the real `BuildService`, and the compile path
still materializes a real sketch directory to disk, so a test can read the
exact bytes `arduino-cli` would have been handed.

VALIDATORS ARE FAKED TOO, AND THAT IS THE POINT. No panel in this codebase
declares a machine-checkable remediation criterion, so the only validator
that ships refuses to run (`DeclaredRequirementValidator`). Proving the
lifecycle therefore means injecting strategies that return each outcome and
asserting what the engine does with them — never asserting that Panel 1's
firmware is secure, which nothing here knows.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.build import (
    BuildWorkspace,
    board_info_from_fqbn,
    create_blink_project,
    create_default_workspace,
    load_sketch_project,
)
from app.build.compiler import CompileFailureCategory, CompileOutcome, CompileRequest
from app.build.events import BuildEventType
from app.build.flasher import (
    DeviceDetectOutcome,
    DeviceDetectRequest,
    FlashFailureCategory,
    FlashOutcome,
    FlashRequest,
    SerialDevice,
)
from app.build.models import CompileStatus, FlashStatus, ValidationStatus
from app.build.program_source import (
    LockedRegionChangedError,
    ProgramStructureChangedError,
    UnstructuredFileError,
    apply_program_to_file,
    program_for_source,
    regenerated,
    source_for_program,
)
from app.build.records import BuildAttemptType
from app.build.semantic import (
    TIME_DELAY,
    LiteralValue,
    OperationStatement,
    SemanticArgument,
    SemanticType,
    default_semantic_operations,
)
from app.build.service import BuildService
from app.build.validation import (
    DeclaredRequirementValidator,
    RemediationSpec,
    ValidationContext,
    ValidationOutcome,
    ValidationPlan,
    ValidationResult,
    ValidationStrategyRegistry,
    build_default_validation_registry,
    default_validation_plan,
    default_validation_registry,
)
from app.build_project_selection import BuildProjectSelection, BuildProjectSource
from app.build_sessions import BuildSession
from app.build_validation_selection import (
    remediation_spec_for,
    select_build_validation,
)
from app.metrics import MetricStatus, compute_aid, compute_ttr
from app.panels import default_panel_package_loader
from app.panels.service import PanelResourceStatus

PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = (
    Path(__file__).resolve().parents[1]
    / "panels"
    / PANEL_ONE
    / "firmware"
    / "smart_home_mqtt_control"
)


def run(coro):
    return asyncio.run(coro)


# =============================================================================
# Doubles
# =============================================================================


class FakeCompilerAdapter:
    """A `CompilerAdapter` that records the SOURCE it was handed.

    The difference from `test_build_service.py`'s double: this one reads the
    materialized sketch directory before returning, so a test can assert what
    the compiler would actually have compiled — which is the whole of B7's
    "source of truth" requirement.
    """

    def __init__(self, outcome: CompileOutcome | None = None) -> None:
        self.outcome = outcome if outcome is not None else _compiled_ok()
        self.requests: list[CompileRequest] = []
        self.sources: list[dict[str, str]] = []

    async def run_compile(self, request: CompileRequest) -> CompileOutcome:
        self.requests.append(request)
        self.sources.append(
            {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted(request.sketch_dir.iterdir())
                if path.is_file()
            }
        )
        await asyncio.sleep(0)
        return self.outcome

    @property
    def primary_source(self) -> str:
        """The `.ino` text handed to the most recent compile."""
        latest = self.sources[-1]
        return next(text for name, text in latest.items() if name.endswith(".ino"))


class FakeFlasherAdapter:
    """A `FlasherAdapter` double: one canned device, one canned upload result."""

    def __init__(
        self,
        outcome: FlashOutcome | None = None,
        devices: tuple[SerialDevice, ...] = (SerialDevice(port="COM-FAKE", board_name="ESP32"),),
    ) -> None:
        self.outcome = outcome if outcome is not None else _flashed_ok()
        self.devices = devices
        self.flash_requests: list[FlashRequest] = []

    async def detect_devices(self, request: DeviceDetectRequest) -> DeviceDetectOutcome:
        await asyncio.sleep(0)
        return DeviceDetectOutcome(devices=self.devices, duration_seconds=0.1)

    async def run_flash(self, request: FlashRequest) -> FlashOutcome:
        self.flash_requests.append(request)
        await asyncio.sleep(0)
        return replace(self.outcome, port=request.port)


class RecordingValidator:
    """A `ValidationStrategy` that returns a fixed verdict and counts calls."""

    def __init__(self, result: ValidationResult, unavailable: str | None = None) -> None:
        self.result = result
        self.unavailable = unavailable
        self.contexts: list[ValidationContext] = []

    def unavailable_reason(self, context: ValidationContext) -> str | None:
        return self.unavailable

    async def validate(self, context: ValidationContext) -> ValidationResult:
        self.contexts.append(context)
        await asyncio.sleep(0)
        return self.result

    @property
    def calls(self) -> int:
        return len(self.contexts)


class ExplodingValidator:
    """A validator that raises — a broken check, not a failed fix."""

    def __init__(self) -> None:
        self.calls = 0

    def unavailable_reason(self, context: ValidationContext) -> str | None:
        return None

    async def validate(self, context: ValidationContext) -> ValidationResult:
        self.calls += 1
        raise RuntimeError("serial link dropped mid-check")


def _compiled_ok(**overrides) -> CompileOutcome:
    defaults = dict(exit_code=0, stdout="Sketch uses 42 bytes.\n", stderr="", duration_seconds=1.0)
    defaults.update(overrides)
    return CompileOutcome.ok(**defaults)


def _compiled_failed(**overrides) -> CompileOutcome:
    defaults = dict(
        category=CompileFailureCategory.COMPILER_ERROR,
        exit_code=1,
        stdout="",
        stderr="main.ino:5: error: expected ';'\n",
        duration_seconds=0.4,
    )
    defaults.update(overrides)
    return CompileOutcome.failed(**defaults)


def _flashed_ok(**overrides) -> FlashOutcome:
    defaults = dict(
        exit_code=0,
        stdout="Hash of data verified.\n",
        stderr="",
        duration_seconds=2.0,
        port="COM-FAKE",
    )
    defaults.update(overrides)
    return FlashOutcome.ok(**defaults)


def _flashed_failed(**overrides) -> FlashOutcome:
    defaults = dict(
        category=FlashFailureCategory.UPLOAD_ERROR,
        exit_code=2,
        stdout="",
        stderr="A fatal error occurred: Failed to connect\n",
        duration_seconds=1.2,
    )
    defaults.update(overrides)
    return FlashOutcome.failed(**defaults)


def panel_one_workspace(editable: tuple[str, ...] = ("loop",)) -> BuildWorkspace:
    """Panel 1's REAL firmware, materialized exactly as B2 materializes it.

    `editable` is supplied here because B2 deliberately declares nothing
    editable (only the remediation phase may make that decision), and a test
    about applying a student edit needs a region a student could own. It
    changes nothing about how the project is read.
    """
    return BuildWorkspace(
        load_sketch_project(
            PANEL_SKETCH,
            project_id="smart-home-mqtt-control-firmware",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="Smart Home MQTT Control System",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            editable_section_ids=editable,
            security_region_id="loop" if "loop" in editable else None,
        )
    )


def delay_statement(milliseconds: int) -> OperationStatement:
    """One authored `delay(...)` — a block a student could have dragged in."""
    return OperationStatement(
        operation=default_semantic_operations.operation(TIME_DELAY),
        arguments=(
            SemanticArgument("MS", LiteralValue(milliseconds, SemanticType.NUMBER)),
        ),
    )


def with_statement_in(program, section_id: str, statement):
    """`program` with one extra statement appended to a named section."""
    section = next(s for s in program.sections if s.section_id == section_id)
    edited = replace(section, statements=section.statements + (statement,))
    return replace(
        program,
        sections=tuple(edited if s is section else s for s in program.sections),
    )


async def compiled_session(
    compiler: FakeCompilerAdapter,
    flasher: FakeFlasherAdapter | None = None,
    *,
    workspace: BuildWorkspace | None = None,
    validation: ValidationPlan | None = None,
) -> tuple[BuildSession, BuildService]:
    """A started session whose workspace has been compiled once."""
    session = BuildSession(
        session_id="b7",
        **({} if workspace is None else {"workspace": workspace}),
        **({} if validation is None else {"validation": validation}),
    )
    service = BuildService(compiler=compiler, flasher=flasher or FakeFlasherAdapter())
    await service.start_session(session)
    await service.compile_workspace(session)
    return session, service


async def flashed_session(
    validation: ValidationPlan | None = None,
    *,
    flash_outcome: FlashOutcome | None = None,
) -> tuple[BuildSession, BuildService]:
    """A session that has compiled and flashed successfully — validation's gate."""
    flasher = FakeFlasherAdapter(outcome=flash_outcome)
    session, service = await compiled_session(
        FakeCompilerAdapter(), flasher, validation=validation
    )
    await service.flash_workspace(session)
    return session, service


# =============================================================================
# 1. Source of truth — B6 output is what the compiler receives
# =============================================================================


def test_an_unchanged_project_compiles_its_existing_source() -> None:
    compiler = FakeCompilerAdapter()

    async def scenario() -> None:
        session, _ = await compiled_session(compiler)
        assert compiler.primary_source == session.workspace.full_source("main.ino")

    run(scenario())


def test_generated_b6_source_is_what_reaches_the_compiler() -> None:
    """A semantic edit's generated C++ — not the original firmware text."""
    compiler = FakeCompilerAdapter()
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path

    async def scenario() -> None:
        program = with_statement_in(workspace.program(path), "loop", delay_statement(4321))
        workspace.apply_program(path, program)
        await compiled_session(compiler, workspace=workspace)
        compiled = compiler.primary_source
        assert "delay(4321);" in compiled
        assert compiled == workspace.full_source(path)
        # And it says exactly the submitted program: B6-generated, not a patched
        # copy of the original. (Layout of UNTOUCHED sections is deliberately the
        # original's, not B6's - see tests/test_build_format_preservation.py - so
        # equality is checked on the program the text expresses.)
        assert source_for_program(program_for_source(compiled)) == source_for_program(program)

    run(scenario())


def test_no_stale_original_source_is_compiled_after_an_edit() -> None:
    """The pre-edit text must not exist anywhere the compiler can reach."""
    compiler = FakeCompilerAdapter()
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path
    original = workspace.full_source(path)

    async def scenario() -> None:
        program = with_statement_in(workspace.program(path), "loop", delay_statement(77))
        workspace.apply_program(path, program)
        await compiled_session(compiler, workspace=workspace)
        assert compiler.primary_source != original
        assert "delay(77);" in compiler.primary_source

    run(scenario())


def test_a_second_compile_after_a_second_edit_sees_the_second_edit() -> None:
    compiler = FakeCompilerAdapter()
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path

    async def scenario() -> None:
        session, service = await compiled_session(compiler, workspace=workspace)
        workspace.apply_program(
            path, with_statement_in(workspace.program(path), "loop", delay_statement(1))
        )
        await service.compile_workspace(session)
        workspace.apply_program(
            path, with_statement_in(workspace.program(path), "loop", delay_statement(2))
        )
        await service.compile_workspace(session)
        assert "delay(1);" in compiler.sources[1][path]
        assert "delay(2);" in compiler.sources[2][path]

    run(scenario())


def test_an_applied_program_invalidates_a_green_build_for_flashing() -> None:
    """The existing compile-to-flash integrity gate covers semantic edits too."""
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path

    async def scenario() -> None:
        session, service = await compiled_session(FakeCompilerAdapter(), workspace=workspace)
        assert session.flash_ready is True
        workspace.apply_program(
            path, with_statement_in(workspace.program(path), "loop", delay_statement(5))
        )
        assert session.flash_ready is False
        result = await service.flash_workspace(session)
        assert result.success is False
        assert "compile again" in (result.error or "")

    run(scenario())


def test_the_compiler_module_knows_nothing_about_the_semantic_layer() -> None:
    """Compiler stays a generic Arduino compiler — B7 adds no Blockly to it."""
    import ast

    source = (Path(__file__).resolve().parents[1] / "app" / "build" / "compiler.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = ("semantic", "blockly", "program_source", "validation", "panels")
    assert not [name for name in imported for bad in banned if bad in name]


# =============================================================================
# 2. Applying a program never widens what a student may change
# =============================================================================


def test_a_program_cannot_rewrite_a_locked_region() -> None:
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path
    before = workspace.fingerprint()
    program = with_statement_in(workspace.program(path), "setup", delay_statement(3))
    with pytest.raises(LockedRegionChangedError):
        apply_program_to_file(workspace.project.files[0], program)
    assert workspace.fingerprint() == before


def test_a_rejected_apply_leaves_the_workspace_untouched() -> None:
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path
    before = workspace.full_source(path)
    program = with_statement_in(workspace.program(path), "setup", delay_statement(3))
    with pytest.raises(Exception):
        workspace.apply_program(path, program)
    assert workspace.full_source(path) == before


def test_a_program_cannot_add_or_remove_a_section() -> None:
    workspace = panel_one_workspace()
    program = workspace.program(workspace.project.files[0].path)
    without_setup = replace(
        program, sections=tuple(s for s in program.sections if s.section_id != "setup")
    )
    with pytest.raises(ProgramStructureChangedError):
        apply_program_to_file(workspace.project.files[0], without_setup)


def test_a_hand_authored_project_is_refused_rather_than_re_sectioned() -> None:
    """Blink names its regions by hand, so a program cannot address them."""
    project = create_blink_project()
    source = project.files[0].render()
    with pytest.raises(UnstructuredFileError):
        apply_program_to_file(project.files[0], program_for_source(source))


def test_regenerating_an_unedited_file_changes_only_layout() -> None:
    """The baseline the locked check compares against is a fixpoint."""
    workspace = panel_one_workspace()
    once = regenerated(workspace.project.files[0])
    twice = regenerated(once)
    assert twice.render() == once.render()
    assert [s.region_id for s in once.segments] == [
        s.region_id for s in workspace.project.files[0].segments
    ]


def test_an_edit_applied_through_the_ir_still_reconstructs_its_file() -> None:
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path
    workspace.apply_program(
        path, with_statement_in(workspace.program(path), "loop", delay_statement(9))
    )
    firmware_file = workspace.project.files[0]
    assert firmware_file.render() == "".join(s.text for s in firmware_file.segments)


# =============================================================================
# 3. Compile lifecycle and its evidence
# =============================================================================


def test_compile_success_records_a_successful_attempt() -> None:
    async def scenario() -> None:
        session, _ = await compiled_session(FakeCompilerAdapter())
        assert session.compile_status is CompileStatus.SUCCEEDED
        attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.COMPILE
        ]
        assert [a.success for a in attempts] == [True]

    run(scenario())


def test_compile_failure_records_a_failed_attempt_and_no_artifact() -> None:
    async def scenario() -> None:
        session, _ = await compiled_session(FakeCompilerAdapter(_compiled_failed()))
        assert session.compile_status is CompileStatus.FAILED
        assert session.compiled_artifact is None
        attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.COMPILE
        ]
        assert [a.success for a in attempts] == [False]

    run(scenario())


def test_flash_is_blocked_after_a_failed_compile() -> None:
    flasher = FakeFlasherAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(FakeCompilerAdapter(_compiled_failed()), flasher)
        result = await service.flash_workspace(session)
        assert result.success is False
        assert session.flash_status is FlashStatus.NOT_STARTED
        assert flasher.flash_requests == []
        assert not [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.FLASH
        ]

    run(scenario())


# =============================================================================
# 4. Flash lifecycle
# =============================================================================


def test_flash_success_uploads_the_retained_artifact_to_the_detected_port() -> None:
    flasher = FakeFlasherAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(FakeCompilerAdapter(), flasher)
        artifact = session.compiled_artifact
        await service.flash_workspace(session)
        assert session.flash_status is FlashStatus.SUCCEEDED
        request = flasher.flash_requests[0]
        # The port comes from the backend's own discovery, never a constant.
        assert request.port == flasher.devices[0].port
        assert request.sketch_dir == artifact.sketch_dir
        assert request.build_path == artifact.build_path

    run(scenario())


def test_flash_failure_is_recorded_and_leaves_validation_untouched() -> None:
    async def scenario() -> None:
        session, service = await flashed_session(flash_outcome=_flashed_failed())
        assert session.flash_status is FlashStatus.FAILED
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert session.validation_result is None
        flashes = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.FLASH
        ]
        assert [a.success for a in flashes] == [False]

    run(scenario())


def test_no_serial_port_is_hardcoded_anywhere_on_the_build_path() -> None:
    """No COM3, no `/dev/tty...` baked into the modules that flash.

    Checked against real string LITERALS rather than raw lines: a docstring
    or comment naming `COM7` as an example is documentation, while a literal
    in executable code would be a port this backend chose for itself instead
    of taking the one discovery returned. The Raspberry Pi the trainer
    deploys to enumerates the same board differently from this dev host, so
    any such constant would be wrong there.
    """
    import ast
    import re

    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"COM\d|/dev/tty")
    offenders = []
    for path in [
        app_dir / "build" / "flasher.py",
        app_dir / "build" / "service.py",
        app_dir / "build" / "compiler.py",
        app_dir / "build_sessions.py",
        app_dir / "build_websocket.py",
        app_dir / "build_validation_selection.py",
        *(app_dir / "build" / "validation").glob("*.py"),
    ]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and pattern.search(node.value)
            ):
                offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert offenders == [], offenders


# =============================================================================
# 5. Validation lifecycle — the B7 addition
# =============================================================================


def plan_with(result: ValidationResult, unavailable: str | None = None) -> ValidationPlan:
    return ValidationPlan(strategy=RecordingValidator(result, unavailable))


def test_validation_success_moves_the_status_and_records_evidence() -> None:
    plan = plan_with(ValidationResult.success("unauthorized command rejected"))

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        result = await service.validate_workspace(session)
        assert result.success is True
        assert session.validation_status is ValidationStatus.SUCCEEDED
        assert session.validation_result.outcome is ValidationOutcome.SUCCESS
        assert [e.type for e in result.events] == [
            BuildEventType.VALIDATION_STARTED,
            BuildEventType.VALIDATION_SUCCEEDED,
        ]
        rows = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert [a.success for a in rows] == [True]

    run(scenario())


def test_validation_failure_stays_a_failure_after_a_green_compile_and_flash() -> None:
    plan = plan_with(ValidationResult.failure("the forged command was still obeyed"))

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        assert session.compile_status is CompileStatus.SUCCEEDED
        assert session.flash_status is FlashStatus.SUCCEEDED
        result = await service.validate_workspace(session)
        assert result.success is True  # the ACTION succeeded
        assert session.validation_status is ValidationStatus.FAILED  # the FIX did not
        assert session.validation_result.succeeded is False
        assert result.events[-1].type is BuildEventType.VALIDATION_FAILED

    run(scenario())


def test_validation_is_never_inferred_from_flash_success() -> None:
    """A successful flash on its own leaves validation exactly where it was."""
    plan = plan_with(ValidationResult.success())

    async def scenario() -> None:
        session, _ = await flashed_session(plan)
        assert session.flash_status is FlashStatus.SUCCEEDED
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert session.validation_result is None
        assert plan.strategy.calls == 0
        assert not [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ]

    run(scenario())


def test_validation_does_not_run_after_a_failed_flash() -> None:
    plan = plan_with(ValidationResult.success("would have passed"))

    async def scenario() -> None:
        session, service = await flashed_session(plan, flash_outcome=_flashed_failed())
        result = await service.validate_workspace(session)
        assert result.success is False
        assert plan.strategy.calls == 0
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert not [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ]

    run(scenario())


def test_validation_does_not_run_without_any_flash() -> None:
    plan = plan_with(ValidationResult.success())

    async def scenario() -> None:
        session, service = await compiled_session(FakeCompilerAdapter(), validation=plan)
        result = await service.validate_workspace(session)
        assert result.success is False
        assert "flash" in (result.error or "")
        assert plan.strategy.calls == 0

    run(scenario())


def test_validation_does_not_run_when_the_workspace_moved_past_the_flash() -> None:
    plan = plan_with(ValidationResult.success())
    workspace = panel_one_workspace()
    path = workspace.project.files[0].path

    async def scenario() -> None:
        flasher = FakeFlasherAdapter()
        session, service = await compiled_session(
            FakeCompilerAdapter(), flasher, workspace=workspace, validation=plan
        )
        await service.flash_workspace(session)
        assert session.flash_status is FlashStatus.SUCCEEDED
        workspace.apply_program(
            path, with_statement_in(workspace.program(path), "loop", delay_statement(11))
        )
        result = await service.validate_workspace(session)
        assert result.success is False
        assert plan.strategy.calls == 0

    run(scenario())


def test_a_validator_that_declines_records_no_attempt_at_all() -> None:
    plan = plan_with(ValidationResult.success(), unavailable="no check exists for this panel")

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        result = await service.validate_workspace(session)
        assert result.success is False
        assert result.error == "no check exists for this panel"
        assert plan.strategy.calls == 0
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert session.events[-1].type is not BuildEventType.VALIDATION_STARTED
        assert not [
            a
            for a in session.recorder.attempts
            if a.attempt_type is BuildAttemptType.VALIDATION
        ]

    run(scenario())


def test_a_validator_that_raises_becomes_an_error_not_a_failed_fix() -> None:
    plan = ValidationPlan(strategy=ExplodingValidator())

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        result = await service.validate_workspace(session)
        assert result.success is True
        assert plan.strategy.calls == 1
        assert session.validation_result.outcome is ValidationOutcome.ERROR
        assert session.validation_status is ValidationStatus.FAILED
        rows = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert [a.success for a in rows] == [False]
        assert rows[0].detail.startswith("error:")

    run(scenario())


def test_a_second_validation_is_refused_while_one_is_running() -> None:
    class SlowValidator:
        def __init__(self) -> None:
            self.calls = 0

        def unavailable_reason(self, context) -> str | None:
            return None

        async def validate(self, context) -> ValidationResult:
            self.calls += 1
            await asyncio.sleep(0.02)
            return ValidationResult.success()

    strategy = SlowValidator()

    async def scenario() -> None:
        session, service = await flashed_session(ValidationPlan(strategy=strategy))
        first, second = await asyncio.gather(
            service.validate_workspace(session), service.validate_workspace(session)
        )
        assert sorted([first.success, second.success]) == [False, True]
        assert strategy.calls == 1

    run(scenario())


def test_the_validator_is_told_which_build_it_is_judging() -> None:
    plan = plan_with(ValidationResult.success())
    workspace = panel_one_workspace()

    async def scenario() -> None:
        flasher = FakeFlasherAdapter()
        session, service = await compiled_session(
            FakeCompilerAdapter(), flasher, workspace=workspace, validation=plan
        )
        await service.flash_workspace(session)
        await service.validate_workspace(session)
        context = plan.strategy.contexts[0]
        assert context.session_id == session.session_id
        assert context.scenario_id == PANEL_ONE
        assert context.board_fqbn == "esp32:esp32:esp32"
        assert context.firmware_fingerprint == session.compiled_artifact.fingerprint
        assert context.flashed_port == flasher.devices[0].port

    run(scenario())


def test_a_validation_result_is_serialisable_for_the_state_frame() -> None:
    plan = plan_with(ValidationResult.failure("still obeyed", observed="START accepted"))

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        await service.validate_workspace(session)
        block = session.snapshot()["validation_output"]
        assert block["outcome"] == "failure"
        assert block["message"] == "still obeyed"
        assert block["details"] == {"observed": "START accepted"}
        assert isinstance(block["occurred_at"], str)

    run(scenario())


def test_the_state_snapshot_carries_no_validation_output_before_one_runs() -> None:
    assert BuildSession(session_id="fresh").snapshot()["validation_output"] is None


# =============================================================================
# 6. Session state transitions are never fabricated
# =============================================================================


def test_the_full_pipeline_moves_each_status_exactly_once() -> None:
    plan = plan_with(ValidationResult.success("authorization enforced"))

    async def scenario() -> None:
        session = BuildSession(session_id="pipeline", validation=plan)
        service = BuildService(compiler=FakeCompilerAdapter(), flasher=FakeFlasherAdapter())
        await service.start_session(session)
        assert (
            session.compile_status,
            session.flash_status,
            session.validation_status,
        ) == (CompileStatus.NOT_STARTED, FlashStatus.NOT_STARTED, ValidationStatus.NOT_STARTED)

        await service.compile_workspace(session)
        assert session.compile_status is CompileStatus.SUCCEEDED
        assert session.flash_status is FlashStatus.NOT_STARTED
        assert session.validation_status is ValidationStatus.NOT_STARTED

        await service.flash_workspace(session)
        assert session.flash_status is FlashStatus.SUCCEEDED
        assert session.validation_status is ValidationStatus.NOT_STARTED

        await service.validate_workspace(session)
        assert session.validation_status is ValidationStatus.SUCCEEDED

        assert [e.type for e in session.events] == [
            BuildEventType.BUILD_SESSION_STARTED,
            BuildEventType.WORKSPACE_LOADED,
            BuildEventType.COMPILE_STARTED,
            BuildEventType.COMPILE_SUCCEEDED,
            BuildEventType.FLASH_STARTED,
            BuildEventType.FLASH_SUCCEEDED,
            BuildEventType.VALIDATION_STARTED,
            BuildEventType.VALIDATION_SUCCEEDED,
        ]

    run(scenario())


# =============================================================================
# 7. Metrics observe the attempts the pipeline now produces
# =============================================================================


def test_metrics_observe_real_validation_attempts(isolated_event_store) -> None:
    plan = plan_with(ValidationResult.success("fixed"))

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        await service.validate_workspace(session)
        await service.end_session(session)

        header = isolated_event_store.build_session(session.session_id)
        attempts = isolated_event_store.build_attempts_for_session(session.session_id)
        kinds = [a.attempt_type for a in attempts]
        assert BuildAttemptType.COMPILE in kinds
        assert BuildAttemptType.FLASH in kinds
        assert BuildAttemptType.VALIDATION in kinds

        ttr = compute_ttr(header, attempts)
        assert ttr.status is MetricStatus.COMPUTED
        aid = compute_aid(header, attempts)
        assert aid.status is MetricStatus.COMPUTED

    run(scenario())


def test_a_failed_validation_is_not_a_validated_successful_fix(isolated_event_store) -> None:
    plan = plan_with(ValidationResult.failure("still vulnerable"))

    async def scenario() -> None:
        session, service = await flashed_session(plan)
        await service.validate_workspace(session)
        await service.end_session(session)
        header = isolated_event_store.build_session(session.session_id)
        attempts = isolated_event_store.build_attempts_for_session(session.session_id)
        assert compute_ttr(header, attempts).status is MetricStatus.NOT_APPLICABLE
        # ...but it IS an attempt, so density still counts it.
        assert compute_aid(header, attempts).status is MetricStatus.COMPUTED

    run(scenario())


# =============================================================================
# 8. Panel package integration — the declaration, honestly
# =============================================================================


def test_panel_one_remediation_declaration_becomes_the_engines_spec() -> None:
    package = default_panel_package_loader().load(PANEL_ONE)
    spec = remediation_spec_for(package)
    assert spec is not None
    assert spec.declared is True
    assert spec.vulnerability == package.remediation.vulnerability
    assert spec.validation_requirement == package.remediation.validation_requirement


def test_a_prose_only_panel_resolves_to_a_validator_that_declines_and_says_why() -> None:
    """B7's finding, still true for every panel that declares prose only.

    Panel 1 stopped being one of those in B8 (it declares a criterion now —
    see `tests/test_build_pipeline_b8.py`), so this states the rule against a
    package that is still in that state rather than against Panel 1. The
    behaviour under test — quote the requirement, run nothing — is unchanged.
    """
    package = default_panel_package_loader().load(PANEL_ONE)
    prose_only = replace(
        package,
        scenario=replace(package.scenario, scenario_id="prose-only-experiment"),
        remediation=replace(package.remediation, criterion=None),
    )
    selection = BuildProjectSelection(
        workspace=create_default_workspace(),
        source=BuildProjectSource.PANEL_PACKAGE,
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE,
        package=prose_only,
    )
    plan = select_build_validation(selection)
    assert isinstance(plan.strategy, DeclaredRequirementValidator)
    context = ValidationContext(
        session_id="s",
        project_id="p",
        scenario_id="prose-only-experiment",
        module_id=PANEL_ONE,
        board_fqbn="esp32:esp32:esp32",
        firmware_fingerprint="abc",
        remediation=plan.remediation,
    )
    reason = plan.strategy.unavailable_reason(context)
    assert reason is not None
    assert package.remediation.validation_requirement in reason


def test_a_selection_with_no_package_still_gets_a_plan() -> None:
    selection = BuildProjectSelection(
        workspace=create_default_workspace(),
        source=BuildProjectSource.NONE,
        panel_status=PanelResourceStatus.NOT_CONNECTED,
    )
    plan = select_build_validation(selection)
    assert isinstance(plan.strategy, DeclaredRequirementValidator)
    assert plan.remediation is None


def test_package_parameters_reach_the_validator_verbatim() -> None:
    package = default_panel_package_loader().load(PANEL_ONE)
    selection = BuildProjectSelection(
        workspace=create_default_workspace(),
        source=BuildProjectSource.PANEL_PACKAGE,
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE,
        package=package,
    )
    plan = select_build_validation(selection)
    assert dict(plan.parameters) == dict(package.parameters)


def test_the_shipped_registry_holds_only_experiments_with_a_real_check() -> None:
    """B7 shipped an empty table; B8 added Panel 1 and nothing else.

    The invariant this has always protected is unchanged: an experiment with
    no registered validator resolves to the one that declines, rather than
    to some other panel's check.
    """
    assert default_validation_registry.scenario_ids == ("smart-home-mqtt-control",)
    assert isinstance(
        default_validation_registry.create("environmental-monitoring"),
        DeclaredRequirementValidator,
    )
    assert isinstance(
        default_validation_registry.create(None), DeclaredRequirementValidator
    )


def test_a_registered_validator_is_resolved_through_the_same_table() -> None:
    marker = RecordingValidator(ValidationResult.success())
    registry = build_default_validation_registry([(PANEL_ONE, lambda: marker)])
    package = default_panel_package_loader().load(PANEL_ONE)
    selection = BuildProjectSelection(
        workspace=create_default_workspace(),
        source=BuildProjectSource.PANEL_PACKAGE,
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE,
        package=package,
    )
    assert select_build_validation(selection, registry).strategy is marker


def test_the_registry_refuses_a_duplicate_registration() -> None:
    registry = ValidationStrategyRegistry({"x": DeclaredRequirementValidator})
    with pytest.raises(ValueError):
        registry.register("x", DeclaredRequirementValidator)


def test_the_registry_never_builds_a_strategy_from_the_id_itself() -> None:
    """An unregistered id is a plain miss — no import, no lookup by name."""
    registry = build_default_validation_registry()
    assert registry.registered("app.build.validation.strategy") is False
    assert isinstance(
        registry.create("app.build.validation.strategy"), DeclaredRequirementValidator
    )


def test_a_default_plan_is_independent_per_session() -> None:
    assert default_validation_plan().strategy is not default_validation_plan().strategy


# =============================================================================
# 8b. The wire: one new field-less request, and nothing else moved
# =============================================================================


def _drain_until_state(websocket) -> dict:
    """Read frames until the next `state` snapshot, returning it."""
    while True:
        frame = websocket.receive_json()
        if frame["type"] == "state":
            return frame["data"]


def test_the_validate_frame_reaches_the_service_and_is_honestly_refused() -> None:
    """End to end over the real socket, with no board and no validator.

    The refusal is the truthful outcome today: with no hardware attached in
    this test environment, the connection resolves to no active project at
    all (the no-device correction — see `app/build/no_device.py`), so THAT
    gate answers before the flash gate or any validator is ever consulted.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/build") as websocket:
            assert websocket.receive_json()["type"] == "session"
            _drain_until_state(websocket)
            websocket.send_json({"type": "validate"})
            frame = websocket.receive_json()
            assert frame["type"] == "error"
            assert "no panel is connected" in frame["message"]


def test_the_validate_frame_carries_no_fields_a_client_could_assert_with() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/build") as websocket:
            assert websocket.receive_json()["type"] == "session"
            _drain_until_state(websocket)
            websocket.send_json({"type": "validate", "success": True})
            frame = websocket.receive_json()
            assert frame["type"] == "error"
            assert frame["message"] == "message does not match the protocol schema"


def test_the_state_frame_gained_validation_output_and_kept_everything_else() -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.build_messages import BUILD_PROTOCOL_VERSION

    with TestClient(app) as client:
        with client.websocket_connect("/ws/build") as websocket:
            session_frame = websocket.receive_json()
            # B7 introduced version 5; B8's correction moved it to 6 when it
            # added the section -> Blockly frames. What this test is actually
            # about is the STATE SNAPSHOT's shape below, which no version bump
            # since has changed, so it tracks the current version rather than
            # pinning the one B7 happened to ship with.
            assert session_frame["protocol_version"] == BUILD_PROTOCOL_VERSION
            assert BUILD_PROTOCOL_VERSION >= 5
            state = _drain_until_state(websocket)
            for key in (
                "project",
                "files",
                "dirty",
                "compile_status",
                "flash_status",
                "validation_status",
                "flash_ready",
                "hardware",
                "compile_output",
                "flash_output",
                "validation_output",
            ):
                assert key in state, key
            assert state["validation_status"] == "not_started"
            assert state["validation_output"] is None


# =============================================================================
# 9. Dependency boundaries
# =============================================================================


def test_the_validation_package_does_not_import_the_panel_or_hardware_layers() -> None:
    import ast

    package_dir = Path(__file__).resolve().parents[1] / "app" / "build" / "validation"
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
        "fastapi",
    )
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(
            name for name in imported for bad in banned if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_no_new_build_module_spawns_a_process() -> None:
    """`app/build/process.py` stays the one place anything is executed."""
    import ast

    app_dir = Path(__file__).resolve().parents[1] / "app"
    new_modules = [
        app_dir / "build" / "program_source.py",
        app_dir / "build_validation_selection.py",
        *(app_dir / "build" / "validation").glob("*.py"),
    ]
    banned_calls = {"system", "popen", "spawn", "Popen", "eval", "exec", "compile", "__import__"}
    for path in new_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in banned_calls, f"{path.name} calls {name}"
        offenders = sorted(
            name
            for name in imported
            for bad in ("subprocess", "os", "asyncio.subprocess")
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_build_service_contains_no_panel_specific_validation_logic() -> None:
    """The generic engine must not know Panel 1 is MQTT."""
    source = (
        Path(__file__).resolve().parents[1] / "app" / "build" / "service.py"
    ).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # Strip the module docstring before searching, so prose may still explain
    # the architecture while the CODE stays panel-free.
    body = code.split('"""', 2)[-1]
    for term in ("smart-home", "smart_home", "mqtt", "MQTT", "motor", "broker", "panel_id =="):
        assert term not in body, f"build/service.py code mentions {term!r}"


def test_the_program_source_seam_is_pure() -> None:
    import ast

    path = Path(__file__).resolve().parents[1] / "app" / "build" / "program_source.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "dataclasses",
        "app.build.discovery",
        "app.build.document_project",
        "app.build.models",
        "app.build.semantic",
    }


# =============================================================================
# 10. Nothing that already worked stopped working
# =============================================================================


def test_the_default_blink_project_still_compiles_and_flashes() -> None:
    compiler = FakeCompilerAdapter()
    flasher = FakeFlasherAdapter()

    async def scenario() -> None:
        session, service = await compiled_session(compiler, flasher)
        assert session.workspace.project.project_id == "led-blink-poc"
        assert "void loop()" in compiler.primary_source
        await service.flash_workspace(session)
        assert session.flash_status is FlashStatus.SUCCEEDED

    run(scenario())


def test_panel_one_still_resolves_to_its_own_firmware_project() -> None:
    workspace = panel_one_workspace(editable=())
    project = workspace.project
    assert project.files[0].path == "smart_home_mqtt_control.ino"
    assert project.scenario_id == PANEL_ONE
    assert {s.kind.value for s in project.files[0].segments} == {"locked"}


def test_the_phase_2e3_recording_seam_still_works() -> None:
    async def scenario() -> None:
        session = BuildSession(session_id="legacy-seam")
        service = BuildService(compiler=FakeCompilerAdapter(), flasher=FakeFlasherAdapter())
        await service.record_validation_attempt(session, success=True, detail="fixed")
        assert session.validation_status is ValidationStatus.SUCCEEDED
        rows = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert [(a.success, a.detail) for a in rows] == [(True, "fixed")]

    run(scenario())
