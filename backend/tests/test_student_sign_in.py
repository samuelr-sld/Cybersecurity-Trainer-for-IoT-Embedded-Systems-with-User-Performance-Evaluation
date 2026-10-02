"""Student authentication: register with name + number, sign in with the number.

Registration is the only place a full name is supplied; sign-in takes the
student number alone and retrieves the stored participant. These tests pin
that at the service, the HTTP contract and the session-ownership seam, and
that participants stored before the change keep working untouched.
"""

from __future__ import annotations

import inspect
import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.events import SqliteEventStore, set_default_store
from app.main import app
from app.participants import (
    ParticipantError,
    ParticipantExistsError,
    ParticipantNotFoundError,
    ParticipantService,
    resolve_participant,
)

STUDENT_ID = "2023-123456"
STUDENT_NAME = "Ada Lovelace"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _register_over_http(client: TestClient, pid: str = STUDENT_ID, name: str = STUDENT_NAME):
    return client.post("/api/participants", json={"participant_id": pid, "full_name": name})


def _sign_in_over_http(client: TestClient, pid: object):
    return client.post("/api/participants/sign-in", json={"participant_id": pid})


# -- 1. registration with name + student number ---------------------------


def test_registration_stores_the_name_against_the_student_number(isolated_event_store) -> None:
    record = ParticipantService(isolated_event_store).register(STUDENT_ID, STUDENT_NAME)
    assert (record.participant_id, record.full_name) == (STUDENT_ID, STUDENT_NAME)
    stored = isolated_event_store.participant(STUDENT_ID)
    assert stored is not None and stored.full_name == STUDENT_NAME


def test_registration_still_requires_both_fields(client, isolated_event_store) -> None:
    assert client.post("/api/participants", json={"participant_id": STUDENT_ID}).status_code == 422
    assert client.post("/api/participants", json={"full_name": STUDENT_NAME}).status_code == 422
    assert _register_over_http(client, name="   ").status_code == 422
    assert _register_over_http(client, pid="not valid").status_code == 422
    assert isolated_event_store.participants() == ()


def test_registering_a_taken_number_does_not_replace_the_stored_name(
    client, isolated_event_store
) -> None:
    assert _register_over_http(client).status_code == 201
    assert _register_over_http(client, name="Someone Else").status_code == 409
    # The original association survived, and sign-in still returns it.
    assert _sign_in_over_http(client, STUDENT_ID).json()["full_name"] == STUDENT_NAME


# -- 2 + 3. sign-in with the student number only, and the lookup ----------


def test_sign_in_service_takes_only_the_student_number() -> None:
    assert list(inspect.signature(ParticipantService.sign_in).parameters) == [
        "self",
        "participant_id",
    ]


def test_sign_in_retrieves_the_registered_participant(isolated_event_store) -> None:
    service = ParticipantService(isolated_event_store)
    registered = service.register(STUDENT_ID, STUDENT_NAME)
    assert service.sign_in(STUDENT_ID) == registered
    assert service.sign_in(f"  {STUDENT_ID} ") == registered  # surrounding spaces are trimmed


def test_sign_in_over_http_needs_only_the_number_and_returns_the_stored_name(
    client, isolated_event_store
) -> None:
    _register_over_http(client)
    response = _sign_in_over_http(client, STUDENT_ID)
    assert response.status_code == 200
    body = response.json()
    assert body["participant_id"] == STUDENT_ID
    assert body["full_name"] == STUDENT_NAME
    assert body["registered_at"]


def test_sign_in_contract_no_longer_accepts_a_name(client, isolated_event_store) -> None:
    _register_over_http(client)
    with_name = client.post(
        "/api/participants/sign-in",
        json={"participant_id": STUDENT_ID, "full_name": STUDENT_NAME},
    )
    assert with_name.status_code == 422
    assert client.post("/api/participants/sign-in", json={}).status_code == 422


# -- 4. invalid / nonexistent student numbers ------------------------------


@pytest.mark.parametrize("pid", ["UNKNOWN-1", "", "   ", "a b", "x" * 40, "../etc", "id;drop", None])
def test_sign_in_refuses_an_unregistered_or_malformed_number(isolated_event_store, pid) -> None:
    ParticipantService(isolated_event_store).register(STUDENT_ID, STUDENT_NAME)
    with pytest.raises(ParticipantNotFoundError):
        ParticipantService(isolated_event_store).sign_in(pid)


def test_sign_in_of_an_unregistered_number_is_a_404_not_an_account(
    client, isolated_event_store
) -> None:
    for pid in ("UNKNOWN-1", "not valid", ""):
        assert _sign_in_over_http(client, pid).status_code == 404
    # Failed sign-ins create nothing.
    assert isolated_event_store.participants() == ()


def test_sign_in_is_not_a_registration(isolated_event_store) -> None:
    with pytest.raises(ParticipantNotFoundError):
        ParticipantService(isolated_event_store).sign_in(STUDENT_ID)
    with pytest.raises(ParticipantError):
        ParticipantService(isolated_event_store).register(STUDENT_ID, "")
    assert isolated_event_store.participant(STUDENT_ID) is None


