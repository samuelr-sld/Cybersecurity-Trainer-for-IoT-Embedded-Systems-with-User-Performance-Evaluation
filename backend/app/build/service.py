"""Build Service — session-level orchestration for Build Mode.

Position in the pipeline:

    Student -> Build Mode UI -> Build WebSocket -> Build Service -> Build Workspace -> Scenario
                                                          |
                                                          +-> CompilerAdapter -> arduino-cli compile
                                                          |
                                                          +-> FlasherAdapter  -> arduino-cli upload -> ESP32

This module is the transport/UI-independent seam: it knows how to turn one
requested action (load a workspace, edit a region, compile, flash) into a
`BuildWorkspace`/`CompilerAdapter`/`FlasherAdapter` call plus the
`BuildEvent`s that action caused, and it never imports FastAPI, WebSocket,
or JSON. `app/build_websocket.py` is the only thing that turns a
`BuildActionResult` into wire frames.

Every method is `async def` — Phase 3A's compile/flash/validation calls now
have real duration (see `compile_workspace` below), which is exactly the
await point `app/commands/router.py` documents anticipating for Hack Mode's
`CommandRouter.dispatch`.

COMPILATION AND FLASHING DEPEND ON ABSTRACTIONS, NOT ON SUBPROCESS DETAILS.
`compile_workspace` calls `self._compiler.run_compile(...)` and
`flash_workspace` calls `self._flasher.detect_devices(...)` /
`self._flasher.run_flash(...)`, where those are a `CompilerAdapter` (see
`app/build/compiler.py`) and a `FlasherAdapter` (see `app/build/flasher.py`)
— `BuildService` has no idea either is, in production, a real `arduino-cli`
subprocess underneath, and builds no command line of its own. That is what
lets tests inject fakes and never spawn a process or touch a board.

COMPILING AND FLASHING ARE DIFFERENT THINGS, AND THIS MODULE KEEPS THEM SO.
A successful compile never implies a successful flash: `compile_status` and
`flash_status` move independently, and the only link between them is a
one-directional precondition — `flash_workspace` refuses to upload unless
the last compile succeeded *and* the workspace still hashes to what that
compile was given.

AND NEITHER IS VALIDATION. Phase B7 adds `validate_workspace`, which closes
the pipeline `generated source -> compile -> flash -> validation` without
ever letting one stage's success stand in for the next one's. `arduino-cli`
exiting 0 twice proves source became a binary and a binary reached a board;
`validation_status` moves only when a `ValidationStrategy`
(`app/build/validation/`) actually returns a verdict, and there is no path in
this module from `FlashStatus.SUCCEEDED` to `ValidationStatus.SUCCEEDED`. The
dependency runs one way only — validation requires a successful flash of the
*current* workspace, and refuses to run otherwise — exactly as flashing
requires a successful compile of it. THE ENGINE ORCHESTRATES; THE VALIDATOR
VALIDATES: nothing here knows what any panel's vulnerability is, and no
panel-specific check exists in this codebase yet (see
`app/build/validation/strategy.py`).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import TYPE_CHECKING

from app import config
from app.build.compiler import (
    CompiledArtifact,
    CompileFailureCategory,
    CompilerAdapter,
    CompileOutcome,
    CompileRequest,
    default_compiler,
)
from app.build.events import BuildEvent, BuildEventType
from app.build.provisioning import ProvisioningError
from app.build.flasher import (
    DeviceDetectRequest,
    FlasherAdapter,
    FlashFailureCategory,
    FlashOutcome,
    FlashRequest,
    default_flasher,
)
from app.build.models import CompileStatus, FlashStatus, ValidationStatus
from app.build.records import BuildAttemptType
from app.build.validation import (
    ValidationContext,
    ValidationOutcome,
    ValidationResult,
)
from app.build.workspace import BuildWorkspaceError
from app.events.clock import utc_now
from app.hardware import (
    DeviceMonitor,
    DeviceState,
    NullIdentityProbe,
    device_monitor,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.build_sessions import BuildSession

logger = logging.getLogger(__name__)


#: How a caller asks to be told about an event *while* a long action is still
#: running, instead of only when it returns. A `BuildActionResult` is enough
#: for an action that finishes in microseconds, but a real `arduino-cli`
#: compile takes ~60s, and until it returned there was nothing on the wire at
#: all — the student saw a dead button and clicked it again. Passing a sink
#: lets the transport render "this started" at the moment it starts, without
#: this layer knowing anything about WebSockets or frames.
BuildEventSink = Callable[[BuildEvent], Awaitable[None]]


@dataclass(frozen=True)
class BuildActionResult:
    """What one Build Service call produced.

    `events` are the `BuildEvent`s this single call caused, already appended
    to the session's own log by the method that returned them — the caller
    renders them, it does not also record them. `error` is a short,
    caller-safe message (built from this module's own vocabulary, never
    reflecting the student's submitted source) for the WebSocket layer to
    surface as an `error` frame; it is set only when `success` is False.

    `data` is what a READ call produced (B8). Every action before B8 changed
    something, so "what happened" was fully described by its events and a
    refreshed snapshot. `read_section_blockly` changes nothing and answers a
    question instead, and its answer is too large and too specific to one
    request to belong in the whole-session `state` snapshot — a student opens
    one section at a time, not sixteen. It is JSON-safe data, never an object
    with behaviour, and it is `None` for every action that causes events.
    """

    success: bool
    events: tuple[BuildEvent, ...] = ()
    error: str | None = None
    data: dict | None = None

    @classmethod
    def ok(cls, *events: BuildEvent) -> "BuildActionResult":
        return cls(success=True, events=events)

    @classmethod
    def answered(cls, data: dict) -> "BuildActionResult":
        """A successful READ: no events, no state change, one answer."""
        return cls(success=True, data=data)

    @classmethod
    def failed(cls, error: str) -> "BuildActionResult":
        return cls(success=False, error=error)


#: Flash states in which a real operation is under way for a session. A
#: second flash, and any compile (which would delete the build directory the
#: upload is reading), are refused while the status is one of these.
_FLASH_IN_PROGRESS = frozenset({FlashStatus.DETECTING, FlashStatus.RUNNING})

#: No-device correction. `session.has_active_project` is False exactly when
#: no panel resolved to a real activity and the workspace is the inert
#: `create_no_device_workspace()` placeholder (see `app/build/no_device.py`);
#: every action that would edit, compile, flash or validate it is refused
#: with this message rather than operating on a placeholder nobody authored.
#: `read_section_blockly` and `detect_hardware` are deliberately NOT gated by
#: it — reading a placeholder section is harmless, and hardware polling must
#: keep working with no activity loaded.
_NO_ACTIVE_PROJECT_ERROR = "no panel is connected; there is no firmware to edit"


def _validation_detail(result: ValidationResult) -> str:
    """The short note a validation attempt row carries.

    A verdict's own message for a check that ran, and the outcome name in
    front of it otherwise — so a stored `ERROR` or `NOT_RUN` row can never
    be mistaken for a plain judgement about the student's firmware by
    someone reading the evidence table later.
    """
    if result.ran:
        return result.message
    if result.message:
        return f"{result.outcome.value}: {result.message}"
    return result.outcome.value


def discard_artifact(session: "BuildSession") -> None:
    """Delete a session's retained build output, if it has one.

    The single owner of that temp directory's lifetime: a new compile calls
    this before building, and `end_session` calls it on disconnect, so a
    Build session cannot leave a build tree behind on the machine. Safe to
    call when there is nothing to discard, and `ignore_errors` because a
    directory a virus scanner or a still-exiting process is holding open is
    not a reason to fail the student's action.
    """
    artifact = session.compiled_artifact
    if artifact is None:
        return
    session.compiled_artifact = None
    shutil.rmtree(artifact.root, ignore_errors=True)


def end_session_sync(session: "BuildSession") -> BuildEvent:
    """Synchronous core of `BuildService.end_session`.

    Split out so `app/build_websocket.py`'s teardown can call it directly
    instead of through `await end_session(...)`. That teardown's `finally`
    can run with this task already mid-cancellation, where — per
    `app/sessions.py::SessionManager.discard`'s reasoning for the identical
    Hack Mode constraint — no `await` is safe to rely on staying suspension-
    free forever, even one with nothing to suspend on today. Every call this
    makes (`discard_artifact`, `BuildEventRecorder.finish`, a dict append) is
    already plain synchronous code, so exposing it without the `async def`
    wrapper changes nothing about what runs, only removes the `await`.
    """
    discard_artifact(session)
    session.recorder.finish()
    event = BuildEvent.create(
        BuildEventType.BUILD_SESSION_ENDED,
        "build session ended",
        session_id=session.session_id,
    )
    session.events.append(event)
    return event


def _mirror_device_state(session: "BuildSession", state: DeviceState) -> None:
    """Copy the shared device state onto the session's hardware fields.

    PHASE 1 — THE SHARED LAYER IS NOW THE SOURCE OF TRUTH. Deciding what a
    detection *means* (connected / disconnected / ambiguous / error) moved
    out of this module into `app/hardware/monitor.py`, so Build Mode and
    Hack Mode read one verdict about one board instead of computing their
    own. What is left here is the projection of that verdict onto the
    session fields Build Mode's `state` frame has always carried —
    unchanged in name, type and meaning, which is why nothing in the
    frontend or the `/ws/build` protocol had to move.

    The one Build-Mode-specific decision that stays Build Mode's: the board
    *name*. The CLI does not always identify a classic ESP32 behind a
    generic USB-UART bridge (see `flasher.py::SerialDevice`), so the shared
    state truthfully reports `board=None` there rather than inventing one.
    Falling back to this session's own project board name is still truthful
    — a single serial candidate survived `_select_candidates`' USB/platform
    preference ladder specifically because it looks like this project's own
    ESP32 — and it is a fallback only the session knows how to make.
    """
    session.hardware_status = state.status
    session.hardware_port = state.port
    session.hardware_board_name = (
        (state.board or session.workspace.project.board.name)
        if state.connected
        else None
    )
    # The whole shared state is kept too, not just the three scalars above:
    # the header renders panel identity (MAC / panel name / port aliases),
    # and Build Mode must hand the frontend exactly what Hack Mode hands it
    # so one component can render both without branching on mode.
    session.device_state = state


class BuildService:
    """Orchestration: every call takes the session it acts on.

    `compiler` defaults to the real `ArduinoCliCompiler` (`default_compiler`)
    and `flasher` to the real `ArduinoCliFlasher` (`default_flasher`), but
    both are injected adapters, so a test can construct
    `BuildService(compiler=FakeCompilerAdapter(...), flasher=FakeFlasher(...))`
    and never touch a real subprocess or a physical board. These are the
    only per-instance state; everything else is still passed in per call via
    `session`.

    `monitor` is the shared device layer (`app/hardware/`), and defaults to
    the process-wide `device_monitor` — the same instance Hack Mode reads,
    which is what makes the two modes structurally unable to report
    different boards or ports for one physical ESP32. Build Mode does not
    own it and does not get its own copy in production.

    A caller that injects its *own* `flasher` without a monitor gets a
    private monitor over that adapter rather than the process-wide one. That
    is not a test affordance bolted on: serving a privately-injected
    adapter's detections out of the shared, cross-session cache would report
    a board that adapter never saw, which would be a lie in production too.
    """

    def __init__(
        self,
        compiler: CompilerAdapter | None = None,
        flasher: FlasherAdapter | None = None,
        monitor: DeviceMonitor | None = None,
    ) -> None:
        self._compiler = compiler if compiler is not None else default_compiler
        self._flasher = flasher if flasher is not None else default_flasher
        if monitor is not None:
            self._monitor = monitor
        elif flasher is not None:
            # `cache_seconds=0`: there are no other sessions sharing this
            # adapter, so there is nothing to deduplicate, and reuse would
            # only hide the injecting caller's own changes to it.
            #
            # `NullIdentityProbe`: this monitor reports whatever the
            # injected adapter says is attached, which need not be
            # physically present. Driving real esptool at a port that
            # adapter named would probe an absent device — or reset a real
            # board nobody asked us to touch. Pass an explicit `monitor` to
            # pair an injected flasher with real identity probing.
            self._monitor = DeviceMonitor(
                detector=flasher,
                cache_seconds=0.0,
                identity_probe=NullIdentityProbe(),
            )
        else:
            self._monitor = device_monitor

    async def start_session(self, session: "BuildSession") -> BuildActionResult:
        """Bring a freshly-created session's default workspace online.

        Idempotent, following the same `first_time` pattern
        `app/scenarios/environmental.py` uses: a session is only ever
        started once, right after the WebSocket accepts the connection (see
        `app/build_websocket.py`), but guarding here means a programming
        mistake that called this twice could not double-emit the bootstrap
        events.
        """
        if session.started:
            return BuildActionResult.ok()

        session.started = True
        events = (
            BuildEvent.create(
                BuildEventType.BUILD_SESSION_STARTED,
                "build session started",
                session_id=session.session_id,
            ),
            BuildEvent.create(
                BuildEventType.WORKSPACE_LOADED,
                # Not "default workspace loaded" since Phase B2: the session
                # may have been handed the attached panel's real firmware
                # instead. Which one it is remains carried by the event's own
                # `project_id`/`scenario_id` data, where it always was.
                "workspace loaded",
                scenario_id=session.workspace.project.scenario_id,
                project_id=session.workspace.project.project_id,
            ),
        )
        session.events.extend(events)
        return BuildActionResult.ok(*events)

    async def edit_region(
        self, session: "BuildSession", path: str, region_id: str, source: str
    ) -> BuildActionResult:
        """Apply one student edit to an editable region.

        Delegates the actual protection to `BuildWorkspace.update_region`:
        this method adds nothing to *whether* an edit is allowed, only to
        what happens once it is — marking the session dirty and building the
        events the edit caused.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        try:
            session.workspace.update_region(path, region_id, source)
        except BuildWorkspaceError as exc:
            return BuildActionResult.failed(str(exc))

        session.dirty = True
        events = [
            BuildEvent.create(
                BuildEventType.CODE_EDITED,
                "student edited firmware source",
                path=path,
                region_id=region_id,
            )
        ]
        if region_id == session.workspace.project.security_region_id:
            events.append(
                BuildEvent.create(
                    BuildEventType.SECURITY_REGION_EDITED,
                    "student edited the security region",
                    path=path,
                    region_id=region_id,
                )
            )
        session.events.extend(events)
        return BuildActionResult.ok(*events)

    async def read_section_blockly(
        self, session: "BuildSession", path: str, section_id: str
    ) -> BuildActionResult:
        """One section's Blockly representation, for the editor opening it.

        THE READ LEG OF THE SECTION -> BLOCKLY CONTRACT (B8). Build Mode is a
        section-based Blockly editor: a student clicks a discovered section and
        this is what that section opens as.

        READ-ONLY, AND THEREFORE NOT AN EDIT. It emits no `BuildEvent`, sets no
        status, and does not mark the session dirty — opening a section to look
        at it is not a remediation attempt and must never be recorded as one
        (the same reason `hardware_status` produces no event). Any section may
        be read whatever its policy, which is precisely what EXPLORE is for;
        writing is what `edit_section_blocks` gates.

        This method adds no knowledge of Blockly, sections or C++ of its own —
        it calls `BuildWorkspace.section_blockly` and returns what comes back,
        exactly as `edit_region` delegates protection to `update_region`.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        try:
            representation = session.workspace.section_blockly(path, section_id)
        except BuildWorkspaceError as exc:
            return BuildActionResult.failed(str(exc))
        return BuildActionResult.answered({"path": path, **representation})

    async def edit_section_blocks(
        self,
        session: "BuildSession",
        path: str,
        section_id: str,
        workspace: dict,
        preserved: list | tuple = (),
    ) -> BuildActionResult:
        """Apply one student edit made as BLOCKS — the intended interface (B8).

        The Blockly counterpart of `edit_region`, and deliberately its twin in
        every respect that matters downstream: the same `CODE_EDITED` and
        `SECURITY_REGION_EDITED` events, the same `dirty` flag, the same
        rejection shape. An evaluator reading the activity log cannot tell
        which mechanism produced an edit, and should not need to — what a
        student changed is the fact worth recording, not which widget they
        changed it with.

        Protection is `BuildWorkspace.apply_section_blockly`'s, which refuses a
        section the policy does not open BEFORE reading a single block, so an
        untrusted workspace for a locked section is never even parsed. This
        method adds nothing to whether an edit is allowed.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        try:
            session.workspace.apply_section_blockly(path, section_id, workspace, preserved)
        except BuildWorkspaceError as exc:
            return BuildActionResult.failed(str(exc))

        session.dirty = True
        events = [
            BuildEvent.create(
                BuildEventType.CODE_EDITED,
                "student edited firmware blocks",
                path=path,
                region_id=section_id,
            )
        ]
        if section_id == session.workspace.project.security_region_id:
            events.append(
                BuildEvent.create(
                    BuildEventType.SECURITY_REGION_EDITED,
                    "student edited the security region",
                    path=path,
                    region_id=section_id,
                )
            )
        session.events.extend(events)
        return BuildActionResult.ok(*events)

    async def compile_workspace(
        self, session: "BuildSession", *, emit: BuildEventSink | None = None
    ) -> BuildActionResult:
        """Compile the session's current workspace with the real toolchain.

        `emit`, when given, is awaited with `compile_started` at the moment
        the status flips to `RUNNING` — before the compiler is spawned,
        rather than ~60s later when it finishes. An event delivered that way
        is *not* repeated in the returned `BuildActionResult`, so a caller
        that passes a sink renders each event exactly once. It is still
        appended to `session.events` either way, so the session's own log is
        identical whether or not a sink was passed.

        Rejects a second concurrent request for the same session outright
        (`BuildActionResult.failed`, no state change, no process spawned) —
        the check-then-set of `session.compile_status` below has no `await`
        between them, so it cannot race under asyncio's cooperative
        scheduling even if this method is invoked twice concurrently for one
        session.

        Also rejects a compile while a flash is in flight: the retained
        build directory an upload is reading from is the very thing a new
        compile would replace.

        Otherwise this always returns `BuildActionResult.ok(...)`: a real
        compile that runs to completion and fails is a *successful action*
        that produced a truthful `compile_failed` event and a `FAILED`
        status, not a rejected request. Nothing here ever touches
        `session.workspace`'s live files — `BuildWorkspace.materialize`
        writes a throwaway filesystem copy and the compiler only ever sees
        that copy, so a failed (or timed-out, or crashed) compile can never
        corrupt the canonical workspace.

        PHASE 3C — ONE RETAINED ARTIFACT, ONLY ON SUCCESS. The temp
        directory is still deleted for every outcome except one: a compile
        that succeeded keeps it, recorded on the session as a
        `CompiledArtifact`, because that build output is what
        `flash_workspace` uploads. Any previous artifact is discarded
        *before* this compile starts, so a failed or crashed compile leaves
        the session with no artifact at all rather than a stale one from an
        earlier build — flashing something the student can no longer see is
        exactly the failure mode this ordering rules out.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        if session.compile_status is CompileStatus.RUNNING:
            return BuildActionResult.failed(
                "a compilation is already running for this session"
            )
        if session.flash_status in _FLASH_IN_PROGRESS:
            return BuildActionResult.failed(
                "a flash is in progress for this session; wait for it to finish"
            )

        session.compile_status = CompileStatus.RUNNING
        started = BuildEvent.create(
            BuildEventType.COMPILE_STARTED,
            "compilation started",
            project_id=session.workspace.project.project_id,
        )
        session.events.append(started)
        # Delivered now if the caller can take it now; otherwise it rides
        # back in the result like every other event.
        if emit is not None:
            await emit(started)
            events = []
        else:
            events = [started]

        discard_artifact(session)
        tmp_root: Path | None = None
        artifact: CompiledArtifact | None = None
        try:
            tmp_root = Path(tempfile.mkdtemp(prefix="buildmode-compile-"))
            sketch_dir = session.workspace.materialize(tmp_root / "sketch")
            build_path = tmp_root / "build"
            # Taken before the compile, not after: this is the source the
            # compiler is about to see, which is precisely what an upload
            # must be allowed to correspond to.
            fingerprint = session.workspace.fingerprint()
            # Option A — inject this session's real, non-committed
            # credentials (if any) into the throwaway copy above, strictly
            # before the real compiler ever sees it. `strategy` is whatever
            # this session's `ProvisioningPlan` carries (a no-op for every
            # panel that registers none); this call never knows what it is
            # provisioning, only that it may raise `ProvisioningError` for a
            # fixture that is not provisioned, which is reported exactly like
            # any other failed compile rather than as a silently-kept
            # placeholder.
            try:
                session.compile_provisioning.strategy.provision(sketch_dir)
            except ProvisioningError as error:
                outcome = CompileOutcome.failed(
                    CompileFailureCategory.INTERNAL_ERROR, stderr=str(error)
                )
            else:
                request = CompileRequest(
                    sketch_dir=sketch_dir,
                    fqbn=session.workspace.project.board.fqbn,
                    build_path=build_path,
                    timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
                )
                outcome = await self._compiler.run_compile(request)
            if outcome.success:
                artifact = CompiledArtifact(
                    root=tmp_root,
                    sketch_dir=sketch_dir,
                    build_path=build_path,
                    fingerprint=fingerprint,
                )
        finally:
            if tmp_root is not None and artifact is None:
                shutil.rmtree(tmp_root, ignore_errors=True)

        session.compile_output = outcome
        session.compiled_artifact = artifact
        if outcome.success:
            session.compile_status = CompileStatus.SUCCEEDED
            finished = BuildEvent.create(
                BuildEventType.COMPILE_SUCCEEDED,
                "compilation succeeded",
                exit_code=outcome.exit_code,
                duration_seconds=round(outcome.duration_seconds, 2),
            )
        else:
            session.compile_status = CompileStatus.FAILED
            finished = BuildEvent.create(
                BuildEventType.COMPILE_FAILED,
                "compilation failed",
                exit_code=outcome.exit_code,
                duration_seconds=round(outcome.duration_seconds, 2),
                category=outcome.category.value,
            )
        session.events.append(finished)
        events.append(finished)
        # PHASE 2E.3 — AID/DEI evidence. One attempt row per completed
        # compile, success or failure alike: this is the durable record
        # `app/metrics/aid.py`/`dei.py` read, distinct from `session.events`
        # (which is live-session-only and untimestamped).
        session.recorder.record_attempt(
            BuildAttemptType.COMPILE,
            outcome.success,
            detail="" if outcome.success else outcome.category.value,
        )
        return BuildActionResult.ok(*events)

    async def flash_workspace(self, session: "BuildSession") -> BuildActionResult:
        """Upload this session's last successful build to a physical ESP32.

        THREE GATES BEFORE ANY PROCESS IS SPAWNED, all of them rejections
        (`BuildActionResult.failed`) rather than flash failures — nothing was
        attempted, so nothing about the flash state changes:

        1. No second flash while one is running. Like `compile_workspace`,
           the check-then-set of `session.flash_status` has no `await`
           between them, so it cannot race under asyncio's cooperative
           scheduling.
        2. A successful compile must exist. `compile_status` alone is not
           enough — the retained `CompiledArtifact` must be there too, since
           that is the thing actually being uploaded.
        3. The workspace must still hash to what that artifact was built
           from. A student who edits the security region after a green build
           has to compile again; flashing stale output while different code
           is on screen would make the whole compile-then-flash story a lie.

        Past the gates this always returns `BuildActionResult.ok(...)`: a
        real upload that fails, a board that was unplugged, or nothing being
        connected at all are all *successful actions* that reported the
        truth, exactly as a failed compile is.

        NO VALIDATION HAPPENS HERE. A successful upload means `arduino-cli
        upload` exited 0 — the firmware was transferred. Whether it runs,
        works, or is actually secure is not checked anywhere in this
        codebase.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        if session.flash_status in _FLASH_IN_PROGRESS:
            return BuildActionResult.failed(
                "a flash is already running for this session"
            )
        if (
            session.compile_status is not CompileStatus.SUCCEEDED
            or session.compiled_artifact is None
        ):
            return BuildActionResult.failed(
                "compile the workspace successfully before flashing"
            )
        if session.compiled_artifact.fingerprint != session.workspace.fingerprint():
            return BuildActionResult.failed(
                "the workspace changed after the last successful compile; "
                "compile again before flashing"
            )

        artifact = session.compiled_artifact
        board = session.workspace.project.board
        session.flash_status = FlashStatus.DETECTING
        session.flash_output = None
        started = BuildEvent.create(
            BuildEventType.FLASH_STARTED,
            "flash started",
            project_id=session.workspace.project.project_id,
            fqbn=board.fqbn,
        )
        session.events.append(started)
        events = [started]

        discovery = await self._flasher.detect_devices(
            DeviceDetectRequest(
                fqbn=board.fqbn,
                timeout_seconds=config.BUILD_DEVICE_DETECT_TIMEOUT_SECONDS,
            )
        )
        # A flash's own discovery call is the freshest hardware information
        # in the process — publish it to the SHARED device state rather than
        # spawning a second `arduino-cli board list` just for the header.
        # Phase 1 widens who benefits: Hack Mode's hardware display learns
        # from this flash too, still without an extra CLI invocation. The
        # flash itself is unchanged and still selects its own port from
        # `discovery` below; nothing about the upload path reads the shared
        # state. See `detect_hardware` for the standalone check.
        _mirror_device_state(
            session, self._monitor.publish(discovery, fqbn=board.fqbn)
        )

        if not discovery.ok:
            # Discovery itself could not run — a missing toolchain or a
            # wedged CLI. Deliberately NOT reported as "no device": those
            # are different failure domains and a student debugging one
            # should never be sent looking at the other.
            return self._finish_flash(
                session,
                events,
                FlashOutcome.failed(
                    discovery.category,
                    stderr=discovery.stderr,
                    duration_seconds=discovery.duration_seconds,
                ),
                status=FlashStatus.FAILED,
                message="device detection failed",
            )

        if not discovery.devices:
            return self._finish_flash(
                session,
                events,
                FlashOutcome.failed(
                    FlashFailureCategory.NO_DEVICE,
                    stderr=discovery.stderr,
                    duration_seconds=discovery.duration_seconds,
                ),
                status=FlashStatus.NO_DEVICE,
                message="no compatible ESP32 device detected",
            )

        if len(discovery.devices) > 1:
            # Two or more plausible boards. Picking one would be a coin
            # flip that writes firmware to whichever device lost, so this
            # reports the ambiguity and uploads nothing.
            ports = ", ".join(device.port for device in discovery.devices)
            return self._finish_flash(
                session,
                events,
                FlashOutcome.failed(
                    FlashFailureCategory.AMBIGUOUS_DEVICE,
                    stderr=(
                        f"multiple candidate serial devices detected: {ports}. "
                        "disconnect all but the intended ESP32 and flash again."
                    ),
                    duration_seconds=discovery.duration_seconds,
                ),
                status=FlashStatus.FAILED,
                message="multiple candidate devices detected",
                device_count=len(discovery.devices),
            )

        device = discovery.devices[0]
        session.flash_status = FlashStatus.RUNNING
        # The upload owns the serial port for its whole duration. The hold
        # stops the shared layer from probing this board's MAC meanwhile —
        # `esptool read_mac` would fight `arduino-cli upload` for the port,
        # and the write is the operation that must win. Nothing else about
        # the flash changes: the port, the artifact and the invocation are
        # exactly what they were.
        with self._monitor.hold_identity_probe():
            outcome = await self._flasher.run_flash(
                FlashRequest(
                    sketch_dir=artifact.sketch_dir,
                    build_path=artifact.build_path,
                    fqbn=board.fqbn,
                    port=device.port,
                    timeout_seconds=config.BUILD_FLASH_TIMEOUT_SECONDS,
                )
            )
        return self._finish_flash(
            session,
            events,
            outcome,
            status=FlashStatus.SUCCEEDED if outcome.success else FlashStatus.FAILED,
            message="firmware uploaded" if outcome.success else "flash failed",
            board_name=device.board_name,
        )

    async def detect_hardware(self, session: "BuildSession") -> BuildActionResult:
        """Refresh the live ESP32 presence — read-only, no upload.

        PHASE 1: this now asks the SHARED device monitor
        (`app/hardware/monitor.py`) instead of calling the flasher directly,
        and mirrors its answer onto the session. Three consequences, none of
        which change what Build Mode shows:

        - Hack Mode sees the same refresh, because there is one monitor.
        - Concurrent polls from many sessions collapse into one real
          `arduino-cli board list` instead of one per session.
        - The board target is still this session's own project board, so a
          Build Mode check prefers exactly the platform it always did.

        Underneath, the monitor runs the same `FlasherAdapter.detect_devices`
        real `arduino-cli board list` that precedes every flash, and never
        proceeds to `run_flash`: this exists purely so the Build Mode header
        can show a truthful BOARD/PORT/LINK before the student ever asks to
        flash, and can keep showing one as boards are plugged in or
        unplugged while the session stays open.

        Deliberately emits no `BuildEvent` — the module docstring in
        `app/build/events.py` already treats device discovery as carrying no
        fact worth a log row of its own for one flash attempt, and that
        applies even harder to a check a client may poll every few seconds:
        an Activity Log entry per poll would be pure noise. The result is
        still real: `session.hardware_status` (and the `hardware` block in
        `BuildSession.snapshot()`) change, so the caller's `state` frame
        reflects it even though `events` is empty.

        Always returns `BuildActionResult.ok()` — a truthful "nothing is
        connected" or "discovery failed" is a successful check, exactly as
        `flash_workspace` treats those outcomes as successful actions, never
        rejections.
        """
        board = session.workspace.project.board
        _mirror_device_state(session, await self._monitor.refresh(fqbn=board.fqbn))
        return BuildActionResult.ok()

    async def validate_workspace(
        self, session: "BuildSession", *, emit: BuildEventSink | None = None
    ) -> BuildActionResult:
        """Run this session's validator against the firmware it just flashed.

        PHASE B7 — THE LAST LINK OF THE PIPELINE, AND THE ONE THAT MAY NOT
        BE INFERRED. Compiling and flashing are this backend's own
        operations and their success is its own to assert; whether the
        remediated firmware actually fixes anything is not, so this method
        delegates the verdict to `session.validation.strategy` (see
        `app/build/validation/`) and does no judging of its own. There is no
        branch here that reads `flash_status` and concludes anything about
        security.

        THREE GATES, ALL REJECTIONS — nothing is attempted, so no status
        moves, no event is emitted and, crucially, NO `BuildAttemptRecord`
        is written (a request that was refused is not an attempt, and AID
        must not count one):

        1. No second validation while one is running, the same check-then-set
           with no `await` between the two that `compile_workspace` and
           `flash_workspace` rely on.
        2. A successful flash must have happened. `validation` measures the
           firmware on the board, so with nothing uploaded there is nothing
           to measure — this is what keeps a failed (or never attempted)
           flash from being followed by a validation verdict.
        3. The workspace must still be the one that was flashed
           (`session.flash_ready`, the same content-hash check flashing
           itself uses). Validating firmware the student has already edited
           past would attribute a verdict to code that is not on the device.

        Then a fourth, asked of the validator rather than of the session:
        `unavailable_reason` — "there is no check to run here". Every panel
        shipped today answers with one, because no package declares a
        machine-checkable remediation criterion. That refusal is deliberately
        NOT a failed validation: a fix nobody checked has not been found
        wanting.

        Past the gates this always returns `BuildActionResult.ok(...)`: a
        check that ran and reported the fix does not work is a *successful
        action* that produced a truthful `validation_failed` event, exactly
        as a compile that reports an error is.
        """
        if not session.has_active_project:
            return BuildActionResult.failed(_NO_ACTIVE_PROJECT_ERROR)
        plan = session.validation
        if session.validation_status is ValidationStatus.RUNNING:
            return BuildActionResult.failed(
                "a validation is already running for this session"
            )
        if session.flash_status is not FlashStatus.SUCCEEDED:
            return BuildActionResult.failed(
                "flash the compiled firmware to the device successfully before validating"
            )
        if not session.flash_ready:
            return BuildActionResult.failed(
                "the workspace changed after the firmware that was flashed; "
                "compile and flash again before validating"
            )

        context = self._validation_context(session)
        unavailable = plan.strategy.unavailable_reason(context)
        if unavailable:
            return BuildActionResult.failed(unavailable)

        session.validation_status = ValidationStatus.RUNNING
        started = BuildEvent.create(
            BuildEventType.VALIDATION_STARTED,
            "validation started",
            project_id=session.workspace.project.project_id,
        )
        session.events.append(started)
        if emit is not None:
            await emit(started)
            events: list[BuildEvent] = []
        else:
            events = [started]

        began = time.monotonic()
        try:
            result = await plan.strategy.validate(context)
            if not isinstance(result, ValidationResult):
                raise TypeError(
                    f"validator returned {type(result).__name__}, not a ValidationResult"
                )
        except Exception as error:  # noqa: BLE001 - a broken check is a result
            # A validator that raises has not proved the firmware wrong; it
            # has proved itself broken. Recorded as ERROR (never FAILURE) so
            # an evaluator can tell the two apart, and never propagated —
            # a crashing check must not take the student's session with it.
            logger.warning(
                "validation strategy failed for build session %s: %s",
                session.session_id,
                error,
                exc_info=True,
            )
            result = ValidationResult.error(f"the validation check did not complete: {error}")

        result = dataclass_replace(
            result,
            duration_seconds=time.monotonic() - began,
            occurred_at=utc_now(),
        )
        return self._finish_validation(session, events, result)

    def _validation_context(self, session: "BuildSession") -> ValidationContext:
        """What the validator is told. Backend state only — see the context's
        own docstring for why nothing from the wire can reach it."""
        project = session.workspace.project
        artifact = session.compiled_artifact
        flash_output = session.flash_output
        return ValidationContext(
            session_id=session.session_id,
            project_id=project.project_id,
            scenario_id=project.scenario_id,
            module_id=project.module_id,
            board_fqbn=project.board.fqbn,
            firmware_fingerprint=(
                artifact.fingerprint if artifact is not None else session.workspace.fingerprint()
            ),
            panel_id=session.panel_id,
            flashed_port=None if flash_output is None else flash_output.port,
            remediation=session.validation.remediation,
            parameters=session.validation.parameters,
        )

    def _finish_validation(
        self,
        session: "BuildSession",
        events: list[BuildEvent],
        result: ValidationResult,
    ) -> BuildActionResult:
        """Record one validation attempt's truthful ending. Never raises.

        ONE PLACE DECIDES WHAT AN OUTCOME MEANS, so the status, the event and
        the evidence row cannot drift apart. Only `SUCCESS` is recorded as a
        successful attempt — the row `app/metrics/ttr.py` reads as "validated
        successful fix" — and `FAILURE`, `ERROR` and a `NOT_RUN` that somehow
        came back from a validator that said it could run are all recorded as
        unsuccessful attempts with their own `detail`. All three are still
        *attempts*: the student asked for a check that was available, so
        `app/metrics/aid.py` counting them is correct.
        """
        succeeded = result.succeeded
        session.validation_result = result
        session.validation_status = (
            ValidationStatus.SUCCEEDED if succeeded else ValidationStatus.FAILED
        )
        finished = BuildEvent.create(
            BuildEventType.VALIDATION_SUCCEEDED
            if succeeded
            else BuildEventType.VALIDATION_FAILED,
            result.message or ("validation succeeded" if succeeded else "validation failed"),
            outcome=result.outcome.value,
            duration_seconds=round(result.duration_seconds, 2),
        )
        session.events.append(finished)
        events.append(finished)
        session.recorder.record_attempt(
            BuildAttemptType.VALIDATION, succeeded, detail=_validation_detail(result)
        )
        return BuildActionResult.ok(*events)

    async def record_validation_attempt(
        self, session: "BuildSession", *, success: bool, detail: str = ""
    ) -> BuildActionResult:
        """Record one validation attempt's outcome, given the verdict. SEAM.

        THIS METHOD DOES NOT VALIDATE ANYTHING — it is the recording half of
        `validate_workspace` (above), reachable directly for a caller that
        already holds a verdict from somewhere else. `success`/`detail` must
        come from an ACTUAL check: never from "the compile succeeded" or
        "the flash succeeded", which are not evidence of a secure fix.

        Since Phase B7 this is a thin wrapper over `_finish_validation`, so a
        directly-recorded attempt produces exactly the same
        `ValidationStatus`, `BuildEvent` and `BuildAttemptRecord` as one the
        real validation path produced — there is one definition of what a
        recorded validation looks like, not two that could drift. It
        deliberately keeps its Phase 2E.3 signature, and it bypasses the
        compile/flash gates `validate_workspace` enforces, which is why it is
        not what the WebSocket layer calls.
        """
        result = ValidationResult(
            outcome=ValidationOutcome.SUCCESS if success else ValidationOutcome.FAILURE,
            message=detail,
            occurred_at=utc_now(),
        )
        return self._finish_validation(session, [], result)

    def _finish_flash(
        self,
        session: "BuildSession",
        events: list[BuildEvent],
        outcome: FlashOutcome,
        *,
        status: FlashStatus,
        message: str,
        **extra: object,
    ) -> BuildActionResult:
        """Record one flash attempt's truthful ending. Never raises.

        Every exit from `flash_workspace` past the gates goes through here,
        so there is exactly one place that decides the terminal status, the
        stored output, and the closing event — the reason a no-device result
        cannot drift out of sync with a flash failure.
        """
        session.flash_status = status
        session.flash_output = outcome
        finished = BuildEvent.create(
            BuildEventType.FLASH_SUCCEEDED if outcome.success else BuildEventType.FLASH_FAILED,
            message,
            port=outcome.port,
            exit_code=outcome.exit_code,
            duration_seconds=round(outcome.duration_seconds, 2),
            category=outcome.category.value,
            **{key: value for key, value in extra.items() if value is not None},
        )
        session.events.append(finished)
        events.append(finished)
        # PHASE 2E.3 — AID/DEI evidence, same discipline as the compile
        # path: one attempt row per completed flash, including a NO_DEVICE
        # outcome (the student did attempt to flash; nothing was uploaded).
        # `outcome.success` is the same truth value the FLASH_SUCCEEDED/
        # FLASH_FAILED event above already used — this records it, it does
        # not reinterpret it.
        session.recorder.record_attempt(
            BuildAttemptType.FLASH,
            outcome.success,
            detail="" if outcome.success else outcome.category.value,
        )
        return BuildActionResult.ok(*events)

    async def end_session(self, session: "BuildSession") -> BuildActionResult:
        """Record that a session ended. Called on disconnect; nothing to send.

        The connection is already closing by the time this runs (see
        `app/build_websocket.py`'s teardown), so this only appends to the
        session's own event log for completeness — there is no socket left
        to render a frame onto.

        It is also where a session's retained build output is released: the
        session object is about to be dropped from the registry, and its
        temp directory would otherwise outlive everything that knows the
        path to it.

        `async def` is kept here for direct callers (tests, any other future
        caller that wants to `await` it like the rest of this service) — the
        WebSocket teardown itself calls `end_session_sync` below instead, not
        this method.
        """
        return BuildActionResult.ok(end_session_sync(session))


#: Service used by the Build Mode WebSocket endpoint.
default_service = BuildService()
