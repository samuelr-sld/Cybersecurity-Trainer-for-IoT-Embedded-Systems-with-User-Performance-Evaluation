"""Phase B7 hardware-in-the-loop: the real Build pipeline, real toolchain.

`tests/test_build_pipeline_b7.py` proves the WIRING with fakes, deliberately,
so the suite runs with nothing plugged in. This file removes the compiler
double and drives the real chain:

    Panel 1's real firmware -> B1 discovery -> BuildProject
        -> (optionally a real B6-generated edit)
        -> BuildWorkspace.materialize
        -> real `arduino-cli compile`
        -> [real `arduino-cli upload`, only when explicitly allowed]
        -> validation attempt

THREE GATES, EACH SKIPPING CLEANLY AND NEVER FAILING:

    `arduino-cli` missing            -> the whole module skips
    the ESP32 core not installed     -> the compile tests skip
    no ESP32 attached                -> the flash test skips

plus a fourth, which is a deliberate refusal rather than a capability check:
FLASHING IS OPT-IN. Writing firmware to whatever board happens to be plugged
into the machine running the suite is a physical, hard-to-reverse side effect
that a `pytest` invocation must not perform by surprise — it replaces
whatever program that board was running. Set `TRAINER_HIL_ALLOW_FLASH=1` to
permit it; without that, the flash test skips with that reason even with a
board attached and everything else ready.

AND, EVEN WHEN FLASHING IS PERMITTED, ONLY PANEL 1 IS EVER FLASHED. Every flash
here writes PANEL 1's firmware, so the board must actually be Panel 1: the
chip's MAC is read through the production `DeviceMonitor` and must be Panel
1's (`tests/hil_gating.py`). A different panel or an unregistered module
skips — flashing Panel 1's firmware onto it would destroy whatever it was
running. The read happens only after the opt-in check, so an ordinary HIL run
never probes (and thereby resets) the board. The compile tests need no such
gate: they compile to a temp directory and never touch the attached board.

PHASE B8 ADDED A FIFTH GATE, AND A TEST THAT CAN ACTUALLY FAIL. Panel 1 now
declares a machine-checkable remediation criterion, so the real validator can
run — but only against the real training network. It needs the MQTT client
library installed, the panel joined to the `CyberTrainer` AP, the Pi's
Mosquitto broker reachable, and the `TRAINER_LAB_*` lab fixtures provisioned.
Any of those missing skips, because a check that cannot observe the device
must never report a verdict about it.

NOTHING HERE CLAIMS A VALIDATED SECURE FIX, AND THE ONE REAL VERDICT IT
ASSERTS IS A FAILURE. What this file flashes is the panel's COMMITTED
firmware, which is deliberately vulnerable, so a correct validator must judge
it unfixed. That is the strongest honest hardware claim available without a
student's remediation in hand: the check runs against a real board over a
real broker and correctly catches the real vulnerability. A SUCCESS verdict
is never asserted here, because nothing in this repository is a fixed
firmware.

The rest of the backend suite never depends on this file passing.
"""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
from pathlib import Path

import pytest

from app import config
from app.build import (
    BuildWorkspace,
    board_info_from_fqbn,
    default_compiler,
    default_flasher,
    load_sketch_project,
)
from app.build.models import CompileStatus, FlashStatus, ValidationStatus
from app.build.service import BuildService
from app.build.validation import (
    SmartHomeAuthorizationValidator,
    ValidationOutcome,
    mqtt_evidence,
)
from app.build_project_selection import BuildProjectSelection, BuildProjectSource
from app.build_sessions import BuildSession
from app.build_validation_selection import select_build_validation
from app.hardware import DeviceMonitor
from app.panels import default_panel_package_loader
from app.panels.service import PanelResourceStatus
from tests.hil_gating import panel_one_skip_reason

pytestmark = pytest.mark.hardware

PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = (
    Path(__file__).resolve().parents[1]
    / "panels"
    / PANEL_ONE
    / "firmware"
    / "smart_home_mqtt_control"
)
FQBN = "esp32:esp32:esp32"


