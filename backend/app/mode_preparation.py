"""Mode Session Preparation — restore the vulnerable baseline before every mode.

EVERY ENTRY INTO HACK MODE OR BUILD MODE STARTS FROM A KNOWN PHYSICAL STATE.
A previous participant may have left a remediated (or half-edited) candidate
firmware on the ESP32. Before either mode's UI becomes usable, this module
puts the attached panel's own vulnerable BASELINE back on the board:

    DETECTING_DEVICE     fresh detection through the shared `DeviceMonitor`;
         |               the board must be one identified, registered panel
         v
    RESOLVING_FIRMWARE   PanelResources -> PanelPackage -> the package's own
         |               sketch, materialized by B2 (`BuildProjectSelector`)
         v
    COMPILING            the real `CompilerAdapter` over a throwaway copy
         |
         v
    FLASHING             the real `FlasherAdapter`, to the port detected in
         |               the first stage and nowhere else
         v
    VERIFYING            post-reset re-detection: the same panel is still on
         |               the port that was just written
         v
    LOADING_SCENARIO     the requested mode's own selection over the same
         |               resources (a mode-keyed table, not a panel branch)
         v
    READY

Hack Mode and Build Mode differ only in the LAST step, and only through
`_MODE_LOADERS` — the mode names what state it opens with, never which panel
it is on.

BASELINE VS CANDIDATE, STRUCTURALLY. The baseline is read from the panel
package on disk (`backend/panels/<package>/firmware/...`) every time this
runs, through the same `BuildProjectSelector.select_for` Build Mode uses at
connect, into a FRESH `BuildWorkspace`. Nothing here reads a `BuildSession`,
a session's workspace, a retained `CompiledArtifact`, or anything a student
edited — and a student's edits never reach the package directory, because
`BuildWorkspace.materialize` only ever writes throwaway copies. So a
candidate firmware cannot become the next session's baseline: there is no
path from one to the other.

COMPOSITION, NOT A SECOND TOOLCHAIN. The compile and flash use the same
adapters, request types, provisioning plan, content fingerprint and
identity-probe hold `BuildService` uses; this module builds no command line
and spawns nothing itself (`app/build/process.py` stays the one place that
does). It deliberately does NOT call `BuildService.compile_workspace` /
`flash_workspace` on a throwaway `BuildSession`: those record
`BuildAttemptRecord` rows — the AID/DEI evidence of a real student — and a
preparation is not a student's attempt.

NO PANEL BRANCH. Which firmware, which board, which scenario: all of it is
read off `PanelPackage` / `PanelDefinition` / the scenario registry. There is
no `if panel_id == ...` here and none is permitted.

FAILURE STOPS EVERYTHING. The first stage that fails ends the run, is
reported with its real reason, and nothing after it is attempted. The
temporary build directory and the identity-probe hold are released on every
exit, including cancellation (a student pressing BACK mid-compile), and
`run_capture` kills a cancelled toolchain's whole process tree.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app import config
from app.build.compiler import CompilerAdapter, CompileRequest, default_compiler
from app.build.flasher import (
    DeviceDetectRequest,
    FlasherAdapter,
    FlashRequest,
    default_flasher,
)
from app.build.provisioning import ProvisioningError
from app.build_project_selection import BuildProjectSelection, BuildProjectSelector
from app.build_provisioning_selection import select_build_provisioning
from app.build_validation_selection import select_build_validation
from app.hardware import DeviceMonitor, DeviceState, DeviceStatus, device_monitor
from app.hardware.panel_identification import PanelIdentificationService
from app.panels.service import PanelResources, PanelResourceService, PanelResourceStatus
from app.scenario_selection import SessionScenarioSelector
from app.scenarios import ScenarioRegistry, default_scenario_registry

logger = logging.getLogger(__name__)

#: Tool output shown on a failed stage is capped: the preparation screen
#: shows the tail of what went wrong, not a full compiler log.
_MAX_DETAIL_CHARS = 1500


class SessionMode(str, Enum):
    """Which mode is being entered. The only thing a client may choose."""

    HACK = "hack"
    BUILD = "build"


class PreparationStage(str, Enum):
    """The lifecycle, in order. Every member is a real backend operation."""

    DETECTING_DEVICE = "detecting_device"
    RESOLVING_FIRMWARE = "resolving_firmware"
    COMPILING = "compiling"
    FLASHING = "flashing"
    VERIFYING = "verifying"
    LOADING_SCENARIO = "loading_scenario"
    READY = "ready"


#: The stages a client renders as a checklist — READY is the outcome, not a
#: step. Order is the execution order and is asserted by the tests.
PREPARATION_STEPS: tuple[PreparationStage, ...] = (
    PreparationStage.DETECTING_DEVICE,
    PreparationStage.RESOLVING_FIRMWARE,
    PreparationStage.COMPILING,
    PreparationStage.FLASHING,
    PreparationStage.VERIFYING,
    PreparationStage.LOADING_SCENARIO,
)


class StageStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class PreparationProgress:
    """One stage transition, emitted the moment it happens."""

    stage: PreparationStage
    status: StageStatus
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparationResult:
    """How one preparation ended. `success` is True only after READY."""

    success: bool
    mode: SessionMode
    message: str
    #: The stage that failed; None on success.
    failed_stage: PreparationStage | None = None
    #: Tool output or reason behind a failure, already truncated.
    detail: str = ""
    #: Facts established along the way (panel, firmware, scenario) — for the
    #: preparation screen, never a capability.
    data: dict[str, Any] = field(default_factory=dict)


PreparationSink = Callable[[PreparationProgress], Awaitable[None]]


class _StageFailed(Exception):
    """Internal: the current stage failed with a student-safe message."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = _truncate(detail)


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_DETAIL_CHARS:
        return text
    return "... " + text[-_MAX_DETAIL_CHARS:]


