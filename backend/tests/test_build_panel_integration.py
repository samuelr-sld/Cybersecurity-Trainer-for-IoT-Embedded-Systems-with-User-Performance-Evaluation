"""Phase 2E.3 — Build Mode panel-package integration and attempt recording.

Covers:

1. Build Mode resolving the currently attached panel's `PanelPackage`
   through the shared, already-existing resolution layer
   (`app/build_panel_resolution.py`), the same way Hack Mode already does.
2. `RemediationDeclaration` — the package-driven "what does remediation for
   this panel mean" seam — loading generically, including for the real
   shipped Panel 1 package.
3. `BuildSession`/`BuildService` recording real compile, flash, and
   validation attempts durably (`app/build/recorder.py`,
   `app/events/store.py`'s Build tables), including failed attempts, and
   that this evidence is session-isolated.
4. Genericity/security: no panel-specific literal or dynamic execution in
   the new generic modules.

Compile/flash are exercised against fake adapters (`FakeCompilerAdapter`/
`FakeFlasherAdapter`, the same pattern `tests/test_build_service.py` already
uses) — no real subprocess, no physical ESP32. `record_validation_attempt`
is exercised directly, since no real validation mechanism exists yet (see
that method's own docstring in `app/build/service.py`) — this file does not
claim physical/real secure-firmware validation works.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from app.build.compiler import CompileFailureCategory, CompileOutcome, CompileRequest
from app.build.flasher import (
    DeviceDetectOutcome,
    DeviceDetectRequest,
    FlashFailureCategory,
    FlashOutcome,
    FlashRequest,
    SerialDevice,
)
from app.build.models import ValidationStatus
from app.build.records import BuildAttemptType
from app.build.service import BuildService
from app.build_panel_resolution import resolve_build_panel_resources
from app.build_sessions import BuildSession, BuildSessionManager
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationStatus,
)
from app.hardware.panels import default_panel_registry
from app.panels import default_panel_package_loader
from app.panels.models import RemediationDeclaration
from app.panels.service import PanelResourceService, PanelResourceStatus

PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"
APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


class FakeCompilerAdapter:
    def __init__(self, outcome: CompileOutcome) -> None:
        self.outcome = outcome

    async def run_compile(self, request: CompileRequest) -> CompileOutcome:
        await asyncio.sleep(0)
        return self.outcome


def compile_success(**overrides) -> CompileOutcome:
    defaults = dict(exit_code=0, stdout="ok", stderr="", duration_seconds=1.0)
    defaults.update(overrides)
    return CompileOutcome.ok(**defaults)


def compile_failure(**overrides) -> CompileOutcome:
    defaults = dict(
        category=CompileFailureCategory.COMPILER_ERROR,
        exit_code=1,
        stdout="",
        stderr="error",
        duration_seconds=0.5,
    )
    defaults.update(overrides)
    return CompileOutcome.failed(**defaults)


class FakeFlasherAdapter:
    def __init__(self, devices=(), outcome: FlashOutcome | None = None) -> None:
        self.devices = devices
        self.outcome = outcome if outcome is not None else flash_success()

    async def detect_devices(self, request: DeviceDetectRequest) -> DeviceDetectOutcome:
        await asyncio.sleep(0)
        if isinstance(self.devices, DeviceDetectOutcome):
            return self.devices
        return DeviceDetectOutcome(devices=tuple(self.devices))

    async def run_flash(self, request: FlashRequest) -> FlashOutcome:
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
    defaults = dict(exit_code=0, stdout="ok", stderr="", duration_seconds=2.0, port="COM7")
    defaults.update(overrides)
    return FlashOutcome.ok(**defaults)


def flash_failure(**overrides) -> FlashOutcome:
    defaults = dict(exit_code=1, stdout="", stderr="failed", duration_seconds=1.0, port="COM7")
    defaults.update(overrides)
    category = defaults.pop("category", FlashFailureCategory.UPLOAD_ERROR)
    return FlashOutcome.failed(category, **defaults)


async def _compiled_ready_session(service: BuildService) -> BuildSession:
    """A session with a green compile in hand, ready to flash."""
    session = BuildSession(session_id="ready")
    await service.compile_workspace(session)
    assert session.flash_ready
    return session


# --- 1: Build Mode resolves the attached panel's package --------------------


class _FixedIdentification:
    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


def _service_over(identification: PanelIdentification) -> PanelResourceService:
    return PanelResourceService(identification=_FixedIdentification(identification))


def test_resolve_build_panel_resources_finds_panel_one() -> None:
    panel = default_panel_registry().resolve(PANEL_ONE_MAC).panel
    assert panel is not None
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port="COM3",
        mac=PANEL_ONE_MAC,
        panel=panel,
    )
    resources = resolve_build_panel_resources(_service_over(identification))
    assert resources.status is PanelResourceStatus.READY
    assert resources.package is not None
    assert resources.package.panel_id == PANEL_ONE_ID


def test_resolve_build_panel_resources_with_no_board_is_not_ready() -> None:
    identification = PanelIdentification(
        status=PanelIdentificationStatus.NOT_CONNECTED, port=None, mac=None, panel=None
    )
    resources = resolve_build_panel_resources(_service_over(identification))
    assert resources.status is PanelResourceStatus.NOT_CONNECTED
    assert resources.package is None


def test_resolve_build_panel_resources_uses_the_real_process_wide_service_by_default() -> None:
    # No injected service: exercises the real default construction path.
    resources = resolve_build_panel_resources()
    assert resources.status is not None  # any status is a valid, non-raising answer


# --- 2: package-defined remediation information loads generically -----------


def test_panel_one_package_declares_remediation() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    assert isinstance(package.remediation, RemediationDeclaration)
    assert "authorization" in package.remediation.vulnerability.lower()
    assert "authorization" in package.remediation.remediation_goal.lower()
    assert package.remediation.validation_requirement


def test_panel_one_remediation_does_not_describe_an_open_broker() -> None:
    """The established vulnerability is missing per-command authorization on
    an AUTHENTICATED broker — never "unauthenticated broker"."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    text = package.remediation.vulnerability.lower()
    assert "unauthenticated broker" not in text
    assert "without per-command authorization" in text


