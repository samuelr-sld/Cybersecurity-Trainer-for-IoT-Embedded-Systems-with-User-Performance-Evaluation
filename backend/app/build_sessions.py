"""Build Mode session lifecycle.

Mirrors `app/sessions.py` for Hack Mode: one session per WebSocket
connection, held in memory for the lifetime of that connection, and fully
isolated from every other session. A disconnect removes the session (and its
workspace) entirely — nothing about a student's firmware edits persists
across a reconnect in Phase 3A.

A session owns its own `BuildWorkspace` (created via a `default_factory`, so
every session gets an independent instance and no two connections can
observe or mutate each other's firmware), its own event log, and the
build/flash/validation status fields. The workspace's default project is
the LED Blink pipeline-proof project as of Phase 1 (`app/build/blink.py`);
see `app/build/__init__.py::create_default_workspace`. Phase 3B starts actually setting
`compile_status` and `compile_output`; Phase 3C adds `flash_status`,
`flash_output`, and the retained `compiled_artifact` a flash uploads.
Phase B7 finally drives `validation_status` for real, and adds the
`validation` plan (who checks this session's remediation) and
`validation_result` (what they found) beside it — though every panel shipped
today resolves to a validator that declines to run, so an ordinary session
still ends at `NOT_STARTED`. See `app/build/validation/`.
This module also adds `hardware_status`/`hardware_board_name`/
`hardware_port` — a live ESP32-presence check that is independent of any
flash attempt, set by `BuildService.detect_hardware` (and, incidentally, by
`flash_workspace`'s own discovery call). See `app/build/models.py`
`HardwareStatus`.

PHASE 2E.3 adds `panel_id` and `recorder`. `panel_id` is captured once, at
session creation (`BuildSessionManager.create`), from whatever the shared
panel-resolution layer already knows — see `app/build_panel_resolution.py`
— and is None whenever no panel resolves (no board, unidentified,
unregistered, no package): the honest "unknown" state, never guessed.
`recorder` is this session's own `BuildEventRecorder`
(`app/build/recorder.py`), the durable-evidence seam TTR/AID/DEI read from;
built in `__post_init__` (not a `default_factory`) because, like Hack
Mode's `HackSession.recorder`, it is not independent of the session — it
needs this session's id, its `created_at` as the TTR anchor, and its
`panel_id`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.build import (
    BuildEvent,
    BuildWorkspace,
    CompileStatus,
    FlashStatus,
    HardwareStatus,
    ValidationStatus,
    create_default_workspace,
)
from app.build.compiler import CompiledArtifact, CompileOutcome
from app.build.flasher import FlashOutcome
from app.build.provisioning import ProvisioningPlan, default_provisioning_plan
from app.build.recorder import BuildEventRecorder
from app.build.validation import (
    ValidationPlan,
    ValidationResult,
    default_validation_plan,
)
from app.hardware import DeviceState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BuildSession:
    """State for a single connected Build Mode client."""

    session_id: str
    created_at: datetime = field(default_factory=_utc_now)
    #: The per-session firmware workspace. Isolation lives here, exactly as
    #: `HackSession.scenario` isolates Hack Mode state: a fresh instance per
    #: session means one student's firmware edits are unreachable from
    #: another's. Since Phase B2 the connection lifecycle may hand one in
    #: (the attached panel's real firmware — see
    #: `app/build_project_selection.py`); the `default_factory` remains the
    #: no-hardware default and is what every other caller still gets.
    workspace: BuildWorkspace = field(default_factory=create_default_workspace)
    #: Every `BuildEvent` this session has caused so far, in order — the
    #: Phase 3B+ evaluation seam, mirroring `Scenario.events`.
    events: list[BuildEvent] = field(default_factory=list)
    #: Set once `BuildService.start_session` has run for this session.
    started: bool = False
    #: True once any editable region has diverged from its starting text.
    dirty: bool = False
    compile_status: CompileStatus = CompileStatus.NOT_STARTED
    flash_status: FlashStatus = FlashStatus.NOT_STARTED
    validation_status: ValidationStatus = ValidationStatus.NOT_STARTED
    #: Live ESP32 serial-device presence, independent of any flash attempt —
    #: set by `BuildService.detect_hardware` and, as a side effect of its own
    #: discovery call, by `BuildService.flash_workspace`. `NOT_CHECKED` until
    #: the first check for this session completes. See `app/build/models.py`
    #: `HardwareStatus` for why this is not folded into `FlashStatus`.
    hardware_status: HardwareStatus = HardwareStatus.NOT_CHECKED
    #: The detected board's identified name, or None whenever
    #: `hardware_status` is not CONNECTED (nothing plugged in, several
    #: candidates, or discovery failed — none of those name one board).
    hardware_board_name: str | None = None
    #: The detected board's serial port (e.g. "COM7" on Windows, a
    #: "/dev/tty..." path elsewhere) — whatever the OS/Arduino CLI reports,
    #: never a value this backend invents or the frontend supplies. None
    #: under the same conditions as `hardware_board_name`.
    hardware_port: str | None = None
    #: The full shared `DeviceState` behind the three scalars above, kept so
    #: the `hardware` block can carry the same panel identity (MAC, panel
    #: name, port aliases) Hack Mode's `hardware` frame carries. Set by
    #: `BuildService._mirror_device_state`; the default is the same
    #: NOT_CHECKED state the shared monitor starts in.
    device_state: DeviceState = field(default_factory=DeviceState)
    #: The most recent real compile's result, or None before any compile has
    #: run. Set only by `BuildService.compile_workspace`.
    compile_output: CompileOutcome | None = None
    #: The most recent real flash attempt's result, or None before any flash
    #: has been requested. Set only by `BuildService.flash_workspace`.
    flash_output: FlashOutcome | None = None
    #: The retained output of this session's last *successful* compile, or
    #: None whenever there isn't one — before the first compile, after a
    #: failed compile, and after the session ends. This is the only firmware
    #: a flash can ever upload; see `app/build/compiler.py::CompiledArtifact`
    #: and `app/build/service.py::discard_artifact`, which owns its lifetime.
    compiled_artifact: CompiledArtifact | None = None
    #: The Phase 2E.3 panel-resolution seam — see the module docstring.
    #: Captured once at session creation, never re-resolved mid-session.
    panel_id: str | None = None
    #: Whether `workspace` names a real activity a student may edit, compile,
    #: flash or validate. Set once at session creation from
    #: `BuildProjectSelection.has_active_project` (see
    #: `app/build_project_selection.py`) — False exactly when no panel
    #: resolved to a real firmware and the session's workspace is the inert
    #: `create_no_device_workspace()` placeholder. Defaults True so every
    #: existing direct construction (most tests, which never go through the
    #: connection lifecycle) is unaffected. `app/build/service.py` checks this
    #: before honouring an edit/compile/flash/validate request; hardware
    #: detection is deliberately NOT gated by it, so the header keeps polling
    #: truthfully with no activity loaded.
    has_active_project: bool = True
    #: Who validates this session's remediation, and against what declared
    #: requirement (Phase B7). THE SAME INJECTION `workspace` uses: the
    #: connection lifecycle chooses it from the attached panel's package via
    #: `app/build_validation_selection.py`, so this module stays as ignorant
    #: of panels and packages as it already was. The default is the honest
    #: one — a validator that runs nothing and says why (see
    #: `app/build/validation/strategy.py`).
    validation: ValidationPlan = field(default_factory=default_validation_plan)
    #: Who provisions this session's compile-time firmware copy with real,
    #: non-committed credentials, if anyone needs to (Option A). THE SAME KIND
    #: OF INJECTION `validation` is: chosen from the attached panel's package
    #: via `app/build_provisioning_selection.py`, so this module stays as
    #: ignorant of panels and secrets as it already was. The default is a
    #: no-op — the materialized sketch compiles exactly as it is, which is
    #: right for every panel that declares no provisioning strategy.
    compile_provisioning: ProvisioningPlan = field(default_factory=default_provisioning_plan)
    #: The verdict of this session's most recent completed validation, or
    #: None before one has run. Set only by `BuildService`; distinct from
    #: `validation_status`, which collapses four outcomes into the wire's
    #: four-member vocabulary and so cannot say NOT_RUN from ERROR.
    validation_result: ValidationResult | None = None
    #: The registered participant who owns this session (Evaluation phase),
    #: resolved at connect by `app/participants.py::resolve_participant`.
    #: None when the connection named no registered participant.
    participant_id: str | None = None
    #: This session's own attempt recorder (Phase 2E.3) — see the module
    #: docstring. Built in `__post_init__`, not a `default_factory`: unlike
    #: `workspace`, it needs this session's own id/created_at/panel_id.
    recorder: BuildEventRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = BuildEventRecorder(
            session_id=self.session_id,
            started_at=self.created_at,
            panel_id=self.panel_id,
            participant_id=self.participant_id,
        )

    @property
    def flash_ready(self) -> bool:
        """Whether a flash would be accepted right now.

        True only when the last compile succeeded, its build output is still
        retained, and the workspace has not changed since — i.e. exactly
        when "the compiled firmware is the code on screen" holds. Computed
        rather than stored so it cannot go stale: an edit changes the
        workspace's fingerprint and this answer follows immediately, with no
        invalidation step to forget.

        `BuildService.flash_workspace` re-checks the same three conditions
        itself; this exists so the frontend can honestly disable the FLASH
        control instead of offering an action the backend would reject.
        """
        return (
            self.compile_status is CompileStatus.SUCCEEDED
            and self.compiled_artifact is not None
            and self.compiled_artifact.fingerprint == self.workspace.fingerprint()
        )

    def snapshot(self) -> dict:
        """A JSON-serialisable view of the whole session for the `state` frame."""
        data = self.workspace.snapshot()
        data["dirty"] = self.dirty
        # No-device correction. False means `data["project"]` is the inert
        # placeholder (`create_no_device_workspace()`), never a real activity
        # — the frontend reads this, not the project id, to decide whether to
        # render the IDE at all.
        data["has_active_project"] = self.has_active_project
        data["compile_status"] = self.compile_status.value
        data["flash_status"] = self.flash_status.value
        data["validation_status"] = self.validation_status.value
        data["flash_ready"] = self.flash_ready
        # Exactly the shared `DeviceState` payload Hack Mode's `hardware`
        # frame carries, with one Build-Mode-owned override: `board_name`
        # falls back to this session's project board when the Arduino CLI
        # could not identify the attached one (see
        # `BuildService._mirror_device_state`). Identical shape in both
        # modes is what lets one frontend component render either.
        data["hardware"] = {
            **self.device_state.snapshot(),
            "status": self.hardware_status.value,
            "board_name": self.hardware_board_name,
            "port": self.hardware_port,
        }
        data["compile_output"] = (
            None
            if self.compile_output is None
            else {
                "success": self.compile_output.success,
                "category": self.compile_output.category.value,
                "exit_code": self.compile_output.exit_code,
                "stdout": self.compile_output.stdout,
                "stderr": self.compile_output.stderr,
                "duration_seconds": round(self.compile_output.duration_seconds, 2),
            }
        )
        data["flash_output"] = (
            None
            if self.flash_output is None
            else {
                "success": self.flash_output.success,
                "category": self.flash_output.category.value,
                "exit_code": self.flash_output.exit_code,
                "stdout": self.flash_output.stdout,
                "stderr": self.flash_output.stderr,
                "duration_seconds": round(self.flash_output.duration_seconds, 2),
                # The port that was actually uploaded to, or None when
                # discovery never selected one (nothing connected, or too
                # many). The frontend shows this as DEVICE: COMx rather than
                # inventing a device name.
                "port": self.flash_output.port,
            }
        )
        # Phase B7. Additive, and None until a validation has actually run —
        # `validation_status` alone cannot distinguish "the check ran and the
        # fix does not work" from "the check itself broke", and a client
        # showing a verdict must be able to tell those apart.
        data["validation_output"] = (
            None if self.validation_result is None else self.validation_result.snapshot()
        )
        # Phase 1.2. The plan's `RemediationSpec` (chosen at connect by
        # `app/build_validation_selection.py`, same as `self.validation`
        # itself) already existed for `validate_workspace` to read — this is
        # the first time its plain-text guidance is also sent to the client,
        # so a student can see what a fix must achieve before running
        # validation. `None` for the four panels that declare no remediation
        # activity yet (`RemediationSpec.declared` is False), the same honest
        # "not yet provisioned" state the plan itself carries. Only the prose
        # fields are sent — never `criterion` (broker/topic/probe detail the
        # scenario intends the student to recover through Hack Mode, not be
        # handed here).
        remediation = self.validation.remediation
        data["remediation"] = (
            None
            if remediation is None or not remediation.declared
            else {
                "vulnerability": remediation.vulnerability,
                "remediation_goal": remediation.remediation_goal,
                "validation_requirement": remediation.validation_requirement,
            }
        )
        return data


class BuildSessionManager:
    """In-memory registry of live Build Mode sessions.

    Async-locked for the same reason `app.sessions.SessionManager` is: a
    single event loop can interleave connect and disconnect handling across
    many concurrent WebSocket connections.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, BuildSession] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        panel_id: str | None = None,
        workspace: BuildWorkspace | None = None,
        validation: ValidationPlan | None = None,
        provisioning: ProvisioningPlan | None = None,
        has_active_project: bool = True,
        participant_id: str | None = None,
    ) -> BuildSession:
        """Create and register a session with a fresh unique id.

        `panel_id` is dependency injection, mirroring
        `app.sessions.SessionManager.create`'s `scenario` argument: the
        caller that owns the connection lifecycle
        (`app/build_websocket.py`) resolves the attached panel — via
        `app/build_panel_resolution.py`, a passive read of the shared
        device monitor's current state — and hands the result in. This
        manager and `BuildSession` never resolve one themselves. Omitting
        it (every existing caller and test) leaves `panel_id` at its
        honest `None` default.

        `workspace` is the SAME kind of injection, added in Phase B2 for the
        project the session loads. The connection lifecycle chooses it
        through `app/build_project_selection.py` — the attached panel's own
        firmware when one resolves, the default LED Blink project otherwise
        — so this manager and `BuildSession` stay exactly as ignorant of
        panels, packages and sketch directories as they were. Omitting it
        (every existing caller and test) falls back to
        `create_default_workspace()`, the long-standing default.

        `validation` is the THIRD injection of the same kind, added in Phase
        B7 for who validates this session's remediation. It is chosen off the
        very same panel resolution (`app/build_validation_selection.py`), and
        omitting it falls back to `default_validation_plan()` — a validator
        that runs nothing and says why.

        `provisioning` is the FOURTH injection of the same kind (Option A):
        who injects real, non-committed credentials into this session's
        compile-time firmware copy, chosen off the same panel resolution
        (`app/build_provisioning_selection.py`). Omitting it falls back to
        `default_provisioning_plan()` — a no-op, which is what every panel
        without a registered provisioning strategy gets.

        The recorder is started here, at connect, for the same reason
        `HackEventRecorder.start()` is: `started_at` is TTR's anchor, so it
        must mean "opened Build Mode", not "ran the first compile".
        """
        session = BuildSession(
            session_id=str(uuid.uuid4()),
            panel_id=panel_id,
            has_active_project=has_active_project,
            participant_id=participant_id,
            **({} if workspace is None else {"workspace": workspace}),
            **({} if validation is None else {"validation": validation}),
            **({} if provisioning is None else {"compile_provisioning": provisioning}),
        )
        session.recorder.start()
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> BuildSession | None:
        """Return the session with this id, or None if it is not live."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove(self, session_id: str) -> BuildSession | None:
        """Unregister a session. Safe to call for an already-removed id."""
        async with self._lock:
            return self._sessions.pop(session_id, None)

    def discard(self, session_id: str) -> BuildSession | None:
        """Unregister without awaiting. The WebSocket teardown path.

        Mirrors `app/sessions.py::SessionManager.discard` for the identical
        constraint: that `finally` block can run on a task the server has
        already cancelled, where an `await` — including acquiring `_lock` —
        is not safe to depend on completing. Dropping the lock is safe here
        rather than merely expedient: a dict `pop` contains no await point,
        so under asyncio it cannot interleave with `create`/`get`/`remove`.
        """
        return self._sessions.pop(session_id, None)

    def is_live(self, session_id: str) -> bool:
        """Whether this process is serving the session now (Evaluation read)."""
        return session_id in self._sessions

    async def count(self) -> int:
        """Number of live sessions (used by tests)."""
        async with self._lock:
            return len(self._sessions)


#: Process-wide session registry used by the Build Mode WebSocket endpoint.
build_session_manager = BuildSessionManager()