def test_the_number_is_still_matched_exactly(isolated_event_store) -> None:
    """Number validation and matching are unchanged: only the name check went."""
    service = ParticipantService(isolated_event_store)
    service.register("Abc-123", STUDENT_NAME)
    assert service.sign_in("Abc-123").full_name == STUDENT_NAME
    with pytest.raises(ParticipantNotFoundError):
        service.sign_in("abc-123")
    with pytest.raises(ParticipantExistsError):
        service.register("Abc-123", "Another Name")


# -- 5. participants stored before this change -----------------------------


def _legacy_database(path: pathlib.Path) -> list[tuple]:
    """A database as the old flow left it: a participants table with rows."""
    rows = [
        ("2021-04213", "Grace Hopper", "2026-01-05T09:00:00+00:00"),
        ("TEST-STUDENT-01", "Test Student One", "2026-01-06T10:30:00+00:00"),
        ("2022-777", "Alan  Turing", "2026-01-07T11:15:00+00:00"),  # stored verbatim, odd spacing
    ]
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE participants (participant_id TEXT PRIMARY KEY, "
        "full_name TEXT NOT NULL, registered_at TEXT NOT NULL)"
    )
    connection.executemany("INSERT INTO participants VALUES (?, ?, ?)", rows)
    connection.commit()
    connection.close()
    return rows


def test_existing_participants_sign_in_by_number_and_are_not_modified(
    tmp_path: pathlib.Path, isolated_event_store
) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy_rows = _legacy_database(path)
    store = SqliteEventStore(path)
    try:
        service = ParticipantService(store)
        for pid, name, registered_at in legacy_rows:
            record = service.sign_in(pid)
            assert record.full_name == name
            assert record.participant_id == pid
            assert record.to_payload()["registered_at"] == registered_at
        assert service.resolve("2021-04213") == "2021-04213"
    finally:
        store.close()

    after = sqlite3.connect(path)
    try:
        assert after.execute(
            "SELECT participant_id, full_name, registered_at FROM participants "
            "ORDER BY rowid"
        ).fetchall() == legacy_rows
        columns = [row[1] for row in after.execute("PRAGMA table_info(participants)")]
    finally:
        after.close()
    assert columns == ["participant_id", "full_name", "registered_at"]  # schema untouched


def test_existing_participant_can_sign_in_over_http_and_own_a_session(
    client, tmp_path: pathlib.Path, isolated_event_store
) -> None:
    path = tmp_path / "legacy-http.sqlite3"
    _legacy_database(path)
    legacy = SqliteEventStore(path)
    set_default_store(legacy)
    try:
        response = _sign_in_over_http(client, "2021-04213")
        assert response.status_code == 200
        assert response.json()["full_name"] == "Grace Hopper"
        participant_id = response.json()["participant_id"]

        with client.websocket_connect(f"/ws/hack?participant={participant_id}") as ws:
            session_id = ws.receive_json()["session_id"]
        from app.sessions import session_manager

        session_manager.end(session_id)
        assert legacy.session(session_id).participant_id == "2021-04213"
    finally:
        from app.build_sessions import build_session_manager
        from app.sessions import session_manager

        for manager in (session_manager, build_session_manager):
            for live in list(manager._sessions):
                manager.end(live)
        set_default_store(isolated_event_store)
        legacy.close()


# -- 7. sessions are owned by the participant sign-in returned -------------


def test_sessions_are_attributed_to_the_participant_that_number_only_sign_in_returned(
    client, isolated_event_store
) -> None:
    _register_over_http(client)
    signed_in = _sign_in_over_http(client, STUDENT_ID).json()
    participant_id = signed_in["participant_id"]  # what the frontend hands the mode sockets

    with client.websocket_connect(f"/ws/hack?participant={participant_id}") as ws:
        hack_session = ws.receive_json()["session_id"]
    with client.websocket_connect(f"/ws/build?participant={participant_id}") as ws:
        build_session = ws.receive_json()["session_id"]

    from app.build_sessions import build_session_manager
    from app.sessions import session_manager

    session_manager.end(hack_session)
    build_session_manager.end(build_session)

    assert isolated_event_store.session(hack_session).participant_id == STUDENT_ID
    assert isolated_event_store.build_session(build_session).participant_id == STUDENT_ID

    listing = {row["participant_id"]: row for row in client.get("/api/participants").json()["participants"]}
    assert listing[STUDENT_ID]["full_name"] == STUDENT_NAME
    assert listing[STUDENT_ID]["hack_session_count"] == 1
    report = client.get(f"/api/evaluation/{STUDENT_ID}").json()
    assert report["participant"]["full_name"] == STUDENT_NAME
    assert [row["session_id"] for row in report["hack"]["sessions"]] == [hack_session]


def test_a_failed_sign_in_cannot_attribute_a_session(client, isolated_event_store) -> None:
    _register_over_http(client)
    assert _sign_in_over_http(client, "NOBODY-1").status_code == 404
    # The rejected number is not a participant, so a socket naming it stays unattributed.
    assert resolve_participant("NOBODY-1") is None
    with client.websocket_connect("/ws/hack?participant=NOBODY-1") as ws:
        session_id = ws.receive_json()["session_id"]
    assert isolated_event_store.session(session_id).participant_id is None
