"""Phase 3A/3B/3C verification: Build Service + Build Session lifecycle.

Exercises `BuildService` directly against `BuildSession`s (the way the
WebSocket layer drives it): session/workspace state, events produced for
real operations, session isolation, and that repeated/invalid actions are
handled deterministically. Phase 3B's `compile_workspace` orchestration is
exercised against a `FakeCompilerAdapter` — no real subprocess, no
dependency on arduino-cli being installed — since `test_build_compiler.py`
already covers the real `ArduinoCliCompiler`'s own behaviour. Phase 3C's
`flash_workspace` is exercised the same way against a `FakeFlasherAdapter`
- no real subprocess and, importantly, no physical ESP32 - because what
belongs here is the orchestration (compile-to-flash integrity, device
situations, state, events, isolation), not the toolchain behaviour
`test_build_flasher.py` covers.
"""

from __future__ import annotations

import asyncio

import pytest

from app.build import (
    BLINK_REGION_ID,
    BuildEventType,
    BuildWorkspace,
    create_environmental_monitoring_project,
)
from app.build.compiler import CompileFailureCategory, CompileOutcome, CompileRequest
from app.build.flasher import (
    DeviceDetectOutcome,
    DeviceDetectRequest,
    FlashFailureCategory,
    FlashOutcome,
    FlashRequest,
    SerialDevice,
)
from app.build.models import CompileStatus, FlashStatus, HardwareStatus
from app.build.service import BuildService
from app.build_sessions import BuildSession, BuildSessionManager


class FakeCompilerAdapter:
    """A `CompilerAdapter` test double: returns a fixed outcome, no subprocess.

    `on_run_compile`, if given, is called with the `CompileRequest` before
    the fixed outcome is returned — tests use it to observe state (e.g.
    `session.compile_status`) at the exact moment the "compiler" is running.
    """

    def __init__(self, outcome: CompileOutcome, on_run_compile=None) -> None:
        self.outcome = outcome
        self.on_run_compile = on_run_compile
        self.requests: list[CompileRequest] = []

    async def run_compile(self, request: CompileRequest) -> CompileOutcome:
        self.requests.append(request)
        if self.on_run_compile is not None:
            self.on_run_compile(request)
        # A real yield point. Without it, two `compile_workspace` coroutines
        # driven through `asyncio.gather` would never actually interleave —
        # the first would run to completion (nothing else in the method
        # awaits) before the second ever started, defeating the point of
        # `test_two_concurrent_compile_calls_only_one_compiler_invocation`.
        await asyncio.sleep(0)
        return self.outcome


def success_outcome(**overrides) -> CompileOutcome:
    defaults = dict(exit_code=0, stdout="Sketch uses 42 bytes.\n", stderr="", duration_seconds=1.5)
    defaults.update(overrides)
    return CompileOutcome.ok(**defaults)


def failure_outcome(**overrides) -> CompileOutcome:
    defaults = dict(
        category=CompileFailureCategory.COMPILER_ERROR,
        exit_code=1,
        stdout="",
        stderr="main.ino:5: error: expected ';'\n",
        duration_seconds=0.8,
    )
    defaults.update(overrides)
    return CompileOutcome.failed(**defaults)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def service() -> BuildService:
    return BuildService()


@pytest.fixture
def session() -> BuildSession:
    return BuildSession(session_id="build-test")


# --- 1: build session/workspace state foundation ----------------------------


def test_fresh_session_has_a_loaded_workspace_and_default_statuses(session: BuildSession) -> None:
    assert session.workspace.project.scenario_id == "led-blink-poc"
    assert session.started is False
    assert session.dirty is False
    assert session.compile_status.value == "not_started"
    assert session.flash_status.value == "not_started"
    assert session.validation_status.value == "not_started"
    assert session.events == []


# --- 9: build events are generated for actual workspace/edit operations ----


def test_start_session_emits_started_then_loaded(service: BuildService, session: BuildSession) -> None:
    result = run(service.start_session(session))
    assert result.success
    assert [e.type for e in result.events] == [
        BuildEventType.BUILD_SESSION_STARTED,
        BuildEventType.WORKSPACE_LOADED,
    ]
    assert session.started is True
    assert session.events == list(result.events)


def test_start_session_is_idempotent(service: BuildService, session: BuildSession) -> None:
    run(service.start_session(session))
    result = run(service.start_session(session))
    assert result.success
    assert result.events == ()
    # No duplicate events were appended to the log.
    assert len(session.events) == 2


def test_end_session_emits_ended(service: BuildService, session: BuildSession) -> None:
    result = run(service.end_session(session))
    assert result.success
    assert [e.type for e in result.events] == [BuildEventType.BUILD_SESSION_ENDED]
    assert session.events[-1].type is BuildEventType.BUILD_SESSION_ENDED


