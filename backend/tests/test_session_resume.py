"""A page reload is a reconnection, not the end of a Hack/Build session.

These drive the real `/ws/hack` and `/ws/build` endpoints and the real
managers (no mocking of the lifecycle itself). The claim under test is that
the SAME backend session stays authoritative across a disconnect: its id, its
recorded telemetry and its workspace belong to the session, so a reconnecting
client finds them where it left them — and that ending a session on purpose
(or letting an abandoned one time out) still really cleans it up.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.build import BLINK_REGION_ID, create_default_workspace
from app.build_project_selection import BuildProjectSelection, BuildProjectSource
from app.build_sessions import BuildSessionManager, build_session_manager
from app.main import app
from app.panels.service import PanelResourceStatus
from app.scenarios import EnvironmentalMonitoringScenario
from tests.test_build_websocket import fake_compile_success  # noqa: F401 - fixture
from app.sessions import SessionManager, session_manager

_TARGET = EnvironmentalMonitoringScenario().state.target
NMAP = f"nmap -p {_TARGET.mqtt_port} {_TARGET.ip_address}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Same reasoning as tests/test_build_websocket.py: force the Blink fixture
    # so Build sessions have an active project without real hardware.
    monkeypatch.setattr(
        "app.build_websocket.select_build_project",
        lambda: BuildProjectSelection(
            workspace=create_default_workspace(),
            source=BuildProjectSource.PANEL_PACKAGE,
            panel_status=PanelResourceStatus.READY,
        ),
    )
    with TestClient(app) as test_client:
        yield test_client


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- Hack Mode ----------------------------------------------------------------


def _hack_open(ws) -> dict:
    """Consume the `session` frame; return it. Banner/replay is read by callers."""
    frame = ws.receive_json()
    assert frame["type"] == "session"
    return frame


def _hack_drain_to_state(ws) -> list[dict]:
    """Read a resumed connection's replay: notice, events, then one `state`."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == "state":
            return frames


def _hack_run(ws, line: str) -> list[dict]:
    """Send a command and read until its `state` (if it changes any) or output."""
    ws.send_json({"type": "input", "data": line + "\r"})
    frames = [ws.receive_json()]
    while frames[-1]["type"] != "state" and frames[-1]["type"] != "output":
        frames.append(ws.receive_json())
    return frames


def test_hack_session_survives_disconnect_and_reconnect_to_the_same_session(client):
    before = len(session_manager._sessions)
    with client.websocket_connect("/ws/hack") as ws:
        first = _hack_open(ws)
        assert first["resumed"] is False
        ws.receive_json()  # banner
        session_id = first["session_id"]
        session = session_manager._sessions[session_id]
        # A real command, recorded server-side by the router.
        ws.send_json({"type": "input", "data": "help\r"})
        ws.receive_json()
        commands_before = len(session.recorder.commands)
        assert commands_before == 1

    # The socket is gone; the session is not.
    assert session_manager.is_live(session_id)
    assert session_manager._residency.is_detached(session_id)

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        second = _hack_open(ws)
        assert second["session_id"] == session_id
        assert second["resumed"] is True
        _hack_drain_to_state(ws)
        # No duplicate session was created by the reload.
        assert len(session_manager._sessions) == before + 1
        assert session_manager._sessions[session_id] is session
        # The resumed connection keeps recording into the SAME session.
        ws.send_json({"type": "input", "data": "help\r"})
        ws.receive_json()
        assert len(session.recorder.commands) == commands_before + 1
        assert not session_manager._residency.is_detached(session_id)


def test_hack_telemetry_stays_associated_with_the_same_session_and_is_replayed(client):
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()  # banner
        _hack_run(ws, NMAP)
        session = session_manager._sessions[session_id]
        recorded = [(e.event_type, e.sequence) for e in session.recorder.events]
        assert recorded, "the scan should have caused at least one scenario event"

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        _hack_open(ws)
        frames = _hack_drain_to_state(ws)
        replayed = [
            (f["event"], f["sequence"]) for f in frames if f["type"] == "event"
        ]
        # The page gets back exactly the rows the recorder (and database) hold.
        assert replayed == recorded
        assert all(e.session_id == session_id for e in session.recorder.events)
        # Nothing was fabricated by reconnecting.
        assert [(e.event_type, e.sequence) for e in session.recorder.events] == recorded


def test_hack_explicit_end_really_cleans_up_and_a_later_resume_starts_fresh(client):
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
        session = session_manager._sessions[session_id]

    response = client.post(f"/api/sessions/hack/{session_id}/end")
    assert response.json() == {"ended": True}
    assert not session_manager.is_live(session_id)
    assert session.recorder._finished is True
    assert client.get(f"/api/sessions/hack/{session_id}").json() == {"live": False}
    # Ending twice is harmless.
    assert client.post(f"/api/sessions/hack/{session_id}/end").json() == {"ended": False}

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        frame = _hack_open(ws)
        assert frame["resumed"] is False
        assert frame["session_id"] != session_id


