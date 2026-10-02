"""The three session lifecycles the frontend relies on, driven end to end.

    page reload   the SAME backend session continues        (`?session=<id>`)
    RESET         end the old session, then a NEW one       (POST .../end, then
                  a connection that names no session)
    QUIT          end the session, create nothing           (POST .../end)

The frontend's Reset/Quit (`src/session/modeLifecycle.js`) is exactly that
wire sequence: it asks `POST /api/sessions/{mode}/{id}/end`, forgets its
remembered id, and the mode screen's next connection therefore carries no
`session` parameter. No backend endpoint exists for Reset or Quit because none
is needed; these tests pin that the existing end + connect pair is enough, for
both modes, and that ending one session never loses its recorded activity or
leaks into its successor.

Real endpoints, real managers, the isolated in-memory event store from
`tests/conftest.py`. Nothing here touches hardware or the real database.
"""

from __future__ import annotations

from app.build_sessions import build_session_manager
from app.events import get_default_store
from app.scenarios import EnvironmentalMonitoringScenario
from app.sessions import session_manager
from tests.test_build_websocket import fake_compile_success  # noqa: F401 - fixture
from tests.test_session_resume import (  # noqa: F401 - `client` is a fixture
    NMAP,
    _build_open_new,
    _build_open_resumed,
    _edit,
    _editable_text,
    _hack_drain_to_state,
    _hack_open,
    _hack_run,
    client,
)

EDIT = "digitalWrite(2, LOW); // the student's work"
# A command that visibly advances the scenario (a scan only emits an event).
EXTRACT = "esptool.py read_flash 0x0 0x400000 firmware.bin"


def _hack_open_new(ws) -> str:
    """Open a connection that names no session; return the new session id."""
    frame = _hack_open(ws)
    assert frame["resumed"] is False
    banner = ws.receive_json()
    assert banner["type"] == "output" and "channel established" in banner["data"]
    return frame["session_id"]


# --- Hack Mode ----------------------------------------------------------------


def test_hack_reload_keeps_the_same_session_open_and_unended(client):
    store = get_default_store()
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open_new(ws)
        _hack_run(ws, NMAP)

    # The page is gone; nothing ended the session.
    assert store.session(session_id).ended_at is None

    with client.websocket_connect(f"/ws/hack?session={session_id}") as ws:
        frame = _hack_open(ws)
        assert (frame["session_id"], frame["resumed"]) == (session_id, True)
        _hack_drain_to_state(ws)
        assert store.session(session_id).ended_at is None
    assert [s.session_id for s in store.sessions()] == [session_id]  # no second row


def test_hack_reset_ends_the_old_session_and_starts_a_completely_new_one(client):
    store = get_default_store()
    with client.websocket_connect("/ws/hack") as ws:
        old_id = _hack_open_new(ws)
        _hack_run(ws, EXTRACT)
        old = session_manager._sessions[old_id]
        old_events = [(e.event_type, e.sequence) for e in old.recorder.events]
        assert old_events, "the extraction should have recorded something to reset away from"
        old_snapshot = old.scenario.snapshot()
        assert old_snapshot["discovery"]["firmware_extracted"] is True

        # RESET step 1: the explicit end (the socket is still open, as it is
        # when the button is pressed).
        assert client.post(f"/api/sessions/hack/{old_id}/end").json() == {"ended": True}
    assert not session_manager.is_live(old_id)

    # RESET step 2: the mode screen mounts again and, having forgotten the
    # old id, connects with no `session` parameter.
    with client.websocket_connect("/ws/hack") as ws:
        new_id = _hack_open_new(ws)
        new = session_manager._sessions[new_id]

        assert new_id != old_id
        assert new is not old
        # Fresh mode state: its own scenario instance in its initial state, and
        # an empty record — nothing carried over from the session just ended.
        assert new.scenario is not old.scenario
        assert new.scenario.snapshot() == EnvironmentalMonitoringScenario().snapshot()
        assert new.scenario.snapshot()["discovery"]["firmware_extracted"] is False
        assert list(new.recorder.events) == []
        assert list(new.recorder.commands) == []
        assert store.events_for_session(new_id) == ()
        assert store.commands_for_session(new_id) == ()
        # Exactly one live session, and it is the new one.
        assert set(session_manager._sessions) == {new_id}

        # The new session records on its own timeline.
        _hack_run(ws, NMAP)
        assert [e.session_id for e in store.events_for_session(new_id)] == [new_id] * len(
            store.events_for_session(new_id)
        )

    # The ended session's evidence is intact and sealed; the new one is open.
    assert [(e.event_type, e.sequence) for e in store.events_for_session(old_id)] == old_events
    assert store.session(old_id).ended_at is not None
    assert store.session(new_id).ended_at is None
    assert store.session(new_id).started_at >= store.session(old_id).started_at