# --- 5: editing the security region succeeds, with the right events --------


def test_edit_region_succeeds_and_emits_code_and_security_events(
    service: BuildService, session: BuildSession
) -> None:
    result = run(
        service.edit_region(session, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
    )
    assert result.success
    assert [e.type for e in result.events] == [
        BuildEventType.CODE_EDITED,
        BuildEventType.SECURITY_REGION_EDITED,
    ]
    assert session.dirty is True
    assert session.workspace.region_source("main.ino", BLINK_REGION_ID) == (
        "digitalWrite(2, LOW); // edited"
    )


def test_edit_region_events_carry_path_and_region_id(
    service: BuildService, session: BuildSession
) -> None:
    result = run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "x"))
    for event in result.events:
        assert event.data["path"] == "main.ino"
        assert event.data["region_id"] == BLINK_REGION_ID


# --- 6 & 7: locked / unknown region edits are rejected, without side effects


def test_edit_region_rejects_locked_region(service: BuildService) -> None:
    """Exercised against the Environmental Monitoring project, which still
    has a real locked region — the Phase 1 LED Blink POC project the
    default `session` fixture loads is temporarily fully editable and has
    no locked region at all right now (see `app/build/blink.py`)."""
    session = BuildSession(
        session_id="locked-region-test",
        workspace=BuildWorkspace(create_environmental_monitoring_project()),
    )
    before = session.workspace.full_source("main.ino")

    result = run(service.edit_region(session, "main.ino", "locked_pre", "malicious"))

    assert result.success is False
    assert result.error
    assert "locked" in result.error
    assert session.dirty is False
    assert session.events == []
    assert session.workspace.full_source("main.ino") == before


def test_edit_region_rejects_unknown_region(service: BuildService, session: BuildSession) -> None:
    result = run(service.edit_region(session, "main.ino", "not_a_region", "x"))
    assert result.success is False
    assert session.dirty is False
    assert session.events == []


def test_edit_region_rejects_unknown_file(service: BuildService, session: BuildSession) -> None:
    result = run(service.edit_region(session, "nope.ino", BLINK_REGION_ID, "x"))
    assert result.success is False
    assert session.dirty is False


# --- 8: build session state is isolated between sessions -------------------


def test_two_sessions_have_isolated_workspaces_and_event_logs(service: BuildService) -> None:
    a = BuildSession(session_id="a")
    b = BuildSession(session_id="b")
    run(service.start_session(a))
    run(service.edit_region(a, "main.ino", BLINK_REGION_ID, "edited by a"))

    assert b.dirty is False
    assert b.events == []
    assert b.workspace.region_source("main.ino", BLINK_REGION_ID) != "edited by a"
    assert "pinMode(2, OUTPUT)" in b.workspace.region_source("main.ino", BLINK_REGION_ID)


def test_session_manager_lifecycle_matches_hack_mode_pattern() -> None:
    async def scenario() -> None:
        manager = BuildSessionManager()
        session = await manager.create()
        assert await manager.get(session.session_id) is session
        assert await manager.count() == 1
        assert await manager.remove(session.session_id) is session
        assert await manager.get(session.session_id) is None
        assert await manager.remove(session.session_id) is None  # idempotent
        assert await manager.count() == 0

    asyncio.run(scenario())


# --- repeated valid edits do not corrupt state ------------------------------


def test_repeated_edits_are_stable(service: BuildService, session: BuildSession) -> None:
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "a"))
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "b"))
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "c"))
    assert session.workspace.region_source("main.ino", BLINK_REGION_ID) == "c"
    assert len(session.events) == 6  # 2 events per successful edit


# --- session snapshot shape --------------------------------------------------


def test_session_snapshot_includes_dirty_and_statuses(
    service: BuildService, session: BuildSession
) -> None:
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "x"))
    snap = session.snapshot()
    assert snap["dirty"] is True
    assert snap["compile_status"] == "not_started"
    assert snap["flash_status"] == "not_started"
    assert snap["validation_status"] == "not_started"
    assert "project" in snap and "files" in snap


# --- Phase 3B: compile_workspace orchestration ------------------------------

# --- 8 & 9: compile starts; state becomes compiling -------------------------


def test_compile_marks_session_running_while_the_compiler_is_called(
    session: BuildSession,
) -> None:
    observed = {}

    def capture(_request: CompileRequest) -> None:
        observed["status_during_call"] = session.compile_status

    fake = FakeCompilerAdapter(success_outcome(), on_run_compile=capture)
    service = BuildService(compiler=fake)

    run(service.compile_workspace(session))

    assert observed["status_during_call"] is CompileStatus.RUNNING
    assert len(fake.requests) == 1


