"""Phase 2A backend verification.

Covers the whole Phase 2A contract: app startup, health, WebSocket lifecycle,
session creation/removal, the message protocol, input/resize handling,
controlled errors on malformed frames, and the standing security boundary
that no terminal input is ever executed as an OS command.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import tokenize

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.messages import PROTOCOL_VERSION
from app.sessions import SessionManager, session_manager


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open_session(ws) -> str:
    """Consume the session + banner frames and return the session id."""
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    banner = ws.receive_json()
    assert banner["type"] == "output"
    return session_frame["session_id"]


# --- 1 & 2: app starts, health responds -----------------------------------


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "iot-cybersecurity-trainer"
    assert body["protocol_version"] == PROTOCOL_VERSION


# --- 3, 4 & 5: connection accepted, session created and announced ----------


def test_websocket_announces_session_id(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
        assert session_id
        assert len(session_id) == 36  # uuid4


def test_sessions_are_isolated_per_connection(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as first:
        first_id = _open_session(first)
        with client.websocket_connect("/ws/hack") as second:
            second_id = _open_session(second)
            assert first_id != second_id


# --- 6: input receives a structured output acknowledgement ----------------


def test_input_message_is_acknowledged(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": "nmap -p 1883 192.168.4.0/24\r"})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert isinstance(reply["data"], str)


# --- 7: resize accepted safely --------------------------------------------


def test_resize_message_is_accepted(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "resize", "cols": 100, "rows": 30})
        reply = ws.receive_json()
        assert reply["type"] == "output"
        assert "100x30" in reply["data"]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "resize", "cols": 0, "rows": 30},
        {"type": "resize", "cols": -5, "rows": 30},
        {"type": "resize", "cols": 100, "rows": 0},
        {"type": "resize", "cols": 10**9, "rows": 30},
        {"type": "resize", "cols": "80", "rows": "24"},
    ],
)
def test_nonsensical_resize_is_rejected(client: TestClient, payload: dict) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json(payload)
        reply = ws.receive_json()
        assert reply["type"] == "error"


# --- 8: disconnect removes the session ------------------------------------


def test_disconnect_removes_session(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open_session(ws)
    # The endpoint's finally-block runs as the connection closes.
    assert session_manager._sessions.get(session_id) is None


def test_session_manager_lifecycle() -> None:
    """create / get / remove, exercised directly on the manager."""

    async def scenario() -> None:
        manager = SessionManager()
        session = await manager.create()
        assert await manager.get(session.session_id) is session
        assert await manager.count() == 1
        assert await manager.remove(session.session_id) is session
        assert await manager.get(session.session_id) is None
        assert await manager.remove(session.session_id) is None  # idempotent
        assert await manager.count() == 0

    asyncio.run(scenario())


# --- 9: invalid structures get a controlled error --------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[]",
        '"a string"',
        "123",
        json.dumps({"type": "unknown"}),
        json.dumps({"data": "missing type"}),
        json.dumps({"type": "input"}),  # missing data
        json.dumps({"type": "input", "data": "x", "extra": "forbidden"}),
        json.dumps({"type": "input", "data": 42}),
        json.dumps({"type": "input", "data": "x" * 9000}),  # oversized
    ],
)
def test_invalid_messages_produce_error_not_crash(client: TestClient, raw: str) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_text(raw)
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert isinstance(reply["message"], str) and reply["message"]

        # The connection survives a bad frame and still serves valid ones.
        ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        assert ws.receive_json()["type"] == "output"


def test_binary_frames_are_refused(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_bytes(b"\x00\x01\x02")
        reply = ws.receive_json()
        assert reply["type"] == "error"


# --- 10: no user input is executed as an OS command ------------------------

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

# Identifiers that can spawn or evaluate code. Checked as parsed NAME tokens
# rather than as substrings, so the prose in app/websocket.py that documents
# these as forbidden does not trip the guard — only real code does.
FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "spawnl",
        "spawnv",
        "eval",
        "exec",
        "compile",
        "shell",
    }
)


def _code_names(path: pathlib.Path) -> set[str]:
    """NAME tokens in a module, excluding comments and string literals."""
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def test_backend_source_contains_no_execution_primitives() -> None:
    """Static guard on the security boundary.

    Phase 2A only transports messages. This fails loudly if a later change
    introduces a process-spawning or code-evaluating primitive anywhere under
    app/.
    """
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        for name in sorted(_code_names(path) & FORBIDDEN_NAMES):
            offenders.append(f"{path.name}: {name}")
    assert offenders == [], f"execution primitive in backend source: {offenders}"


def test_shell_like_input_is_not_executed(client: TestClient) -> None:
    """Shell metacharacters are inert data on this channel.

    The acknowledgement must not contain the payload or any evidence of it
    having run — Phase 2A discards input after counting it.
    """
    payload = "; echo pwned > pwned.txt && whoami\r"
    marker = pathlib.Path.cwd() / "pwned.txt"
    existed_before = marker.exists()

    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        ws.send_json({"type": "input", "data": payload})
        reply = ws.receive_json()

    assert reply["type"] == "output"
    assert "pwned" not in reply["data"]
    assert "echo" not in reply["data"]
    assert marker.exists() == existed_before