def test_hack_a_stale_session_id_can_never_bring_back_a_reset_session(client):
    with client.websocket_connect("/ws/hack") as ws:
        old_id = _hack_open_new(ws)
    client.post(f"/api/sessions/hack/{old_id}/end")

    # A tab that somehow still remembered the old id gets a new session, never
    # the ended one.
    with client.websocket_connect(f"/ws/hack?session={old_id}") as ws:
        frame = _hack_open(ws)
        assert frame["resumed"] is False
        assert frame["session_id"] != old_id


def test_hack_quit_ends_the_session_and_creates_nothing(client):
    store = get_default_store()
    before = len(session_manager._sessions)
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _hack_open_new(ws)
        _hack_run(ws, NMAP)
        session = session_manager._sessions[session_id]
        assert client.post(f"/api/sessions/hack/{session_id}/end").json() == {"ended": True}

    assert len(session_manager._sessions) == before  # nothing replaced it
    assert session.recorder._finished is True
    ended_at = store.session(session_id).ended_at
    assert ended_at is not None
    assert client.get(f"/api/sessions/hack/{session_id}").json() == {"live": False}

    # Ending again — a double click, or the screen-change safety net after an
    # explicit Quit — is harmless and does not re-stamp the session.
    assert client.post(f"/api/sessions/hack/{session_id}/end").json() == {"ended": False}
    assert store.session(session_id).ended_at == ended_at
    assert [s.session_id for s in store.sessions()] == [session_id]


# --- Build Mode ---------------------------------------------------------------


def test_build_reload_keeps_the_same_session_and_its_work(client):
    store = get_default_store()
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        _edit(ws, EDIT)

    assert store.build_session(session_id).ended_at is None

    with client.websocket_connect(f"/ws/build?session={session_id}") as ws:
        state = _build_open_resumed(ws, session_id)
        assert _editable_text(state) == EDIT
        assert store.build_session(session_id).ended_at is None
    assert set(build_session_manager._sessions) == {session_id}  # no duplicate


def test_build_reset_ends_the_old_session_and_starts_a_completely_new_one(client):
    store = get_default_store()
    with client.websocket_connect("/ws/build") as ws:
        old_id, _ = _build_open_new(ws)
        edited = _edit(ws, EDIT)
        assert edited["dirty"] is True
        old = build_session_manager._sessions[old_id]
        assert client.post(f"/api/sessions/build/{old_id}/end").json() == {"ended": True}
    assert not build_session_manager.is_live(old_id)

    with client.websocket_connect("/ws/build") as ws:
        # `_build_open_new` itself asserts resumed is False and the bootstrap
        # frames of a brand new session.
        new_id, state = _build_open_new(ws)
        new = build_session_manager._sessions[new_id]

        assert new_id != old_id
        assert new is not old
        # Fresh mode state: the student's edit is gone, the workspace is the
        # pristine project, and the log holds only the new session's bootstrap.
        assert state["dirty"] is False
        assert _editable_text(state) != EDIT
        assert new.workspace is not old.workspace
        assert [e.type.value for e in new.events] == ["build_session_started", "workspace_loaded"]
        assert set(build_session_manager._sessions) == {new_id}

    # The ended session was finished properly; the new one is open.
    assert old.events[-1].type.value == "build_session_ended"
    assert store.build_session(old_id).ended_at is not None
    assert store.build_session(new_id).ended_at is None


def test_build_a_stale_session_id_can_never_bring_back_a_reset_session(client):
    with client.websocket_connect("/ws/build") as ws:
        old_id, _ = _build_open_new(ws)
        _edit(ws, EDIT)
    client.post(f"/api/sessions/build/{old_id}/end")

    with client.websocket_connect(f"/ws/build?session={old_id}") as ws:
        new_id, state = _build_open_new(ws)
        assert new_id != old_id
        assert state["dirty"] is False


def test_build_quit_ends_the_session_and_creates_nothing(client):
    store = get_default_store()
    before = len(build_session_manager._sessions)
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _build_open_new(ws)
        session = build_session_manager._sessions[session_id]
        assert client.post(f"/api/sessions/build/{session_id}/end").json() == {"ended": True}

    assert len(build_session_manager._sessions) == before
    assert session.recorder._finished is True
    ended_at = store.build_session(session_id).ended_at
    assert ended_at is not None
    assert client.post(f"/api/sessions/build/{session_id}/end").json() == {"ended": False}
    assert store.build_session(session_id).ended_at == ended_at


def test_resetting_one_mode_leaves_the_other_modes_session_running(client):
    with client.websocket_connect("/ws/hack") as hack, client.websocket_connect("/ws/build") as build:
        hack_id = _hack_open_new(hack)
        build_id, _ = _build_open_new(build)

        assert client.post(f"/api/sessions/hack/{hack_id}/end").json() == {"ended": True}
        assert build_session_manager.is_live(build_id)
        assert build_session_manager._sessions[build_id].recorder._finished is False