# --- 10: successful compilation updates state correctly ---------------------


def test_successful_compile_updates_status_and_events(session: BuildSession) -> None:
    fake = FakeCompilerAdapter(success_outcome(exit_code=0, duration_seconds=12.3))
    service = BuildService(compiler=fake)

    result = run(service.compile_workspace(session))

    assert result.success is True
    assert [e.type for e in result.events] == [
        BuildEventType.COMPILE_STARTED,
        BuildEventType.COMPILE_SUCCEEDED,
    ]
    assert session.compile_status is CompileStatus.SUCCEEDED
    assert session.compile_output.success is True
    assert session.compile_output.exit_code == 0


def test_successful_compile_event_carries_exit_code_and_duration(
    session: BuildSession,
) -> None:
    fake = FakeCompilerAdapter(success_outcome(exit_code=0, duration_seconds=12.3))
    service = BuildService(compiler=fake)

    result = run(service.compile_workspace(session))
    succeeded = result.events[-1]
    assert succeeded.data["exit_code"] == 0
    assert succeeded.data["duration_seconds"] == pytest.approx(12.3)


# --- 11: failed compilation updates state correctly -------------------------


def test_failed_compile_updates_status_and_events(session: BuildSession) -> None:
    fake = FakeCompilerAdapter(failure_outcome(exit_code=1))
    service = BuildService(compiler=fake)

    result = run(service.compile_workspace(session))

    assert result.success is True  # the ACTION succeeded: it ran and reported truthfully
    assert [e.type for e in result.events] == [
        BuildEventType.COMPILE_STARTED,
        BuildEventType.COMPILE_FAILED,
    ]
    assert session.compile_status is CompileStatus.FAILED
    assert session.compile_output.success is False
    assert session.compile_output.exit_code == 1


def test_failed_compile_event_carries_category(session: BuildSession) -> None:
    fake = FakeCompilerAdapter(failure_outcome(category=CompileFailureCategory.TIMEOUT))
    service = BuildService(compiler=fake)

    result = run(service.compile_workspace(session))
    failed = result.events[-1]
    assert failed.data["category"] == "timeout"


# --- 12 & 13: events + compiler output are exposed correctly ---------------


def test_compile_output_appears_in_session_snapshot(session: BuildSession) -> None:
    fake = FakeCompilerAdapter(
        success_outcome(stdout="all good\n", stderr="", exit_code=0, duration_seconds=3.0)
    )
    service = BuildService(compiler=fake)

    run(service.compile_workspace(session))
    snap = session.snapshot()

    assert snap["compile_status"] == "succeeded"
    assert snap["compile_output"]["success"] is True
    assert snap["compile_output"]["stdout"] == "all good\n"
    assert snap["compile_output"]["exit_code"] == 0


def test_compile_output_is_none_before_any_compile(session: BuildSession) -> None:
    assert session.snapshot()["compile_output"] is None


# --- 14 & 15: a failed compile does not corrupt the workspace; retry works -


def test_failed_compile_leaves_workspace_source_intact(session: BuildSession) -> None:
    before = session.workspace.full_source("main.ino")
    fake = FakeCompilerAdapter(failure_outcome())
    service = BuildService(compiler=fake)

    run(service.compile_workspace(session))

    assert session.workspace.full_source("main.ino") == before