def test_hack_explicit_end_while_connected_is_not_undone_by_the_socket_closing(client):
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
        assert client.post(f"/api/sessions/hack/{session_id}/end").json() == {"ended": True}
    # The closing connection no longer owns the session and must not resurrect it.
    assert not session_manager.is_live(session_id)
    assert not session_manager._residency.is_detached(session_id)


def test_hack_new_session_is_created_normally_when_none_is_requested(client):
    before = len(session_manager._sessions)
    with client.websocket_connect("/ws/hack") as ws:
        frame = _hack_open(ws)
        assert frame["resumed"] is False
        banner = ws.receive_json()
        assert banner["type"] == "output" and "channel established" in banner["data"]
        assert len(session_manager._sessions) == before + 1


@pytest.mark.parametrize("bogus", ["not-a-session", "00000000-0000-0000-0000-000000000000", ""])
def test_hack_invalid_or_expired_session_id_falls_back_to_a_normal_new_session(client, bogus):
    before = len(session_manager._sessions)
    with client.websocket_connect(f"/ws/hack?session={bogus}") as ws:
        frame = _hack_open(ws)
        assert frame["resumed"] is False
        assert frame["session_id"] != bogus
        banner = ws.receive_json()
        assert banner["type"] == "output" and "channel established" in banner["data"]
        assert len(session_manager._sessions) == before + 1


def test_hack_stale_connection_closing_late_does_not_tear_down_the_resumed_session(client):
    with client.websocket_connect("/ws/hack") as old:
        session_id = _hack_open(old)["session_id"]
        old.receive_json()
        with client.websocket_connect(f"/ws/hack?session={session_id}") as new:
            assert _hack_open(new)["resumed"] is True
            _hack_drain_to_state(new)
        # `new` closed first; now let `old` close as well (a late, superseded close).
    # Both connections are gone, but never was the session ended or duplicated.
    assert session_manager.is_live(session_id)
    assert session_manager._residency.is_detached(session_id)


def test_hack_detached_session_is_finished_when_the_grace_period_runs_out(client, monkeypatch):
    monkeypatch.setattr(session_manager._residency, "_grace", 0.05)
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
        session = session_manager._sessions[session_id]
    assert _wait_until(lambda: not session_manager.is_live(session_id))
    assert session.recorder._finished is True


def test_hack_resuming_cancels_the_pending_expiry(client, monkeypatch):
    monkeypatch.setattr(session_manager._residency, "_grace", 0.3)
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        _hack_open(ws)
        _hack_drain_to_state(ws)
        time.sleep(0.6)  # well past the original deadline
        assert session_manager.is_live(session_id)


def test_hack_disconnect_releases_the_serial_port_but_keeps_the_session(client, monkeypatch):
    released = []
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open(ws)["session_id"]
        ws.receive_json()
        session = session_manager._sessions[session_id]
        monkeypatch.setattr(session.serial, "release", lambda: released.append(True))
    assert released, "the port must not stay held by a detached session"
    assert session_manager.is_live(session_id)


def test_hack_manager_refuses_to_hand_a_session_to_a_different_participant():
    import asyncio

    async def go() -> None:
        manager = SessionManager()
        session = await manager.create(participant_id="2021-0001")
        assert manager.resume(session.session_id, "2021-0001") is session
        assert manager.resume(session.session_id, "2021-9999") is None
        assert manager.resume(session.session_id, None) is None
        assert manager.resume(None, "2021-0001") is None
        assert manager.resume("unknown", "2021-0001") is None

    asyncio.run(go())


# --- Build Mode ---------------------------------------------------------------


def _build_open_new(ws) -> tuple[str, dict]:
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert session_frame["resumed"] is False
    assert ws.receive_json()["event"] == "build_session_started"
    assert ws.receive_json()["event"] == "workspace_loaded"
    state = ws.receive_json()
    assert state["type"] == "state"
    return session_frame["session_id"], state["data"]


def _build_open_resumed(ws, session_id: str) -> dict:
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert session_frame["session_id"] == session_id
    assert session_frame["resumed"] is True
    state = ws.receive_json()
    # Straight to the snapshot: no bootstrap events are re-emitted.
    assert state["type"] == "state"
    return state["data"]


def _edit(ws, source: str) -> dict:
    ws.send_json(
        {"type": "edit_region", "path": "main.ino", "region_id": BLINK_REGION_ID, "source": source}
    )
    frame = ws.receive_json()
    while frame["type"] != "state":
        frame = ws.receive_json()
    return frame["data"]


def _editable_text(state: dict) -> str:
    return next(
        s for s in state["files"]["main.ino"]["segments"] if s["region_id"] == BLINK_REGION_ID
    )["text"]