@pytest.fixture(scope="module")
def arduino_cli() -> str:
    """The real CLI, or skip the whole module."""
    executable = shutil.which(config.ARDUINO_CLI_PATH)
    if executable is None:
        pytest.skip(
            f"arduino-cli not found on PATH as {config.ARDUINO_CLI_PATH!r}; "
            "the hardware-in-the-loop build pipeline needs the real toolchain"
        )
    return executable


@pytest.fixture(scope="module")
def attached_board(arduino_cli):
    """Skip the whole module unless a real ESP32 answers.

    The same gate `tests/test_hardware_in_the_loop.py::hil_monitor` applies,
    over a PRIVATE `DeviceMonitor` for the same reason: the process-wide
    singleton is reset around every test by `conftest.py`, and every other
    test in the suite assumes it starts cold.

    A real compile here takes on the order of a minute, so gating on the
    board (not only on the toolchain) also keeps an ordinary, hardware-free
    `pytest` run as fast as it was before B7.
    """
    from app.hardware import DeviceMonitor, NullIdentityProbe

    # Presence is all this gate needs, and a MAC probe would drive the board
    # into its bootloader and hard-reset it for no reason — see
    # `app/hardware/monitor.py`. The panel-identity chain is proved in
    # `tests/test_hardware_in_the_loop.py`, not here.
    state = asyncio.run(
        DeviceMonitor(identity_probe=NullIdentityProbe()).refresh(fqbn=FQBN)
    )
    if not state.connected:
        pytest.skip(
            "no ESP32 detected on USB ('arduino-cli board list' found nothing); "
            "the hardware-in-the-loop build pipeline requires a connected board"
        )
    return state


@pytest.fixture(scope="module")
def panel_one_workspace_source(attached_board) -> Path:
    if not PANEL_SKETCH.is_dir():
        pytest.skip(f"Panel 1's firmware sketch is not present at {PANEL_SKETCH}")
    return PANEL_SKETCH


def fresh_workspace() -> BuildWorkspace:
    """Panel 1's real firmware as a project. One per test — no sharing.

    Since B8 the interaction policy is the panel's own declared one rather
    than the arbitrary stand-in B7 used, so what the real toolchain compiles
    here is exactly what a real connection would hand a student.
    """
    declared = default_panel_package_loader().load(PANEL_ONE).remediation
    return BuildWorkspace(
        load_sketch_project(
            PANEL_SKETCH,
            project_id="smart-home-mqtt-control-firmware",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="Smart Home MQTT Control System",
            board=board_info_from_fqbn(FQBN),
            editable_section_ids=declared.editable_section_ids,
            explore_section_ids=declared.explore_section_ids,
            security_region_id=declared.security_section_id,
        )
    )


def real_service() -> BuildService:
    """The production adapters, wired exactly as `default_service` wires them."""
    return BuildService(compiler=default_compiler, flasher=default_flasher)


def service_for(session: BuildSession) -> BuildService:
    """A service for a session an earlier step already compiled and flashed."""
    return real_service()


def skip_if_toolchain_missing(session: BuildSession) -> None:
    """Skip rather than fail when the ESP32 core is simply not installed."""
    outcome = session.compile_output
    text = f"{outcome.stdout}\n{outcome.stderr}".lower()
    if "platform" in text and ("not installed" in text or "not found" in text):
        pytest.skip(f"the {FQBN} platform is not installed on this machine")


# --- 1: a real compile of the real panel firmware ---------------------------


def test_panel_one_firmware_compiles_with_the_real_toolchain(
    panel_one_workspace_source,
) -> None:
    async def scenario() -> None:
        session = BuildSession(session_id="hil-compile", workspace=fresh_workspace())
        service = real_service()
        await service.start_session(session)
        await service.compile_workspace(session)
        skip_if_toolchain_missing(session)
        assert session.compile_status is CompileStatus.SUCCEEDED, (
            session.compile_output.stderr or session.compile_output.stdout
        )
        assert session.compiled_artifact is not None
        assert session.flash_ready is True

    asyncio.run(scenario())


# --- 2: a real compile of B6-GENERATED source -------------------------------