def test_student_can_edit_and_recompile_after_a_failure(session: BuildSession) -> None:
    failing = BuildService(compiler=FakeCompilerAdapter(failure_outcome()))
    first = run(failing.compile_workspace(session))
    assert session.compile_status is CompileStatus.FAILED
    assert first.success is True

    edit_result = run(
        failing.edit_region(session, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
    )
    assert edit_result.success is True

    succeeding = BuildService(compiler=FakeCompilerAdapter(success_outcome()))
    second = run(succeeding.compile_workspace(session))
    assert second.success is True
    assert session.compile_status is CompileStatus.SUCCEEDED


# --- 16: concurrent compilation is prevented --------------------------------


def test_concurrent_compile_request_is_rejected(session: BuildSession) -> None:
    session.compile_status = CompileStatus.RUNNING
    fake = FakeCompilerAdapter(success_outcome())
    service = BuildService(compiler=fake)

    result = run(service.compile_workspace(session))

    assert result.success is False
    assert result.error
    assert fake.requests == []  # no process was spawned for the rejected request


def test_two_concurrent_compile_calls_only_one_compiler_invocation() -> None:
    session = BuildSession(session_id="concurrent-test")
    fake = FakeCompilerAdapter(success_outcome())
    service = BuildService(compiler=fake)

    async def scenario() -> tuple:
        return await asyncio.gather(
            service.compile_workspace(session), service.compile_workspace(session)
        )

    first, second = asyncio.run(scenario())
    # Exactly one of the two concurrent calls actually compiled; the other
    # was rejected outright (a failed *request*, not a failed compile), and
    # only one subprocess-equivalent call happened.
    assert len(fake.requests) == 1
    successes = [first.success, second.success]
    assert successes.count(True) == 1
    assert successes.count(False) == 1
    rejected = first if not first.success else second
    winner = second if not first.success else first
    assert rejected.error == "a compilation is already running for this session"
    assert [e.type for e in winner.events] == [
        BuildEventType.COMPILE_STARTED,
        BuildEventType.COMPILE_SUCCEEDED,
    ]


# --- 17: session isolation holds for compilation too ------------------------


def test_compiling_one_session_does_not_affect_another() -> None:
    a = BuildSession(session_id="a")
    b = BuildSession(session_id="b")
    service = BuildService(compiler=FakeCompilerAdapter(success_outcome()))

    run(service.compile_workspace(a))

    assert a.compile_status is CompileStatus.SUCCEEDED
    assert b.compile_status is CompileStatus.NOT_STARTED
    assert b.compile_output is None


# --- Phase 3C: flash_workspace orchestration --------------------------------
#
# Driven entirely through a `FakeFlasherAdapter` — no real subprocess, no
# physical ESP32 — since `test_build_flasher.py` already covers the real
# `ArduinoCliFlasher`'s own behaviour. What is verified here is the
# orchestration that only `BuildService` can get wrong: the compile->flash
# precondition, what each device situation does to session state, which
# events come out, and that none of it leaks between sessions.


class FakeFlasherAdapter:
    """A `FlasherAdapter` test double: canned devices and a fixed outcome.

    `devices` is what `detect_devices` reports — pass a `DeviceDetectOutcome`
    instead to simulate discovery itself failing. `outcome` is what
    `run_flash` returns, and `requests` records every `FlashRequest` it was
    given, so a test can assert the upload was pointed at the artifact the
    compile produced rather than at anything else.
    """

    def __init__(self, devices=(), outcome: FlashOutcome | None = None) -> None:
        self.devices = devices
        self.outcome = outcome if outcome is not None else flash_success()
        self.detect_requests: list[DeviceDetectRequest] = []
        self.requests: list[FlashRequest] = []

    async def detect_devices(self, request: DeviceDetectRequest) -> DeviceDetectOutcome:
        self.detect_requests.append(request)
        await asyncio.sleep(0)
        if isinstance(self.devices, DeviceDetectOutcome):
            return self.devices
        return DeviceDetectOutcome(devices=tuple(self.devices))

    async def run_flash(self, request: FlashRequest) -> FlashOutcome:
        self.requests.append(request)
        # A real yield point, for the same reason FakeCompilerAdapter has
        # one: without it two `flash_workspace` coroutines could not
        # actually interleave under `asyncio.gather`.
        await asyncio.sleep(0)
        return self.outcome


def esp32(port: str = "COM7") -> SerialDevice:
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn="esp32:esp32:esp32",
        has_usb_id=True,
    )


def flash_success(**overrides) -> FlashOutcome:
    defaults = dict(
        exit_code=0,
        stdout="Hash of data verified.\nLeaving...\n",
        stderr="",
        duration_seconds=14.2,
        port="COM7",
    )
    defaults.update(overrides)
    return FlashOutcome.ok(**defaults)


def flash_failure(**overrides) -> FlashOutcome:
    defaults = dict(
        exit_code=1,
        stdout="",
        stderr="A fatal error occurred: MD5 of file does not match data in flash!\n",
        duration_seconds=9.0,
        port="COM7",
    )
    defaults.update(overrides)
    category = defaults.pop("category", FlashFailureCategory.UPLOAD_ERROR)
    return FlashOutcome.failed(category, **defaults)


def flashable(session: BuildSession, service: BuildService | None = None) -> BuildService:
    """Put `session` into the state a successful compile leaves behind."""
    service = service or BuildService(compiler=FakeCompilerAdapter(success_outcome()))
    run(service.compile_workspace(session))
    assert session.compile_status is CompileStatus.SUCCEEDED
    assert session.compiled_artifact is not None
    return service


def flash_service(**flasher_kwargs) -> BuildService:
    """A service whose compiler always succeeds and whose flasher is a fake."""
    return BuildService(
        compiler=FakeCompilerAdapter(success_outcome()),
        flasher=FakeFlasherAdapter(**flasher_kwargs),
    )


