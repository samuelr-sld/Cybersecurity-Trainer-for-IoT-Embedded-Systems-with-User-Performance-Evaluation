"""Micro-fix pass over session resume: log/clock restoration, compile in
flight, panel identity on resume, and the detached-session grace period."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest

from app.build import BuildEventType, CompileStatus
from app.build.compiler import CompileOutcome
from app.build.events import BuildEvent
from app.build.service import BuildService
from app.build_sessions import BuildSession, build_session_manager
from app.hardware.panel_identification import PanelIdentification, PanelIdentificationStatus
from app.hardware.panels import BUILT_IN_PANELS
from app.session_panel_guard import resume_panel_matches
from app.session_residency import Residency
from app.sessions import session_manager
from tests.test_session_resume import (  # noqa: F401 - fixtures/helpers
    _build_open_new,
    _build_open_resumed,
    _hack_open,
    client,
)

PANEL_A, PANEL_B = BUILT_IN_PANELS[0], BUILT_IN_PANELS[1]


# --- 1 & 2: activity log and elapsed clock ---------------------------------


def test_new_build_session_frame_has_no_history_and_starts_at_zero(client):
    with client.websocket_connect("/ws/build") as ws:
        frame = ws.receive_json()
        assert frame["resumed"] is False
        assert frame["history"] == []
        assert frame["elapsed_seconds"] <= 1


def test_resumed_build_session_replays_its_real_log_and_original_age(client):
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]
    stored = list(session.events)
    assert [e.type for e in stored][:2] == [
        BuildEventType.BUILD_SESSION_STARTED,
        BuildEventType.WORKSPACE_LOADED,
    ]
    # Pretend the session began 1h02m05s ago; the events keep their own stamps.
    session.created_at -= timedelta(seconds=3725)
    stored_stamps = [e.occurred_at for e in stored]

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        frame = ws.receive_json()
        assert frame["resumed"] is True
        assert 3725 <= frame["elapsed_seconds"] <= 3727  # not reset to 0
        assert [h["event"] for h in frame["history"]] == [e.type.value for e in stored]
        # Elapsed per event is measured from the session's true start.
        assert 3725 <= frame["history"][0]["elapsed_seconds"] <= 3727
        # Nothing was fabricated, duplicated or restamped by reconnecting.
        assert session.events == stored
        assert [e.occurred_at for e in session.events] == stored_stamps
        assert ws.receive_json()["type"] == "state"


def test_build_event_equality_ignores_its_timestamp():
    first = BuildEvent.create(BuildEventType.CODE_EDITED, "x")
    second = BuildEvent.create(BuildEventType.CODE_EDITED, "x")
    assert first == second


# --- 3: compile in flight ---------------------------------------------------


class _GatedCompiler:
    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.calls = 0

    async def run_compile(self, request):
        self.calls += 1
        await self.gate.wait()
        return CompileOutcome.ok(exit_code=0, stdout="", stderr="", duration_seconds=1.0)


def test_second_compile_cannot_overlap_and_the_first_result_stays_on_the_session():
    async def go() -> None:
        compiler = _GatedCompiler()
        service = BuildService(compiler=compiler)
        session = BuildSession(session_id="s")
        first = asyncio.create_task(service.compile_workspace(session))
        await asyncio.sleep(0.05)
        assert session.compile_status is CompileStatus.RUNNING

        second = await service.compile_workspace(session)
        assert second.success is False and "already running" in second.error
        assert compiler.calls == 1

        compiler.gate.set()
        assert (await first).success
        assert session.compile_status is CompileStatus.SUCCEEDED
        assert session.compiled_artifact is not None

    asyncio.run(go())


def test_resumed_client_sees_compile_running_and_a_second_compile_is_refused(client):
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]
        session.compile_status = CompileStatus.RUNNING  # the original compile, still going

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        state = _build_open_resumed(ws, session_id)
        assert state["compile_status"] == "running"
        ws.send_json({"type": "compile"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "already running" in frame["message"]
        assert session.compile_status is CompileStatus.RUNNING  # untouched


# --- 4: panel identity on resume --------------------------------------------


class _FakeIdentification:
    def __init__(self, panel) -> None:
        status = (
            PanelIdentificationStatus.IDENTIFIED
            if panel is not None
            else PanelIdentificationStatus.NOT_CONNECTED
        )
        self._value = PanelIdentification(status=status, panel=panel)

    def identify(self) -> PanelIdentification:
        return self._value


def test_guard_matching_missing_and_mismatched_panel():
    assert resume_panel_matches(PANEL_A.panel_id, _FakeIdentification(PANEL_A))
    assert not resume_panel_matches(PANEL_A.panel_id, _FakeIdentification(None))
    assert not resume_panel_matches(PANEL_A.panel_id, _FakeIdentification(PANEL_B))
    # A session that never had a panel holds nothing panel-specific.
    assert resume_panel_matches(None, _FakeIdentification(PANEL_B))


_PANEL_CASES = [("same", True), ("none", False), ("other", False)]


@pytest.mark.parametrize("attached, resumes", _PANEL_CASES)
def test_build_resume_is_bound_to_the_sessions_panel(client, monkeypatch, attached, resumes):
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]
        session.panel_id = PANEL_A.panel_id
    panel = {"same": PANEL_A, "none": None, "other": PANEL_B}[attached]
    monkeypatch.setattr(
        "app.session_panel_guard.PanelIdentificationService", lambda: _FakeIdentification(panel)
    )

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        frame = ws.receive_json()
        assert frame["resumed"] is resumes
        assert (frame["session_id"] == session_id) is resumes
    # Refusing never damaged or ended the original.
    assert build_session_manager.is_live(session_id)
    assert session.panel_id == PANEL_A.panel_id


@pytest.mark.parametrize("attached, resumes", _PANEL_CASES)
def test_hack_resume_is_bound_to_the_sessions_panel(client, monkeypatch, attached, resumes):
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
        session_manager._panels[session_id] = PANEL_A.panel_id
    panel = {"same": PANEL_A, "none": None, "other": PANEL_B}[attached]
    monkeypatch.setattr(
        "app.session_panel_guard.PanelIdentificationService", lambda: _FakeIdentification(panel)
    )

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        frame = _hack_open(ws)
        assert frame["resumed"] is resumes
        assert (frame["session_id"] == session_id) is resumes
    assert session_manager.is_live(session_id)
    assert session_manager.panel_of(session_id) == PANEL_A.panel_id


# --- 5: grace period --------------------------------------------------------


def test_residency_detach_resume_expire_semantics():
    async def go() -> None:
        expired: list[str] = []
        residency = Residency(grace_seconds=0.05)

        token = residency.claim("s")
        assert residency.detach("s", token, lambda: expired.append("s"))
        assert len(residency._timers) == 1
        # A second detach with the same (now spent) token starts nothing.
        assert not residency.detach("s", token, lambda: expired.append("dup"))
        assert len(residency._timers) == 1

        # Resuming cancels the timer; the old deadline passes harmlessly.
        new = residency.claim("s")
        assert not residency._timers
        await asyncio.sleep(0.15)
        assert expired == []
        # The superseded connection cannot detach (and so cannot end) it.
        assert not residency.detach("s", token, lambda: expired.append("stale"))
        assert residency.is_current("s", new)

        # Detach again: exactly one expiry, and only after the grace period.
        assert residency.detach("s", new, lambda: expired.append("s"))
        await asyncio.sleep(0.15)
        assert expired == ["s"]
        assert not residency._timers and not residency.is_current("s", new)

    asyncio.run(go())


def test_forget_cancels_a_pending_expiry():
    async def go() -> None:
        expired: list[str] = []
        residency = Residency(grace_seconds=0.05)
        token = residency.claim("s")
        residency.detach("s", token, lambda: expired.append("s"))
        residency.forget("s")  # explicit end
        await asyncio.sleep(0.15)
        assert expired == []

    asyncio.run(go())


def test_build_expired_session_cannot_be_resumed(client, monkeypatch):
    monkeypatch.setattr(build_session_manager._residency, "_grace", 0.05)
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
    deadline = time.monotonic() + 3
    while build_session_manager.is_live(session_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not build_session_manager.is_live(session_id)
    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        assert ws.receive_json()["resumed"] is False