def test_remediation_declaration_requires_all_three_fields() -> None:
    with pytest.raises(ValueError):
        RemediationDeclaration(vulnerability="", remediation_goal="x", validation_requirement="y")
    with pytest.raises(ValueError):
        RemediationDeclaration(vulnerability="x", remediation_goal="", validation_requirement="y")
    with pytest.raises(ValueError):
        RemediationDeclaration(vulnerability="x", remediation_goal="y", validation_requirement="")


def test_package_with_no_remediation_declared_is_the_honest_none() -> None:
    from app.panels.models import EvaluationDeclaration, PanelPackage, ScenarioDefinition

    package = PanelPackage(
        schema_version=1,
        panel_id="no-remediation-yet",
        scenario=ScenarioDefinition(scenario_id="x", title="X"),
    )
    assert package.remediation is None


# --- 3: BuildSession / BuildSessionManager panel_id wiring -------------------


def test_build_session_manager_create_accepts_a_panel_id() -> None:
    async def run() -> None:
        manager = BuildSessionManager()
        session = await manager.create(panel_id=PANEL_ONE_ID)
        assert session.panel_id == PANEL_ONE_ID
        assert session.recorder.panel_id == PANEL_ONE_ID

    asyncio.run(run())


def test_build_session_manager_create_without_a_panel_id_is_honestly_none() -> None:
    async def run() -> None:
        manager = BuildSessionManager()
        session = await manager.create()
        assert session.panel_id is None
        assert session.recorder.panel_id is None

    asyncio.run(run())


def test_build_session_recorder_is_built_from_session_identity() -> None:
    session = BuildSession(session_id="fixed-id", panel_id="some-panel")
    assert session.recorder.session_id == "fixed-id"
    assert session.recorder.panel_id == "some-panel"
    assert session.recorder.started_at == session.created_at