# --- 13: flashing is rejected when no successful compile exists -------------


def test_flash_is_rejected_before_any_compile(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[esp32()])
    service = BuildService(flasher=fake)

    result = run(service.flash_workspace(session))

    assert result.success is False
    assert result.error
    # Nothing was attempted at all — not even device detection.
    assert fake.detect_requests == []
    assert fake.requests == []
    assert session.flash_status is FlashStatus.NOT_STARTED
    assert session.events == []


def test_flash_is_rejected_after_a_failed_compile(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[esp32()])
    service = BuildService(compiler=FakeCompilerAdapter(failure_outcome()), flasher=fake)
    run(service.compile_workspace(session))

    result = run(service.flash_workspace(session))

    assert result.success is False
    assert session.compiled_artifact is None
    assert fake.requests == []
    assert session.flash_status is FlashStatus.NOT_STARTED


# --- 14: flashing is rejected once the workspace changed after compiling ---


def test_flash_is_rejected_after_the_workspace_changes_post_compile(
    session: BuildSession,
) -> None:
    service = flash_service(devices=[esp32()])
    flashable(session, service)
    assert session.flash_ready is True

    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited"))

    assert session.flash_ready is False
    result = run(service.flash_workspace(session))
    assert result.success is False
    assert "compile again" in result.error
    assert service._flasher.requests == []


def test_flash_becomes_possible_again_after_recompiling(session: BuildSession) -> None:
    service = flash_service(devices=[esp32()])
    flashable(session, service)
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited"))
    run(service.compile_workspace(session))

    assert session.flash_ready is True
    result = run(service.flash_workspace(session))
    assert result.success is True
    assert session.flash_status is FlashStatus.SUCCEEDED


def test_an_edit_that_is_undone_still_matches_the_compiled_build(
    session: BuildSession,
) -> None:
    """The integrity check is a content hash, not an 'edited since' flag."""
    original = session.workspace.region_source("main.ino", BLINK_REGION_ID)
    service = flashable(session)
    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "something else"))
    assert session.flash_ready is False

    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, original))

    assert session.flash_ready is True


# --- 15 & 16: a successful flash runs and updates state --------------------


def test_successful_flash_updates_status_and_events(session: BuildSession) -> None:
    service = flash_service(devices=[esp32()], outcome=flash_success())
    flashable(session, service)

    result = run(service.flash_workspace(session))

    assert result.success is True
    assert [e.type for e in result.events] == [
        BuildEventType.FLASH_STARTED,
        BuildEventType.FLASH_SUCCEEDED,
    ]
    assert session.flash_status is FlashStatus.SUCCEEDED
    assert session.flash_output.success is True
    assert session.flash_output.port == "COM7"
    # Compilation state is untouched by flashing: the two are independent.
    assert session.compile_status is CompileStatus.SUCCEEDED


def test_flash_uploads_the_artifact_the_compile_produced(session: BuildSession) -> None:
    """The upload is pointed at this session's own built output, nothing else."""
    service = flash_service(devices=[esp32()])
    flashable(session, service)
    artifact = session.compiled_artifact

    run(service.flash_workspace(session))

    fake = service._flasher
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.build_path == artifact.build_path
    assert request.sketch_dir == artifact.sketch_dir
    # The board target comes from the project's own BoardInfo.
    assert request.fqbn == session.workspace.project.board.fqbn
    assert request.port == "COM7"
    # And detection was asked about that same board.
    assert fake.detect_requests[0].fqbn == "esp32:esp32:esp32"


def test_flash_success_event_carries_port_exit_code_and_duration(
    session: BuildSession,
) -> None:
    service = flash_service(
        devices=[esp32("COM12")],
        outcome=flash_success(port="COM12", duration_seconds=17.5),
    )
    flashable(session, service)

    result = run(service.flash_workspace(session))

    succeeded = result.events[-1]
    assert succeeded.data["port"] == "COM12"
    assert succeeded.data["exit_code"] == 0
    assert succeeded.data["duration_seconds"] == pytest.approx(17.5)
    assert succeeded.data["category"] == "none"


# --- 17: a failed flash updates state --------------------------------------


def test_failed_flash_updates_status_and_events(session: BuildSession) -> None:
    service = flash_service(devices=[esp32()], outcome=flash_failure())
    flashable(session, service)

    result = run(service.flash_workspace(session))

    # The ACTION succeeded: it ran and reported truthfully.
    assert result.success is True
    assert [e.type for e in result.events] == [
        BuildEventType.FLASH_STARTED,
        BuildEventType.FLASH_FAILED,
    ]
    assert session.flash_status is FlashStatus.FAILED
    assert session.flash_output.success is False
    assert result.events[-1].data["category"] == "upload_error"