async def _discard(_progress: PreparationProgress) -> None:
    return None


#: What a failed detection means, per shared device status. Every non-
#: CONNECTED status is a different reason and is reported as such.
_DEVICE_FAILURES: dict[DeviceStatus, str] = {
    DeviceStatus.NOT_CHECKED: "the hardware monitor has not checked for a board",
    DeviceStatus.DETECTING: "device detection did not complete",
    DeviceStatus.DISCONNECTED: "no ESP32 is connected",
    DeviceStatus.AMBIGUOUS: "more than one candidate board is connected",
    DeviceStatus.ERROR: "device detection could not run",
}

#: Panel-resolution statuses that mean "no identified, registered panel".
_UNIDENTIFIED_PANEL: dict[PanelResourceStatus, str] = {
    PanelResourceStatus.NOT_CHECKED: "the hardware monitor has not checked for a board",
    PanelResourceStatus.NOT_CONNECTED: "no ESP32 is connected",
    PanelResourceStatus.UNIDENTIFIED: "the connected board could not be identified",
    PanelResourceStatus.UNREGISTERED: "the connected board is not a registered training panel",
}


class ModePreparationService:
    """Owns the preparation lifecycle. One run at a time, process-wide.

    Every collaborator is injectable so the whole lifecycle runs in tests
    against fakes — no toolchain, no board — the same convention
    `BuildService` and `SessionScenarioSelector` follow. In production all of
    them are the process-wide instances both modes already use; there is no
    second detection system, compiler or flasher.
    """

    def __init__(
        self,
        *,
        monitor: DeviceMonitor | None = None,
        resources: PanelResourceService | None = None,
        compiler: CompilerAdapter | None = None,
        flasher: FlasherAdapter | None = None,
        scenario_registry: ScenarioRegistry | None = None,
    ) -> None:
        self._monitor = monitor if monitor is not None else device_monitor
        self._resources = (
            resources
            if resources is not None
            else PanelResourceService(
                identification=PanelIdentificationService(monitor=self._monitor)
            )
        )
        self._compiler = compiler if compiler is not None else default_compiler
        self._flasher = flasher if flasher is not None else default_flasher
        self._scenario_registry = (
            scenario_registry if scenario_registry is not None else default_scenario_registry
        )
        #: One physical board: two preparations must never compile/flash it
        #: concurrently. A second request waits for the first to finish (or
        #: to be cancelled), then runs from a clean state of its own.
        self._lock = asyncio.Lock()

    async def prepare(
        self, mode: SessionMode, *, emit: PreparationSink | None = None
    ) -> PreparationResult:
        """Run the whole lifecycle for `mode`. Never raises except on cancel.

        `emit` is awaited on every stage transition, so a transport can show
        each checkmark when — and only when — its operation has actually
        completed.
        """
        sink = emit if emit is not None else _discard
        run = _PreparationRun(self, SessionMode(mode), sink)
        async with self._lock:
            return await run.execute()

    # --- mode-specific tail (the only place the two modes differ) ----------

    def _load_hack(self, run: "_PreparationRun") -> dict[str, Any]:
        selector = SessionScenarioSelector(
            resources=self._resources, registry=self._scenario_registry
        )
        selection = selector.select_for(run.resources)
        if not selection.from_panel:
            raise _StageFailed(
                "this panel's scenario is not available", selection.detail
            )
        return {"scenario_id": selection.scenario_id}

    def _load_build(self, run: "_PreparationRun") -> dict[str, Any]:
        selection = run.selection
        if not selection.has_active_project:
            raise _StageFailed("this panel's Build Mode project is not available", selection.detail)
        # Constructing the plan is what the Build connection does next; a
        # package whose declaration cannot produce one fails here, not in
        # front of the student. (The plan itself is never sent to a client.)
        select_build_validation(selection)
        return {
            "scenario_id": selection.workspace.project.scenario_id,
            "project_id": selection.project_id,
        }


