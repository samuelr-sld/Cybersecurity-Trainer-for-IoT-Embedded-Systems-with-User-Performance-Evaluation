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
`validation_status` remains `NOT_STARTED` — nothing validates anything yet.
This module also adds `hardware_status`/`hardware_board_name`/
`hardware_port` — a live ESP32-presence check that is independent of any
flash attempt, set by `BuildService.detect_hardware` (and, incidentally, by
`flash_workspace`'s own discovery call). See `app/build/models.py`
`HardwareStatus`.
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
    #: another's.
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
        data["compile_status"] = self.compile_status.value
        data["flash_status"] = self.flash_status.value
        data["validation_status"] = self.validation_status.value
        data["flash_ready"] = self.flash_ready
        data["hardware"] = {
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

    async def create(self) -> BuildSession:
        """Create and register a session with a fresh unique id."""
        session = BuildSession(session_id=str(uuid.uuid4()))
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

    async def count(self) -> int:
        """Number of live sessions (used by tests)."""
        async with self._lock:
            return len(self._sessions)


#: Process-wide session registry used by the Build Mode WebSocket endpoint.
build_session_manager = BuildSessionManager()