# --- 4/5/6: compile / flash / validation attempts are recorded --------------


def test_compile_attempts_are_recorded(isolated_event_store) -> None:
    service = BuildService(compiler=FakeCompilerAdapter(compile_success()))
    session = BuildSession(session_id="compile-record")

    asyncio.run(service.compile_workspace(session))

    assert len(session.recorder.attempts) == 1
    attempt = session.recorder.attempts[0]
    assert attempt.attempt_type is BuildAttemptType.COMPILE
    assert attempt.success is True

    stored = isolated_event_store.build_attempts_for_session(session.session_id)
    assert len(stored) == 1
    assert stored[0].attempt_type is BuildAttemptType.COMPILE
    assert stored[0].success is True


def test_failed_compile_attempts_are_recorded(isolated_event_store) -> None:
    service = BuildService(compiler=FakeCompilerAdapter(compile_failure()))
    session = BuildSession(session_id="compile-fail-record")

    asyncio.run(service.compile_workspace(session))

    attempts = session.recorder.attempts
    assert len(attempts) == 1
    assert attempts[0].attempt_type is BuildAttemptType.COMPILE
    assert attempts[0].success is False


def test_flash_attempts_are_recorded(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService(
            compiler=FakeCompilerAdapter(compile_success()),
            flasher=FakeFlasherAdapter(devices=[esp32()], outcome=flash_success()),
        )
        session = await _compiled_ready_session(service)
        await service.flash_workspace(session)

        flash_attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.FLASH
        ]
        assert len(flash_attempts) == 1
        assert flash_attempts[0].success is True

        stored = isolated_event_store.build_attempts_for_session(session.session_id)
        stored_flash = [a for a in stored if a.attempt_type is BuildAttemptType.FLASH]
        assert len(stored_flash) == 1
        assert stored_flash[0].success is True

    asyncio.run(run())


def test_failed_flash_attempts_are_recorded(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService(
            compiler=FakeCompilerAdapter(compile_success()),
            flasher=FakeFlasherAdapter(devices=[esp32()], outcome=flash_failure()),
        )
        session = await _compiled_ready_session(service)
        await service.flash_workspace(session)

        flash_attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.FLASH
        ]
        assert len(flash_attempts) == 1
        assert flash_attempts[0].success is False

    asyncio.run(run())


def test_no_device_flash_outcome_is_still_a_recorded_attempt(isolated_event_store) -> None:
    """The student did attempt to flash; nothing was uploaded. It still
    counts as a (failed) flash attempt for AID/DEI — see the code comment
    in `app/build/service.py::_finish_flash`."""

    async def run() -> None:
        service = BuildService(
            compiler=FakeCompilerAdapter(compile_success()),
            flasher=FakeFlasherAdapter(devices=[]),  # nothing detected
        )
        session = await _compiled_ready_session(service)
        await service.flash_workspace(session)

        flash_attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.FLASH
        ]
        assert len(flash_attempts) == 1
        assert flash_attempts[0].success is False

    asyncio.run(run())