#: Mode -> loader. A table, like every other selection in this codebase.
_MODE_LOADERS: dict[SessionMode, Callable[[ModePreparationService, "_PreparationRun"], dict]] = {
    SessionMode.HACK: ModePreparationService._load_hack,
    SessionMode.BUILD: ModePreparationService._load_build,
}


class _PreparationRun:
    """One execution of the lifecycle. Holds nothing past its own return."""

    def __init__(
        self, service: ModePreparationService, mode: SessionMode, sink: PreparationSink
    ) -> None:
        self._service = service
        self.mode = mode
        self._sink = sink
        self.data: dict[str, Any] = {"mode": mode.value}
        self.resources: PanelResources | None = None
        self.selection: BuildProjectSelection | None = None
        self.detected: DeviceState | None = None
        self._tmp_root: Path | None = None
        self._artifact: tuple[Path, Path, str] | None = None
        self._flashed_port: str | None = None

    async def execute(self) -> PreparationResult:
        steps = (
            (PreparationStage.DETECTING_DEVICE, self._detect),
            (PreparationStage.RESOLVING_FIRMWARE, self._resolve_firmware),
            (PreparationStage.COMPILING, self._build_baseline),
            (PreparationStage.FLASHING, self._flash_baseline),
            (PreparationStage.VERIFYING, self._verify),
            (PreparationStage.LOADING_SCENARIO, self._load_mode_state),
        )
        try:
            for stage, action in steps:
                await self._emit(stage, StageStatus.RUNNING, _RUNNING[stage])
                try:
                    facts = await action()
                except _StageFailed as failure:
                    return await self._fail(stage, failure.message, failure.detail)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - reported, never swallowed
                    logger.exception("mode preparation failed unexpectedly at %s", stage.value)
                    return await self._fail(stage, "internal error during preparation", str(error))
                self.data.update(facts)
                await self._emit(stage, StageStatus.SUCCEEDED, _DONE[stage], facts)
            await self._emit(PreparationStage.READY, StageStatus.SUCCEEDED, "ready", {})
            logger.info("mode preparation ready: %s", self.data)
            return PreparationResult(
                success=True,
                mode=self.mode,
                message="training session prepared",
                data=dict(self.data),
            )
        finally:
            self._release()

    # --- stages ---------------------------------------------------------------

    async def _detect(self) -> dict[str, Any]:
        monitor = self._service._monitor
        # A FRESH detection AND a fresh identity read, never the caches: the
        # whole point is to confirm what is plugged in right now, and the
        # panel decided here selects the firmware that gets flashed. The
        # monitor caches a MAC per PORT, and a board swapped for another
        # behind the same kind of USB bridge reuses the port, so a cached MAC
        # could name the previous board and this stage would restore the
        # WRONG panel's firmware onto the new one. One extra MAC read (a
        # reset) here, right before the flash that resets the board anyway.
        state = await monitor.refresh(max_age_seconds=0, reverify_identity=True)
        if state.status is not DeviceStatus.CONNECTED or not state.port:
            raise _StageFailed(
                _DEVICE_FAILURES.get(state.status, "no ESP32 is connected"), state.detail
            )
        resources = self._service._resources.resolve()
        if resources.status in _UNIDENTIFIED_PANEL:
            raise _StageFailed(_UNIDENTIFIED_PANEL[resources.status], resources.detail)
        panel = resources.panel
        if panel is None:  # pragma: no cover - every identified status carries one
            raise _StageFailed("the connected board could not be identified")
        self.detected = state
        self.resources = resources
        return {
            "panel_id": panel.panel_id,
            "panel_name": panel.display_name,
            "port": state.port,
            "mac": resources.mac,
        }

    async def _resolve_firmware(self) -> dict[str, Any]:
        resources = self.resources
        if resources.status is not PanelResourceStatus.READY or resources.package is None:
            raise _StageFailed("this panel has no usable courseware package", resources.detail)
        # The SAME materialization Build Mode performs at connect — from the
        # package's own sketch on disk, into a brand-new workspace.
        selection = BuildProjectSelector(resources=self._service._resources).select_for(resources)
        if not selection.from_panel:
            raise _StageFailed("the panel's baseline firmware could not be loaded", selection.detail)
        self.selection = selection
        project = selection.workspace.project
        return {
            "firmware_id": project.project_id,
            "firmware_name": project.firmware_name,
            "fqbn": project.board.fqbn,
            "baseline_fingerprint": selection.workspace.fingerprint(),
        }

    async def _build_baseline(self) -> dict[str, Any]:
        workspace = self.selection.workspace
        self._tmp_root = Path(tempfile.mkdtemp(prefix="mode-prep-baseline-"))
        sketch_dir = workspace.materialize(self._tmp_root / "sketch")
        build_path = self._tmp_root / "build"
        fingerprint = workspace.fingerprint()
        provisioning = select_build_provisioning(self.selection)
        try:
            provisioning.strategy.provision(sketch_dir)
        except ProvisioningError as error:
            raise _StageFailed("the baseline firmware could not be provisioned", str(error))
        outcome = await self._service._compiler.run_compile(
            CompileRequest(
                sketch_dir=sketch_dir,
                fqbn=workspace.project.board.fqbn,
                build_path=build_path,
                timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
            )
        )
        if not outcome.success:
            raise _StageFailed(
                f"vulnerable firmware compilation failed ({outcome.category.value})",
                outcome.stderr or outcome.stdout,
            )
        self._artifact = (sketch_dir, build_path, fingerprint)
        return {"compile_seconds": round(outcome.duration_seconds, 2)}

    async def _flash_baseline(self) -> dict[str, Any]:
        monitor = self._service._monitor
        flasher = self._service._flasher
        workspace = self.selection.workspace
        fqbn = workspace.project.board.fqbn
        sketch_dir, build_path, fingerprint = self._artifact
        # Stale-artifact protection, as `flash_workspace` applies it: the
        # binary must correspond to the baseline source it was built from.
        if fingerprint != workspace.fingerprint():
            raise _StageFailed("the compiled baseline no longer matches its source")

        discovery = await flasher.detect_devices(
            DeviceDetectRequest(fqbn=fqbn, timeout_seconds=config.BUILD_DEVICE_DETECT_TIMEOUT_SECONDS)
        )
        monitor.publish(discovery, fqbn=fqbn)
        if not discovery.ok:
            raise _StageFailed("device detection failed before flashing", discovery.stderr)
        if len(discovery.devices) != 1:
            raise _StageFailed(
                "no ESP32 is connected"
                if not discovery.devices
                else "more than one candidate board is connected"
            )
        port = discovery.devices[0].port
        if port != self.detected.port:
            # Never write firmware to a board other than the one identified
            # as this panel in the first stage.
            raise _StageFailed(
                "the connected board changed during preparation",
                f"identified on {self.detected.port}, now on {port}",
            )
        with monitor.hold_identity_probe():
            outcome = await flasher.run_flash(
                FlashRequest(
                    sketch_dir=sketch_dir,
                    build_path=build_path,
                    fqbn=fqbn,
                    port=port,
                    timeout_seconds=config.BUILD_FLASH_TIMEOUT_SECONDS,
                )
            )
        if not outcome.success:
            raise _StageFailed(
                f"firmware flash failed ({outcome.category.value})",
                outcome.stderr or outcome.stdout,
            )
        self._flashed_port = outcome.port or port
        return {"flash_seconds": round(outcome.duration_seconds, 2)}

    async def _verify(self) -> dict[str, Any]:
        # The upload's own success already includes esptool's post-write
        # hash check. What this adds is the device side: after the reset the
        # upload ends with, the SAME panel is still attached on the SAME port.
        state = await self._service._monitor.refresh(max_age_seconds=0)
        if state.status is not DeviceStatus.CONNECTED or state.port != self._flashed_port:
            raise _StageFailed(
                "the ESP32 did not come back after flashing",
                state.detail or f"expected on {self._flashed_port}, found {state.port or 'nothing'}",
            )
        resources = self._service._resources.resolve()
        panel = resources.panel
        expected = self.resources.panel
        if panel is None or panel.panel_id != expected.panel_id:
            raise _StageFailed(
                "the ESP32 answered as a different panel after flashing", resources.detail
            )
        return {"verified_port": state.port}

    async def _load_mode_state(self) -> dict[str, Any]:
        return _MODE_LOADERS[self.mode](self._service, self)

    # --- plumbing -------------------------------------------------------------

    async def _emit(
        self,
        stage: PreparationStage,
        status: StageStatus,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._sink(PreparationProgress(stage, status, message, dict(data or {})))

    async def _fail(self, stage: PreparationStage, message: str, detail: str) -> PreparationResult:
        logger.warning("mode preparation failed at %s: %s (%s)", stage.value, message, detail)
        await self._emit(stage, StageStatus.FAILED, message, {"detail": detail})
        return PreparationResult(
            success=False,
            mode=self.mode,
            message=message,
            failed_stage=stage,
            detail=detail,
            data=dict(self.data),
        )

    def _release(self) -> None:
        """Delete the throwaway build. Synchronous, so it survives cancel."""
        self._artifact = None
        if self._tmp_root is not None:
            shutil.rmtree(self._tmp_root, ignore_errors=True)
            self._tmp_root = None


_RUNNING: dict[PreparationStage, str] = {
    PreparationStage.DETECTING_DEVICE: "detecting device",
    PreparationStage.RESOLVING_FIRMWARE: "resolving firmware",
    PreparationStage.COMPILING: "compiling vulnerable firmware",
    PreparationStage.FLASHING: "flashing vulnerable firmware",
    PreparationStage.VERIFYING: "verifying device",
    PreparationStage.LOADING_SCENARIO: "loading scenario",
}

_DONE: dict[PreparationStage, str] = {
    PreparationStage.DETECTING_DEVICE: "panel detected",
    PreparationStage.RESOLVING_FIRMWARE: "firmware resolved",
    PreparationStage.COMPILING: "vulnerable firmware compiled",
    PreparationStage.FLASHING: "vulnerable firmware flashed",
    PreparationStage.VERIFYING: "device verified",
    PreparationStage.LOADING_SCENARIO: "scenario loaded",
}


#: The service `/ws/prepare` uses — the process-wide monitor, compiler and
#: flasher both modes already share.
default_preparation_service = ModePreparationService()
