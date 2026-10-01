"""Evaluation phase — participant ownership, aggregation, and the HTTP API.

Every metric asserted here is produced by the existing `app/metrics`
functions over rows the existing recorders persisted; these tests pin that
the Evaluation layer (`app/participants.py`, `app/evaluation.py`,
`app/evaluation_api.py`) attributes, selects and serialises them faithfully
— and never turns an absent value into a number.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.build.records import BuildAttemptRecord, BuildAttemptType, BuildSessionRecord
from app.build_sessions import BuildSession
from app.commands import CommandContext, default_router
from app.evaluation import EvaluationService
from app.events import SqliteEventStore
from app.events.records import HackSessionRecord
from app.main import app
from app.participants import (
    ParticipantError,
    ParticipantExistsError,
    ParticipantNotFoundError,
    ParticipantService,
    resolve_participant,
)
from app.sessions import HackSession, SessionManager

TEST_ID = "TEST-STUDENT-01"
TEST_NAME = "Test Student One"
PANEL_ONE_FLOW = (
    "esptool.py read_flash 0x0 0x400000 firmware.bin",
    "strings firmware.bin",
    "nmap 192.168.50.1",
    "mosquitto_sub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control",
    "mosquitto_pub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control -m START",
)
T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _register(store: SqliteEventStore, pid: str = TEST_ID, name: str = TEST_NAME):
    return ParticipantService(store).register(pid, name)


def _panel_one_session(session_id: str, participant_id: str | None, flow=PANEL_ONE_FLOW):
    """A real HackSession running the real Smart Home scenario (simulated MQTT)."""
    from app.scenarios import SmartHomeMQTTScenario

    session = HackSession(
        session_id=session_id, scenario=SmartHomeMQTTScenario(), participant_id=participant_id
    )
    session.recorder.start()
    for line in flow:
        asyncio.run(default_router.dispatch(line, CommandContext(session=session)))
    return session


# -- participants ---------------------------------------------------------


def test_register_and_sign_in_round_trip(isolated_event_store) -> None:
    service = ParticipantService(isolated_event_store)
    record = service.register(f"  {TEST_ID} ", "  Test   Student One ")
    assert record.participant_id == TEST_ID
    assert record.full_name == TEST_NAME
    assert service.sign_in(TEST_ID, "test student one").participant_id == TEST_ID


def test_duplicate_registration_is_refused(isolated_event_store) -> None:
    _register(isolated_event_store)
    with pytest.raises(ParticipantExistsError):
        _register(isolated_event_store, name="Someone Else")


@pytest.mark.parametrize("pid", ["", "a b", "x" * 40, "../etc", "id;drop", None])
def test_malformed_student_numbers_are_rejected(isolated_event_store, pid) -> None:
    with pytest.raises(ParticipantError):
        ParticipantService(isolated_event_store).register(pid, TEST_NAME)


def test_sign_in_requires_matching_name(isolated_event_store) -> None:
    _register(isolated_event_store)
    with pytest.raises(ParticipantNotFoundError):
        ParticipantService(isolated_event_store).sign_in(TEST_ID, "Wrong Name")
    with pytest.raises(ParticipantNotFoundError):
        ParticipantService(isolated_event_store).sign_in("UNKNOWN-1", TEST_NAME)


def test_resolve_participant_never_guesses(isolated_event_store) -> None:
    _register(isolated_event_store)
    assert resolve_participant(TEST_ID) == TEST_ID
    assert resolve_participant("NOT-REGISTERED") is None
    assert resolve_participant("bad id!") is None
    assert resolve_participant(None) is None
    assert resolve_participant("") is None


# -- ownership over the real WebSocket lifecycles -------------------------


def test_hack_websocket_attributes_the_session_to_the_participant(
    client, isolated_event_store
) -> None:
    _register(isolated_event_store)
    with client.websocket_connect(f"/ws/hack?participant={TEST_ID}") as ws:
        session_id = ws.receive_json()["session_id"]
    # A closing socket only detaches; ending the session (explicitly or by the
    # grace period) is what stamps it.
    from app.sessions import session_manager

    session_manager.end(session_id)
    record = isolated_event_store.session(session_id)
    assert record.participant_id == TEST_ID
    assert record.ended_at is not None  # ending the session stamped it


def test_hack_websocket_without_or_with_unknown_participant_stays_unattributed(
    client, isolated_event_store
) -> None:
    for url in ("/ws/hack", "/ws/hack?participant=NOBODY-1"):
        with client.websocket_connect(url) as ws:
            session_id = ws.receive_json()["session_id"]
        assert isolated_event_store.session(session_id).participant_id is None


def test_build_websocket_attributes_the_session_to_the_participant(
    client, isolated_event_store
) -> None:
    _register(isolated_event_store)
    with client.websocket_connect(f"/ws/build?participant={TEST_ID}") as ws:
        session_id = ws.receive_json()["session_id"]
    record = isolated_event_store.build_session(session_id)
    assert record.participant_id == TEST_ID
    assert isolated_event_store.build_sessions_for_participant(TEST_ID)[0].session_id == session_id


def test_session_manager_passes_participant_to_the_recorder() -> None:
    session = asyncio.run(SessionManager().create(participant_id=TEST_ID))
    assert session.participant_id == TEST_ID
    assert session.recorder.participant_id == TEST_ID


def test_build_session_participant_reaches_the_record(isolated_event_store) -> None:
    session = BuildSession(session_id="b-1", participant_id=TEST_ID)
    session.recorder.start()
    assert isolated_event_store.build_session("b-1").participant_id == TEST_ID


def test_old_database_gains_the_build_participant_column(tmp_path: pathlib.Path) -> None:
    import sqlite3

    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE build_sessions (session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "ended_at TEXT, panel_id TEXT)"
    )
    connection.execute(
        "INSERT INTO build_sessions VALUES ('old', '2026-01-01T00:00:00+00:00', NULL, NULL)"
    )
    connection.commit()
    connection.close()
    store = SqliteEventStore(path)
    try:
        assert store.build_session("old").participant_id is None
    finally:
        store.close()


# -- aggregation ----------------------------------------------------------


def test_no_sessions_is_an_empty_report_not_zeroes(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    report = EvaluationService(isolated_event_store).report(participant)
    assert report["participant"]["participant_id"] == TEST_ID
    assert report["hack"] == {"sessions": [], "activities": []}
    assert report["build"] == {"sessions": []}


def test_completed_panel_one_session_reports_the_existing_metrics(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    session = _panel_one_session("hack-1", TEST_ID)
    session.recorder.finish()

    report = EvaluationService(isolated_event_store).report(participant)
    (row,) = report["hack"]["sessions"]
    assert row["status"] == "completed"
    assert row["panel_id"] == "smart-home-mqtt-control"
    assert row["panel_name"] == "SMART HOME MQTT CONTROL SYSTEM"
    assert row["command_count"] == len(PANEL_ONE_FLOW)
    assert "attack_completed" in row["events"]
    assert row["metrics"]["ACR"]["status"] == "computed"
    assert row["metrics"]["ACR"]["value"] == pytest.approx(100.0)
    assert row["metrics"]["RE"]["value"] == pytest.approx(100.0)
    assert row["metrics"]["TTE"]["status"] == "computed"
    assert row["metrics"]["TTE"]["value"] >= 0
    (activity,) = report["hack"]["activities"]
    assert activity["metrics"]["EAC"] == {
        **activity["metrics"]["EAC"],
        "status": "computed",
        "value": 1,
    }


def test_incomplete_and_in_progress_sessions_are_not_zeroed(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    ended = _panel_one_session("hack-a", TEST_ID, flow=PANEL_ONE_FLOW[:2])
    ended.recorder.finish()
    live = _panel_one_session("hack-b", TEST_ID, flow=PANEL_ONE_FLOW[:1])

    service = EvaluationService(isolated_event_store, is_hack_live=lambda sid: sid == "hack-b")
    rows = {r["session_id"]: r for r in service.report(participant)["hack"]["sessions"]}

    assert rows["hack-a"]["status"] == "incomplete"
    assert rows["hack-a"]["metrics"]["TTE"]["status"] == "not_applicable"
    assert rows["hack-a"]["metrics"]["TTE"]["value"] is None
    assert rows["hack-a"]["metrics"]["RE"]["status"] == "not_applicable"
    # ACR is always reported for a session with declared objectives. The
    # `strings` dump also surfaces the broker and topic, so extract + analyze
    # + discover = 3/5.
    assert rows["hack-a"]["metrics"]["ACR"]["value"] == pytest.approx(60.0)
    assert rows["hack-a"]["metrics"]["ACR"]["detail"] == "3/5 declared objectives completed"
    assert rows[live.session_id]["status"] == "in_progress"
    assert rows[live.session_id]["metrics"]["TTE"]["status"] == "not_yet_computable"
    eac = service.report(participant)["hack"]["activities"][0]["metrics"]["EAC"]
    assert eac["status"] == "not_yet_computable" and eac["value"] is None


def test_unclosed_session_not_being_served_is_interrupted(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    _panel_one_session("hack-crashed", TEST_ID, flow=PANEL_ONE_FLOW[:1])
    row = EvaluationService(isolated_event_store).report(participant)["hack"]["sessions"][0]
    assert row["status"] == "interrupted"


def test_eac_counts_sessions_until_first_success(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    first = _panel_one_session("try-1", TEST_ID, flow=PANEL_ONE_FLOW[:3])
    first.recorder.finish()
    second = _panel_one_session("try-2", TEST_ID)
    second.recorder.finish()

    report = EvaluationService(isolated_event_store).report(participant)
    assert report["hack"]["activities"][0]["metrics"]["EAC"]["value"] == 2
    assert report["hack"]["activities"][0]["session_count"] == 2
    # Newest first for display.
    assert [r["session_id"] for r in report["hack"]["sessions"]] == ["try-2", "try-1"]


def test_sessions_are_isolated_per_participant(isolated_event_store) -> None:
    alice = _register(isolated_event_store, "ALICE-1", "Alice")
    _register(isolated_event_store, "BOB-1", "Bob")
    _panel_one_session("bob-session", "BOB-1").recorder.finish()
    _panel_one_session("anon-session", None).recorder.finish()

    report = EvaluationService(isolated_event_store).report(alice)
    assert report["hack"]["sessions"] == []
    assert report["hack"]["activities"] == []


def test_scenario_without_a_package_gets_na_acr_re(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    isolated_event_store.open_session(
        HackSessionRecord(
            session_id="env", scenario_id="environmental-monitoring",
            started_at=T0, ended_at=T0 + timedelta(minutes=1), participant_id=TEST_ID,
        )
    )
    row = EvaluationService(isolated_event_store).report(participant)["hack"]["sessions"][0]
    assert row["panel_id"] is None
    assert row["metrics"]["ACR"]["status"] == "not_applicable"
    assert row["metrics"]["RE"]["status"] == "not_applicable"
    assert "no panel package" in row["metrics"]["ACR"]["detail"]


def _build(store, session_id, attempts, *, ended=True):
    store.open_build_session(
        BuildSessionRecord(
            session_id=session_id, started_at=T0, panel_id="smart-home-mqtt-control",
            participant_id=TEST_ID,
        )
    )
    for seq, (kind, ok, minutes) in enumerate(attempts, start=1):
        store.append_build_attempt(
            BuildAttemptRecord(
                session_id=session_id, sequence=seq, attempt_type=kind, success=ok,
                occurred_at=T0 + timedelta(minutes=minutes),
            )
        )
    if ended:
        store.close_build_session(session_id, T0 + timedelta(minutes=10))


def test_build_metrics_come_from_recorded_attempts(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    _build(
        isolated_event_store,
        "build-1",
        [
            (BuildAttemptType.COMPILE, False, 1),
            (BuildAttemptType.COMPILE, True, 3),
            (BuildAttemptType.FLASH, True, 4),
            (BuildAttemptType.VALIDATION, True, 5),
        ],
    )
    (row,) = EvaluationService(isolated_event_store).report(participant)["build"]["sessions"]
    assert row["status"] == "completed"
    assert row["panel_name"] == "SMART HOME MQTT CONTROL SYSTEM"
    assert row["attempts"]["compile"] == {"total": 2, "succeeded": 1}
    assert row["metrics"]["TTR"]["value"] == pytest.approx(300.0)
    assert row["metrics"]["AID"]["value"] == pytest.approx(4 / 10)
    assert row["metrics"]["DEI"]["value"] == pytest.approx(120.0)


def test_build_session_without_validation_is_not_given_a_ttr(isolated_event_store) -> None:
    participant = _register(isolated_event_store)
    _build(isolated_event_store, "build-2", [(BuildAttemptType.COMPILE, True, 1)])
    _build(isolated_event_store, "build-3", [], ended=False)
    service = EvaluationService(isolated_event_store, is_build_live=lambda sid: sid == "build-3")
    rows = {r["session_id"]: r for r in service.report(participant)["build"]["sessions"]}
    assert rows["build-2"]["status"] == "incomplete"
    assert rows["build-2"]["metrics"]["TTR"]["status"] == "not_applicable"
    assert rows["build-2"]["metrics"]["DEI"]["status"] == "not_applicable"
    assert rows["build-2"]["metrics"]["DEI"]["value"] is None
    assert rows["build-3"]["status"] == "in_progress"
    assert rows["build-3"]["metrics"]["AID"]["status"] == "not_yet_computable"


# -- HTTP API -------------------------------------------------------------


def test_api_register_sign_in_and_empty_evaluation(client) -> None:
    body = {"participant_id": TEST_ID, "full_name": TEST_NAME}
    assert client.post("/api/participants", json=body).status_code == 201
    assert client.post("/api/participants", json=body).status_code == 409
    assert client.post("/api/participants/sign-in", json=body).json()["participant_id"] == TEST_ID
    assert (
        client.post(
            "/api/participants/sign-in", json={**body, "full_name": "Nope"}
        ).status_code
        == 404
    )
    assert client.post(
        "/api/participants", json={"participant_id": "bad id", "full_name": "x"}
    ).status_code == 422
    assert client.post("/api/participants", json={**body, "extra": 1}).status_code == 422

    report = client.get(f"/api/evaluation/{TEST_ID}").json()
    assert report["hack"]["sessions"] == [] and report["build"]["sessions"] == []
    assert client.get("/api/evaluation/UNKNOWN-9").status_code == 404


def test_api_evaluation_reflects_a_real_hack_websocket_session(
    client, isolated_event_store
) -> None:
    client.post("/api/participants", json={"participant_id": TEST_ID, "full_name": TEST_NAME})
    with client.websocket_connect(f"/ws/hack?participant={TEST_ID}") as ws:
        session_id = ws.receive_json()["session_id"]
    from app.sessions import session_manager

    session_manager.end(session_id)  # detached until ended; now it is over

    listing = client.get("/api/participants").json()["participants"]
    assert listing[0]["participant_id"] == TEST_ID
    assert listing[0]["hack_session_count"] == 1
    assert listing[0]["last_activity"] is not None

    (row,) = client.get(f"/api/evaluation/{TEST_ID}").json()["hack"]["sessions"]
    assert row["session_id"] == session_id
    assert row["status"] == "incomplete"
    assert row["command_count"] == 0
    assert row["metrics"]["TTE"]["value"] is None


def test_api_exposes_no_command_arguments_or_secrets(client, isolated_event_store) -> None:
    _register(isolated_event_store)
    _panel_one_session("hack-secret", TEST_ID).recorder.finish()
    text = client.get(f"/api/evaluation/{TEST_ID}").text
    for forbidden in ("argv", "password", "TRAINER_LAB_", "192.168.50.1", "cybertrainer/"):
        assert forbidden not in text