def test_a_device_disconnected_failure_keeps_its_own_category(
    session: BuildSession,
) -> None:
    service = flash_service(
        devices=[esp32()],
        outcome=flash_failure(category=FlashFailureCategory.DEVICE_DISCONNECTED),
    )
    flashable(session, service)

    result = run(service.flash_workspace(session))

    assert session.flash_status is FlashStatus.FAILED
    assert result.events[-1].data["category"] == "device_disconnected"


# --- no device / ambiguous device: their own honest states ------------------


def test_no_connected_device_yields_no_device_not_a_build_failure(
    session: BuildSession,
) -> None:
    service = flash_service(devices=[])
    flashable(session, service)

    result = run(service.flash_workspace(session))

    assert result.success is True
    assert session.flash_status is FlashStatus.NO_DEVICE
    assert session.flash_output.category is FlashFailureCategory.NO_DEVICE
    assert session.flash_output.port is None
    assert result.events[-1].data["category"] == "no_device"
    # No upload was attempted, and compilation is untouched — this is
    # emphatically not a compiler or toolchain problem.
    assert service._flasher.requests == []
    assert session.compile_status is CompileStatus.SUCCEEDED
    assert session.compile_output.success is True


def test_multiple_candidate_devices_are_reported_rather_than_guessed_between(
    session: BuildSession,
) -> None:
    service = flash_service(devices=[esp32("COM7"), esp32("COM9")])
    flashable(session, service)

    result = run(service.flash_workspace(session))

    assert session.flash_status is FlashStatus.FAILED
    assert session.flash_output.category is FlashFailureCategory.AMBIGUOUS_DEVICE
    assert result.events[-1].data["category"] == "ambiguous_device"
    assert result.events[-1].data["device_count"] == 2
    # Critically: nothing was written to either board.
    assert service._flasher.requests == []
    assert "COM7" in session.flash_output.stderr
    assert "COM9" in session.flash_output.stderr


def test_a_failed_device_detection_is_not_reported_as_no_device(
    session: BuildSession,
) -> None:
    service = flash_service(
        devices=DeviceDetectOutcome(
            category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE, stderr="no arduino-cli"
        )
    )
    flashable(session, service)

    run(service.flash_workspace(session))

    assert session.flash_status is FlashStatus.FAILED
    assert session.flash_output.category is FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
    assert service._flasher.requests == []


# --- 18 & 19: flash events and flash output are exposed --------------------


def test_flash_output_appears_in_session_snapshot(session: BuildSession) -> None:
    service = flash_service(
        devices=[esp32()], outcome=flash_success(stdout="Leaving...\n", duration_seconds=11.0)
    )
    flashable(session, service)

    run(service.flash_workspace(session))
    snap = session.snapshot()

    assert snap["flash_status"] == "succeeded"
    assert snap["flash_output"]["success"] is True
    assert snap["flash_output"]["stdout"] == "Leaving...\n"
    assert snap["flash_output"]["port"] == "COM7"
    assert snap["flash_output"]["exit_code"] == 0


def test_flash_output_is_none_and_flash_not_ready_before_any_compile(
    session: BuildSession,
) -> None:
    snap = session.snapshot()
    assert snap["flash_output"] is None
    assert snap["flash_status"] == "not_started"
    assert snap["flash_ready"] is False


def test_flash_ready_is_true_only_between_a_good_compile_and_the_next_edit(
    session: BuildSession,
) -> None:
    service = flashable(session)
    assert session.snapshot()["flash_ready"] is True

    run(service.edit_region(session, "main.ino", BLINK_REGION_ID, "x"))
    assert session.snapshot()["flash_ready"] is False


# --- 20: concurrent flashing is prevented -----------------------------------


def test_concurrent_flash_request_is_rejected(session: BuildSession) -> None:
    flashable(session)
    session.flash_status = FlashStatus.RUNNING
    fake = FakeFlasherAdapter(devices=[esp32()])
    service = BuildService(flasher=fake)

    result = run(service.flash_workspace(session))

    assert result.success is False
    assert result.error
    assert fake.detect_requests == []  # nothing was spawned for the rejected request
    assert fake.requests == []


def test_two_concurrent_flash_calls_only_one_upload() -> None:
    session = BuildSession(session_id="concurrent-flash")
    service = flash_service(devices=[esp32()])
    flashable(session, service)

    async def scenario() -> tuple:
        return await asyncio.gather(
            service.flash_workspace(session), service.flash_workspace(session)
        )

    first, second = asyncio.run(scenario())

    # Exactly one of the two concurrent calls actually flashed; the other
    # was rejected outright, and only one upload process-equivalent ran.
    assert len(service._flasher.requests) == 1
    successes = [first.success, second.success]
    assert successes.count(True) == 1
    assert successes.count(False) == 1
    rejected = first if not first.success else second
    assert rejected.error == "a flash is already running for this session"