def test_build_session_and_workspace_survive_disconnect_and_reconnect(client):
    before = len(build_session_manager._sessions)
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        edited = _edit(ws, "digitalWrite(2, LOW); // work in progress")
        assert edited["dirty"] is True
        session = build_session_manager._sessions[session_id]
        events_before = list(session.events)
        assert events_before

    assert build_session_manager.is_live(session_id)

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        state = _build_open_resumed(ws, session_id)
        # The student's edit is still there, in the SAME session.
        assert state["dirty"] is True
        assert _editable_text(state) == "digitalWrite(2, LOW); // work in progress"
        assert build_session_manager._sessions[session_id] is session
        assert len(build_session_manager._sessions) == before + 1  # no duplicate
        # Telemetry belongs to the same session and nothing was appended by reconnecting.
        assert session.events == events_before
        assert session.recorder.session_id == session_id
        # Work continues normally in the resumed session.
        again = _edit(ws, "digitalWrite(2, HIGH); // continued")
        assert _editable_text(again) == "digitalWrite(2, HIGH); // continued"
        assert len(session.events) > len(events_before)


def test_build_compile_state_is_owned_by_the_session_across_reconnect(client, fake_compile_success):
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        _edit(ws, "digitalWrite(2, LOW); // compiled")
        ws.send_json({"type": "compile"})
        state = None
        while state is None or state["compile_status"] != "succeeded":
            frame = ws.receive_json()
            if frame["type"] == "state":
                state = frame["data"]
        artifact = build_session_manager._sessions[session_id].compiled_artifact
        assert artifact is not None

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        resumed = _build_open_resumed(ws, session_id)
        # The retained build (what a flash would upload) survived the reload.
        assert resumed["compile_status"] == "succeeded"
        assert resumed["flash_ready"] is True
        assert build_session_manager._sessions[session_id].compiled_artifact is artifact


def test_build_explicit_end_really_cleans_up_and_a_later_resume_starts_fresh(client):
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]

    assert client.post(f"/api/sessions/build/{session_id}/end").json() == {"ended": True}
    assert not build_session_manager.is_live(session_id)
    assert session.recorder._finished is True
    assert session.events[-1].type.value == "build_session_ended"
    assert client.get(f"/api/sessions/build/{session_id}").json() == {"live": False}

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        new_id, state = _build_open_new(ws)  # asserts resumed False + bootstrap frames
        assert new_id != session_id
        assert state["dirty"] is False


def test_build_new_session_is_created_normally_when_none_is_requested(client):
    before = len(build_session_manager._sessions)
    with client.websocket_connect("/ws/build") as ws:
        _build_open_new(ws)
        assert len(build_session_manager._sessions) == before + 1


@pytest.mark.parametrize("bogus", ["not-a-session", "00000000-0000-0000-0000-000000000000"])
def test_build_invalid_or_expired_session_id_falls_back_to_a_normal_new_session(client, bogus):
    before = len(build_session_manager._sessions)
    with client.websocket_connect(f"/ws/build?session={bogus}") as ws:
        session_id, state = _build_open_new(ws)
        assert session_id != bogus
        assert state["dirty"] is False
        assert len(build_session_manager._sessions) == before + 1


def test_build_detached_session_is_finished_when_the_grace_period_runs_out(client, monkeypatch):
    monkeypatch.setattr(build_session_manager._residency, "_grace", 0.05)
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]
    assert _wait_until(lambda: not build_session_manager.is_live(session_id))
    assert session.recorder._finished is True


def test_build_stale_connection_closing_late_does_not_tear_down_the_resumed_session(client):
    with client.websocket_connect("/ws/build") as old:
        session_id, _ = _build_open_new(old)
        with client.websocket_connect(f"/ws/build?session={session_id}") as new:
            _build_open_resumed(new, session_id)
    assert build_session_manager.is_live(session_id)
    assert build_session_manager._residency.is_detached(session_id)


def test_build_manager_refuses_to_hand_a_session_to_a_different_participant():
    import asyncio

    async def go() -> None:
        manager = BuildSessionManager()
        session = await manager.create(participant_id="2021-0001")
        assert manager.resume(session.session_id, "2021-0001") is session
        assert manager.resume(session.session_id, "2021-9999") is None
        assert manager.resume(session.session_id, None) is None

    asyncio.run(go())


def test_hack_and_build_sessions_are_ended_independently(client):
    with client.websocket_connect("/ws/hack") as h, client.websocket_connect("/ws/build") as b:
        hack_id = _hack_open(h)["session_id"]
        h.receive_json()
        build_id, _ = _build_open_new(b)
    assert client.post(f"/api/sessions/hack/{hack_id}/end").json() == {"ended": True}
    assert build_session_manager.is_live(build_id)
    assert not session_manager.is_live(hack_id)
    # A hack id means nothing to the build registry.
    assert client.post(f"/api/sessions/build/{hack_id}/end").json() == {"ended": False}
