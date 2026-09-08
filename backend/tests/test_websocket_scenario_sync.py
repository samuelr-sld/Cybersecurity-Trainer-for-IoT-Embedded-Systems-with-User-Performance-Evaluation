"""Phase 2D-A verification: scenario event/state delivery over the WebSocket.

Covers what Phase 2D-A adds on top of the Phase 2A-2C baseline (still
exercised by tests/test_hack_backend.py, tests/test_command_router.py, and
tests/test_scenario_engine.py, which this file does not repeat): that a
command whose scenario events are non-empty gets one `event` frame per event
followed by one `state` frame, that a command with no scenario events sends
neither, that the delivered state always matches the session's own scenario
snapshot, and that two connections' event/state streams stay isolated.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scenarios import EnvironmentalMonitoringScenario
from app.sessions import session_manager

_T = EnvironmentalMonitoringScenario().state.target
TARGET_IP = _T.ip_address
TARGET_PORT = _T.mqtt_port
TARGET_TOPIC = _T.mqtt_topic


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open_session(ws) -> str:
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert ws.receive_json()["type"] == "output"
    return session_frame["session_id"]


def _send_input(ws, line: str) -> None:
    ws.send_json({"type": "input", "data": line + "\r"})


# --- 1: WebSocket connection still creates a session -----------------------


def test_connection_still_creates_a_session(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        assert session_id


# --- 2: existing banner still works -----------------------------------------


def test_banner_still_works(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_frame = ws.receive_json()
        assert session_frame["type"] == "session"
        banner = ws.receive_json()
        assert banner["type"] == "output"
        assert "hack mode channel established" in banner["data"]


# --- 3: input command still produces command output ------------------------


def test_input_still_produces_command_output(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _send_input(ws, "help")
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "Available commands:" in reply["data"]


# --- 4 & 5: scenario events and state are emitted when expected ------------


def test_firmware_extract_emits_one_event_then_state(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]

        _send_input(ws, "firmware-extract")
        output = ws.receive_json()
        assert output["type"] == "output"

        event = ws.receive_json()
        assert event["type"] == "event"
        assert event["event"] == "firmware_extracted"

        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["data"] == session.scenario.snapshot()
        assert state["data"]["discovery"]["firmware_extracted"] is True


def test_firmware_analyze_emits_three_events_then_one_state(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]

        _send_input(ws, "firmware-extract")
        for _ in range(3):  # output, event, state
            ws.receive_json()

        _send_input(ws, "firmware-analyze")
        assert ws.receive_json()["type"] == "output"

        events = [ws.receive_json() for _ in range(3)]
        assert all(frame["type"] == "event" for frame in events)
        assert [frame["event"] for frame in events] == [
            "firmware_analyzed",
            "broker_discovered",
            "topic_discovered",
        ]

        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["data"] == session.scenario.snapshot()
        assert state["data"]["discovery"]["broker_discovered"] is True
        assert state["data"]["discovery"]["topic_discovered"] is True


def test_help_and_clear_emit_no_event_or_state_frames(client: TestClient) -> None:
    """Commands that never touch the scenario stay exactly as before."""
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)

        _send_input(ws, "help")
        assert ws.receive_json()["type"] == "output"

        _send_input(ws, "clear")
        assert ws.receive_json() == {"type": "action", "action": "clear"}

        # Nothing else arrives before the next command's own reply.
        _send_input(ws, "help")
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "Available commands:" in reply["data"]


# --- 6: wrong MQTT topic does not mutate target state -----------------------


def test_wrong_topic_observe_does_not_mutate_state_or_emit_frames(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]
        before = session.scenario.snapshot()

        _send_input(ws, f"mosquitto_sub -h {TARGET_IP} -t bogus/topic")
        reply = ws.receive_json()
        assert reply["type"] == "output"

        assert session.scenario.snapshot() == before
        assert session.scenario.state.discovery.mqtt_observed is False

        # No event/state frame was queued in between: the very next frame is
        # the next command's own output, not a leftover from the failed one.
        _send_input(ws, "help")
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "Available commands:" in reply["data"]


# --- 7: successful spoof produces the correct event/state ------------------


def test_successful_spoof_emits_full_event_chain_and_final_state(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        session = session_manager._sessions[session_id]

        for line in (
            "firmware-extract",
            "firmware-analyze",
            f"mosquitto_sub -h {TARGET_IP} -t {TARGET_TOPIC}",
        ):
            _send_input(ws, line)
            while ws.receive_json()["type"] != "state":
                pass

        _send_input(ws, f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m temperature=150")
        assert ws.receive_json()["type"] == "output"

        events = [ws.receive_json() for _ in range(4)]
        assert all(frame["type"] == "event" for frame in events)
        assert [frame["event"] for frame in events] == [
            "spoof_attempted",
            "spoof_succeeded",
            "target_impacted",
            "attack_completed",
        ]

        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["data"] == session.scenario.snapshot()
        assert state["data"]["environment"]["temperature"] == 150
        assert state["data"]["attack"]["spoofed_temperature"] == 150
        assert state["data"]["completion"]["attack_successful"] is True


# --- 8: existing clear action still works -----------------------------------


def test_clear_action_still_works(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _send_input(ws, "clear")
        assert ws.receive_json() == {"type": "action", "action": "clear"}


# --- 9: resize still works --------------------------------------------------


def test_resize_still_works(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "resize", "cols": 100, "rows": 30})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "100x30" in reply["data"]


# --- 10: malformed frames remain rejected -----------------------------------


def test_malformed_frame_still_produces_a_controlled_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_text("not json at all")
        reply = ws.receive_json()
        assert reply["type"] == "error"

        # The session survives and still processes valid input afterwards.
        _send_input(ws, "help")
        assert ws.receive_json()["type"] == "output"


# --- 11: independent sessions retain independent scenario state ------------


def test_independent_sessions_retain_independent_scenario_state(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/hack") as first:
        first_id = _open_session(first)
        with client.websocket_connect("/ws/hack") as second:
            second_id = _open_session(second)

            first_session = session_manager._sessions[first_id]
            second_session = session_manager._sessions[second_id]

            _send_input(first, "firmware-extract")
            output = first.receive_json()
            assert output["type"] == "output"
            event = first.receive_json()
            assert event["type"] == "event"
            state = first.receive_json()
            assert state["type"] == "state"
            assert state["data"]["discovery"]["firmware_extracted"] is True

            # The second connection's scenario was never touched, and its
            # own commands produce no leftover frames from the first.
            assert second_session.scenario.state.discovery.firmware_extracted is False
            assert first_session.scenario.state.discovery.firmware_extracted is True

            _send_input(second, "help")
            reply = second.receive_json()
            assert reply["type"] == "output"
            assert "Available commands:" in reply["data"]