def test_compiling_is_refused_while_a_flash_is_in_flight(session: BuildSession) -> None:
    """A new compile would delete the very build directory being uploaded."""
    service = flash_service(devices=[esp32()])
    flashable(session, service)
    session.flash_status = FlashStatus.RUNNING
    before = len(service._compiler.requests)

    result = run(service.compile_workspace(session))

    assert result.success is False
    assert len(service._compiler.requests) == before
    assert session.compiled_artifact is not None  # the artifact survived


# --- 21 & 22: a failed flash does not corrupt the workspace; retry works ---


def test_failed_flash_leaves_workspace_and_artifact_intact(session: BuildSession) -> None:
    service = flash_service(devices=[esp32()], outcome=flash_failure())
    flashable(session, service)
    before = session.workspace.full_source("main.ino")
    artifact = session.compiled_artifact

    run(service.flash_workspace(session))

    assert session.workspace.full_source("main.ino") == before
    assert session.compiled_artifact is artifact
    assert artifact.root.exists()
    assert session.flash_ready is True  # still flashable — retry is allowed


def test_retry_after_a_failed_flash_succeeds(session: BuildSession) -> None:
    service = flash_service(devices=[esp32()], outcome=flash_failure())
    flashable(session, service)
    run(service.flash_workspace(session))
    assert session.flash_status is FlashStatus.FAILED

    retry = BuildService(flasher=FakeFlasherAdapter(devices=[esp32()], outcome=flash_success()))
    result = run(retry.flash_workspace(session))

    assert result.success is True
    assert session.flash_status is FlashStatus.SUCCEEDED


def test_retry_after_a_no_device_result_succeeds_once_a_board_appears(
    session: BuildSession,
) -> None:
    service = flash_service(devices=[])
    flashable(session, service)
    run(service.flash_workspace(session))
    assert session.flash_status is FlashStatus.NO_DEVICE

    plugged_in = BuildService(flasher=FakeFlasherAdapter(devices=[esp32()]))
    run(plugged_in.flash_workspace(session))

    assert session.flash_status is FlashStatus.SUCCEEDED


# --- 23: session isolation holds for flashing too ---------------------------


def test_flashing_one_session_does_not_affect_another() -> None:
    a = BuildSession(session_id="a")
    b = BuildSession(session_id="b")
    service = flash_service(devices=[esp32()])
    flashable(a, service)

    run(service.flash_workspace(a))

    assert a.flash_status is FlashStatus.SUCCEEDED
    assert b.flash_status is FlashStatus.NOT_STARTED
    assert b.flash_output is None
    assert b.compiled_artifact is None
    assert b.flash_ready is False


# --- artifact lifetime ------------------------------------------------------


def test_a_successful_compile_retains_its_build_output_on_disk(
    session: BuildSession,
) -> None:
    flashable(session)
    artifact = session.compiled_artifact
    assert artifact.root.exists()
    assert artifact.sketch_dir.exists()


def test_a_failed_compile_leaves_no_artifact_behind(session: BuildSession) -> None:
    service = BuildService(compiler=FakeCompilerAdapter(failure_outcome()))
    run(service.compile_workspace(session))

    assert session.compiled_artifact is None
    assert session.flash_ready is False


def test_recompiling_discards_the_previous_artifact(session: BuildSession) -> None:
    service = flashable(session)
    stale_root = session.compiled_artifact.root

    run(service.compile_workspace(session))

    assert session.compiled_artifact.root != stale_root
    assert not stale_root.exists()


def test_ending_a_session_releases_its_build_output(session: BuildSession) -> None:
    service = flashable(session)
    root = session.compiled_artifact.root

    run(service.end_session(session))

    assert session.compiled_artifact is None
    assert not root.exists()


# --- hardware status: standalone detection, independent of flashing --------
#
# `detect_hardware` is the read-only check the Build Mode header polls — it
# must never require a compile first (unlike `flash_workspace`), never
# upload anything, and never emit a `BuildEvent` (see the module docstring
# in app/build/service.py::BuildService.detect_hardware).


def test_hardware_status_starts_not_checked(session: BuildSession) -> None:
    assert session.hardware_status is HardwareStatus.NOT_CHECKED
    assert session.hardware_board_name is None
    assert session.hardware_port is None
    assert session.snapshot()["hardware"] == {
        "status": "not_checked",
        "board_name": None,
        "port": None,
    }