def test_generated_source_compiles_with_the_real_toolchain(
    panel_one_workspace_source,
) -> None:
    """The B7 claim that matters: B6's output is real, compilable firmware.

    An unedited round trip is used deliberately — the program applied is the
    one read back from the panel's own source, so what this proves is that
    passing real firmware through `analyze -> generate` still yields
    something `arduino-cli` accepts, without this test asserting anything
    about a remediation it does not implement.
    """

    async def scenario() -> None:
        workspace = fresh_workspace()
        path = workspace.project.files[0].path
        original = workspace.full_source(path)
        workspace.apply_program(path, workspace.program(path))
        generated = workspace.full_source(path)
        # Every statement B3 did not model is carried verbatim, so the
        # panel's Wi-Fi/MQTT machinery must survive generation intact.
        for fragment in ("client.setServer(", "WiFi.begin(", "applyCommand("):
            assert fragment in generated, fragment

        session = BuildSession(session_id="hil-generated", workspace=workspace)
        service = real_service()
        await service.start_session(session)
        await service.compile_workspace(session)
        skip_if_toolchain_missing(session)
        assert session.compile_status is CompileStatus.SUCCEEDED, (
            session.compile_output.stderr or session.compile_output.stdout
        )

    asyncio.run(scenario())


# --- 3: a real flash, only when explicitly permitted ------------------------


def test_the_real_pipeline_reaches_a_real_flash(panel_one_workspace_source) -> None:
    skip_unless_flashing_is_permitted()

    async def scenario() -> None:
        session = BuildSession(session_id="hil-flash", workspace=fresh_workspace())
        service = real_service()
        await service.start_session(session)
        await service.compile_workspace(session)
        skip_if_toolchain_missing(session)
        assert session.compile_status is CompileStatus.SUCCEEDED
        await service.flash_workspace(session)
        if session.flash_status is FlashStatus.NO_DEVICE:
            pytest.skip("no ESP32 detected on USB; nothing to flash")
        assert session.flash_status is FlashStatus.SUCCEEDED, (
            session.flash_output.stderr or session.flash_output.stdout
        )
        # ...and a successful upload still says NOTHING about validation.
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert session.validation_result is None

    asyncio.run(scenario())


# --- 4: the validation seam, against the real package -----------------------


def panel_one_plan():
    """The validation plan `/ws/build` would give a session on Panel 1."""
    package = default_panel_package_loader().load(PANEL_ONE)
    plan = select_build_validation(
        BuildProjectSelection(
            workspace=fresh_workspace(),
            source=BuildProjectSource.PANEL_PACKAGE,
            panel_status=PanelResourceStatus.READY,
            panel_id=PANEL_ONE,
            package=package,
        )
    )
    return package, plan


@functools.cache
def _not_panel_one_reason() -> str | None:
    """Why the attached board must not receive Panel 1's firmware, or None.

    One real detection with the real MAC probe (the same `DeviceMonitor()`
    `tests/test_hardware_in_the_loop.py` uses), cached so the flashing tests
    share a single read rather than resetting the board once each. Only ever
    reached after the `TRAINER_HIL_ALLOW_FLASH` opt-in, where the board is
    about to be reset by a flash anyway.
    """
    state = asyncio.run(DeviceMonitor().refresh(fqbn=FQBN))
    return panel_one_skip_reason(state)


def skip_unless_flashing_is_permitted() -> None:
    """Skip unless flashing is opted into AND the attached board is Panel 1.

    The opt-in is checked first so that nothing touches the board — not even
    the identity probe — in a run that was never going to flash it.
    """
    if os.getenv("TRAINER_HIL_ALLOW_FLASH") != "1":
        pytest.skip(
            "flashing overwrites whatever firmware is on the attached board; "
            "set TRAINER_HIL_ALLOW_FLASH=1 to permit it"
        )
    reason = _not_panel_one_reason()
    if reason is not None:
        pytest.skip(f"refusing to flash Panel 1's firmware: {reason}")