def test_validation_attempts_are_recorded(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService()
        session = BuildSession(session_id="validation-record")

        result = await service.record_validation_attempt(session, success=True, detail="ok")

        assert result.success is True
        assert session.validation_status is ValidationStatus.SUCCEEDED
        validation_attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert len(validation_attempts) == 1
        assert validation_attempts[0].success is True

        stored = isolated_event_store.build_attempts_for_session(session.session_id)
        stored_validation = [a for a in stored if a.attempt_type is BuildAttemptType.VALIDATION]
        assert len(stored_validation) == 1
        assert stored_validation[0].success is True

    asyncio.run(run())


def test_failed_validation_attempts_are_recorded() -> None:
    async def run() -> None:
        service = BuildService()
        session = BuildSession(session_id="validation-fail-record")

        await service.record_validation_attempt(session, success=False, detail="still vulnerable")

        assert session.validation_status is ValidationStatus.FAILED
        validation_attempts = [
            a for a in session.recorder.attempts if a.attempt_type is BuildAttemptType.VALIDATION
        ]
        assert len(validation_attempts) == 1
        assert validation_attempts[0].success is False

    asyncio.run(run())


def test_failed_attempts_remain_available_alongside_successes() -> None:
    """AID/DEI need the full history, not just the latest outcome."""

    async def run() -> None:
        service = BuildService(
            compiler=FakeCompilerAdapter(compile_failure())
        )
        session = BuildSession(session_id="mixed-history")
        await service.compile_workspace(session)

        service._compiler = FakeCompilerAdapter(compile_success())
        await service.compile_workspace(session)

        attempts = session.recorder.attempts
        assert len(attempts) == 2
        assert attempts[0].success is False
        assert attempts[1].success is True

    asyncio.run(run())


# --- session isolation --------------------------------------------------


def test_build_attempt_recording_is_session_isolated(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService(compiler=FakeCompilerAdapter(compile_success()))
        session_a = BuildSession(session_id="isolated-a")
        session_b = BuildSession(session_id="isolated-b")

        await service.compile_workspace(session_a)

        assert len(session_a.recorder.attempts) == 1
        assert len(session_b.recorder.attempts) == 0
        assert len(isolated_event_store.build_attempts_for_session("isolated-a")) == 1
        assert len(isolated_event_store.build_attempts_for_session("isolated-b")) == 0

    asyncio.run(run())


def test_two_build_sessions_have_independent_panel_ids() -> None:
    async def run() -> None:
        manager = BuildSessionManager()
        a = await manager.create(panel_id="panel-a")
        b = await manager.create(panel_id="panel-b")
        assert a.panel_id != b.panel_id
        assert a.recorder is not b.recorder

    asyncio.run(run())


# --- session lifecycle: recorder start/finish --------------------------


def test_ending_a_session_finishes_its_recorder(isolated_event_store) -> None:
    async def run() -> None:
        service = BuildService()
        session = BuildSession(session_id="lifecycle")
        session.recorder.start()

        await service.end_session(session)

        stored = isolated_event_store.build_session("lifecycle")
        assert stored is not None
        assert stored.ended_at is not None

    asyncio.run(run())


# =============================================================================
# Genericity and security
# =============================================================================

_NEW_MODULES = (
    APP_DIR / "build" / "records.py",
    APP_DIR / "build" / "recorder.py",
    APP_DIR / "build_panel_resolution.py",
)


def test_new_build_modules_contain_no_panel_specific_literal() -> None:
    banned_literals = {
        "smart-home-mqtt-control",
        "20:9b:a9:88:0b:e4",
        "cybertrainer/smart-home/motor/control",
        "cybertrainer/smart-home/motor/state",
        "192.168.50.1",
    }
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in banned_literals
        )
        assert offenders == [], f"{path.name} names panel-specific literals: {offenders}"


def test_new_build_modules_use_no_dynamic_execution() -> None:
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {"eval", "exec", "compile", "__import__"}, path.name
            if isinstance(func, ast.Attribute):
                assert func.attr not in {
                    "system",
                    "popen",
                    "spawn",
                    "spawnv",
                    "import_module",
                    "Popen",
                    "run",
                }, path.name
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ), path.name


def test_new_build_modules_import_no_subprocess_or_shell() -> None:
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        banned = ("subprocess", "os")
        offenders = sorted(
            name for name in imported for bad in banned if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_build_records_module_has_no_app_level_dependencies() -> None:
    """`app/build/records.py` must stay a true leaf (stdlib only) — see its
    own module docstring and `app/build/recorder.py`'s, which explain this
    is what keeps the app.build <-> app.events import graph acyclic."""
    tree = ast.parse((APP_DIR / "build" / "records.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    offenders = [name for name in imported if name.startswith("app.")]
    assert offenders == [], f"records.py imports app-level modules: {offenders}"