def test_hardware_status_reports_connected_for_one_device(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[esp32(port="COM7")])
    service = BuildService(flasher=fake)

    result = run(service.detect_hardware(session))

    assert result.success is True
    assert result.events == ()
    assert session.hardware_status is HardwareStatus.CONNECTED
    assert session.hardware_board_name == "ESP32 Dev Module"
    assert session.hardware_port == "COM7"
    assert fake.detect_requests[0].fqbn == session.workspace.project.board.fqbn


def test_hardware_status_falls_back_to_project_board_name_when_unidentified(
    session: BuildSession,
) -> None:
    """A classic ESP32 behind a generic USB-UART bridge often has no
    `board_name` from the CLI (see flasher.py::SerialDevice) — the header
    should still name this project's own board rather than show nothing.
    """
    unidentified = SerialDevice(port="COM5", protocol="serial", has_usb_id=True)
    fake = FakeFlasherAdapter(devices=[unidentified])
    service = BuildService(flasher=fake)

    run(service.detect_hardware(session))

    assert session.hardware_status is HardwareStatus.CONNECTED
    assert session.hardware_board_name == session.workspace.project.board.name
    assert session.hardware_port == "COM5"


def test_hardware_status_reports_disconnected_for_no_devices(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[])
    service = BuildService(flasher=fake)

    run(service.detect_hardware(session))

    assert session.hardware_status is HardwareStatus.DISCONNECTED
    assert session.hardware_board_name is None
    assert session.hardware_port is None


def test_hardware_status_reports_ambiguous_for_multiple_devices(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[esp32(port="COM7"), esp32(port="COM9")])
    service = BuildService(flasher=fake)

    run(service.detect_hardware(session))

    assert session.hardware_status is HardwareStatus.AMBIGUOUS
    assert session.hardware_board_name is None
    assert session.hardware_port is None


def test_hardware_status_reports_error_when_discovery_itself_fails(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(
        devices=DeviceDetectOutcome(category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE)
    )
    service = BuildService(flasher=fake)

    run(service.detect_hardware(session))

    assert session.hardware_status is HardwareStatus.ERROR
    assert session.hardware_board_name is None
    assert session.hardware_port is None


def test_hardware_status_transitions_back_to_disconnected_after_unplug(
    session: BuildSession,
) -> None:
    """The whole point of polling: a board that was there and is gone must
    stop being reported as CONNECTED — see CLAUDE.md requirement 7 (E)."""
    connected = FakeFlasherAdapter(devices=[esp32()])
    run(BuildService(flasher=connected).detect_hardware(session))
    assert session.hardware_status is HardwareStatus.CONNECTED

    unplugged = FakeFlasherAdapter(devices=[])
    run(BuildService(flasher=unplugged).detect_hardware(session))

    assert session.hardware_status is HardwareStatus.DISCONNECTED
    assert session.hardware_board_name is None
    assert session.hardware_port is None


def test_hardware_status_appears_in_session_snapshot(session: BuildSession) -> None:
    fake = FakeFlasherAdapter(devices=[esp32(port="COM7")])
    run(BuildService(flasher=fake).detect_hardware(session))

    assert session.snapshot()["hardware"] == {
        "status": "connected",
        "board_name": "ESP32 Dev Module",
        "port": "COM7",
    }


def test_flashing_also_refreshes_the_live_hardware_status(session: BuildSession) -> None:
    """`flash_workspace` runs its own discovery before uploading — that
    result should update `hardware_status` too, without a second
    `arduino-cli board list` call, rather than leaving it NOT_CHECKED."""
    service = flash_service(devices=[esp32(port="COM7")])
    flashable(session, service)

    run(service.flash_workspace(session))

    assert session.hardware_status is HardwareStatus.CONNECTED
    assert session.hardware_board_name == "ESP32 Dev Module"
    assert session.hardware_port == "COM7"


def test_flashing_with_no_device_also_reports_hardware_disconnected(
    session: BuildSession,
) -> None:
    service = flash_service(devices=[])
    flashable(session, service)

    run(service.flash_workspace(session))

    assert session.flash_status is FlashStatus.NO_DEVICE
    assert session.hardware_status is HardwareStatus.DISCONNECTED


def test_hardware_status_for_one_session_does_not_affect_another() -> None:
    a = BuildSession(session_id="hw-a")
    b = BuildSession(session_id="hw-b")
    service = BuildService(flasher=FakeFlasherAdapter(devices=[esp32()]))

    run(service.detect_hardware(a))

    assert a.hardware_status is HardwareStatus.CONNECTED
    assert b.hardware_status is HardwareStatus.NOT_CHECKED