async def compiled_and_flashed(session_id: str, plan) -> BuildSession:
    """A real compile and a real upload of Panel 1's firmware. Skips cleanly."""
    session = BuildSession(
        session_id=session_id, workspace=fresh_workspace(), validation=plan
    )
    service = real_service()
    await service.start_session(session)
    await service.compile_workspace(session)
    skip_if_toolchain_missing(session)
    assert session.compile_status is CompileStatus.SUCCEEDED
    await service.flash_workspace(session)
    if session.flash_status is FlashStatus.NO_DEVICE:
        pytest.skip("no ESP32 detected on USB; nothing to flash")
    assert session.flash_status is FlashStatus.SUCCEEDED
    return session


def test_panel_one_resolves_to_its_own_validator_on_real_hardware() -> None:
    """B8: the registry row is what a real connection resolves through."""
    _, plan = panel_one_plan()
    assert isinstance(plan.strategy, SmartHomeAuthorizationValidator)
    assert plan.remediation.checkable is True


def test_an_unprovisioned_lab_refuses_to_validate_rather_than_guessing(
    panel_one_workspace_source,
) -> None:
    """Past every gate, on real hardware, with no lab credentials configured.

    Everything before validation really happened — a real compile, a real
    upload to a real board — so this refusal cannot be the flash gate
    answering early. It is Panel 1's own validator saying it cannot observe
    the device, and the important part is what does NOT happen: no verdict,
    no status change, and no recorded attempt for AID to count.
    """
    skip_unless_flashing_is_permitted()
    package, plan = panel_one_plan()
    criterion = plan.remediation.criterion
    if all(config.lab_secret(name) for name in criterion.secret_env_names) and (
        mqtt_evidence.available()
    ):
        pytest.skip(
            "this lab IS provisioned; the real check is covered by the next test"
        )

    async def scenario() -> None:
        session = await compiled_and_flashed("hil-validate-unprovisioned", plan)
        result = await service_for(session).validate_workspace(session)
        assert result.success is False
        assert result.error
        assert session.validation_status is ValidationStatus.NOT_STARTED
        assert not [
            attempt
            for attempt in session.recorder.attempts
            if attempt.attempt_type.value == "validation"
        ]
        assert package.panel_id == PANEL_ONE

    asyncio.run(scenario())


def test_the_real_check_catches_the_real_vulnerability(
    panel_one_workspace_source,
) -> None:
    """The one real hardware verdict this repository can honestly assert.

    Compile and flash the panel's COMMITTED firmware — which has no
    per-command authorization — then run the real validator against the real
    Mosquitto broker over the real training network. A correct check must
    return FAILURE, having observed an authenticated-but-unauthorized client
    actuate the motor.

    SUCCESS is never asserted, here or anywhere: no firmware in this
    repository is remediated, so a SUCCESS would mean the check is wrong.
    """
    skip_unless_flashing_is_permitted()
    if not mqtt_evidence.available():
        pytest.skip("paho-mqtt is not installed; the evidence channel cannot be opened")
    _, plan = panel_one_plan()
    missing = [
        name
        for name in plan.remediation.criterion.secret_env_names
        if not config.lab_secret(name)
    ]
    if missing:
        pytest.skip(
            "the training-lab fixtures are not provisioned on this machine; set "
            + ", ".join(missing)
        )

    async def scenario() -> None:
        session = await compiled_and_flashed("hil-validate-real", plan)
        result = await service_for(session).validate_workspace(session)
        assert result.success is True  # the ACTION ran
        verdict = session.validation_result
        assert verdict is not None
        if verdict.outcome is ValidationOutcome.ERROR:
            pytest.skip(
                f"the evidence channel was not usable from this machine: {verdict.message}"
            )
        assert verdict.outcome is ValidationOutcome.FAILURE, verdict.message
        assert session.validation_status is ValidationStatus.FAILED
        assert verdict.details.get("failed_probe")
        # One recorded, unsuccessful attempt — real evidence of a real check.
        attempts = [
            attempt
            for attempt in session.recorder.attempts
            if attempt.attempt_type.value == "validation"
        ]
        assert [attempt.success for attempt in attempts] == [False]

    asyncio.run(scenario())
