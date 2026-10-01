"""Phase 2B verification: the Hack Mode event + timestamp foundation.

What this file is asserting, in one sentence: that the backend — never the
terminal — decides what happened, stamps it with its own clock, files it
under the session that caused it, and can hand it back later in order.

The lettered sections match the Phase 2B acceptance tests:

    B  a new session starts with a clean, isolated log
    C  one real action produces one correct, server-timestamped event
    D  several actions are stored in chronological order
    E  a failed action records the failure and never the success
    F  two sessions never see each other's events
    G  the existing Environmental Monitoring flow still works end to end,
       and the events land at the right transitions

Everything here drives the real command router and the real scenario engine.
Nothing stubs the scenario, because the point of Phase 2B is that the events
come from actual backend state transitions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.commands import CommandContext, CommandResult, CommandRouter, build_default_registry
from app.events import (
    MEMORY_PATH,
    HackEventRecorder,
    SqliteEventStore,
    StoreError,
    from_iso,
    to_iso,
    utc_now,
)
from app.main import app
from app.scenarios import EnvironmentalMonitoringScenario
from app.sessions import HackSession, session_manager

_T = EnvironmentalMonitoringScenario().state.target
TARGET_IP = _T.ip_address
TARGET_PORT = _T.mqtt_port
TARGET_TOPIC = _T.mqtt_topic

#: The full intended attack chain, as the courseware teaches it.
FULL_FLOW = (
    "esptool.py read_flash 0x0 0x400000 firmware.bin",
    "strings firmware.bin",
    f"nmap -p {TARGET_PORT} {TARGET_IP}",
    f"mosquitto_sub -h {TARGET_IP} -t {TARGET_TOPIC}",
    # Single-quoted JSON, the way the courseware types it: the parser has no
    # backslash escapes, so the outer quotes must differ from the inner ones.
    f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m '{{\"temperature\": 99}}'",
)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def router() -> CommandRouter:
    return CommandRouter(build_default_registry())


def new_session(session_id: str, store: SqliteEventStore) -> HackSession:
    """A session whose recorder writes to the test's own store."""
    session = HackSession(session_id=session_id)
    session.recorder._store = store  # the injection seam; see HackEventRecorder
    session.recorder.start()
    return session


def run(router: CommandRouter, line: str, session: HackSession) -> CommandResult:
    return asyncio.run(router.dispatch(line, CommandContext(session=session)))


def run_all(router: CommandRouter, lines, session: HackSession) -> None:
    for line in lines:
        run(router, line, session)


def types_of(records) -> list[str]:
    return [record.event_type for record in records]


# --- B: a new session starts clean ----------------------------------------


def test_new_session_has_an_id_and_an_empty_log(isolated_event_store) -> None:
    session = new_session("session-b", isolated_event_store)

    assert session.session_id == "session-b"
    assert session.recorder.session_id == "session-b"
    assert session.recorder.events == ()
    assert session.recorder.commands == ()
    assert isolated_event_store.events_for_session("session-b") == ()
    assert isolated_event_store.commands_for_session("session-b") == ()


def test_new_session_is_opened_in_the_log_with_a_server_start_time(
    isolated_event_store,
) -> None:
    """The TTE anchor exists from connect, before any command is typed."""
    before = utc_now()
    session = new_session("session-b2", isolated_event_store)
    after = utc_now()

    header = isolated_event_store.session("session-b2")
    assert header is not None
    assert header.scenario_id == session.scenario.scenario_id
    assert before <= header.started_at <= after
    assert header.ended_at is None


def test_a_fresh_session_does_not_inherit_an_earlier_sessions_events(
    router: CommandRouter, isolated_event_store
) -> None:
    first = new_session("earlier", isolated_event_store)
    run_all(router, FULL_FLOW, first)
    assert first.recorder.events  # the earlier session really did log things

    second = new_session("later", isolated_event_store)
    assert second.recorder.events == ()
    assert second.recorder.commands == ()
    assert isolated_event_store.events_for_session("later") == ()


def test_reconnecting_over_the_websocket_starts_a_new_empty_log(
    client: TestClient,
) -> None:
    """Two consecutive connections are two sessions, not a continuation."""
    ids = []
    for _ in range(2):
        with client.websocket_connect("/ws/hack") as ws:
            ids.append(_open(ws))
            _send(ws, "esptool.py read_flash 0x0 0x400000 firmware.bin")
            _drain(ws)
    assert ids[0] != ids[1]


# --- C: one action produces one correct, timestamped event ----------------


def test_one_action_records_exactly_one_event_of_the_right_type(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-c", isolated_event_store)
    before = utc_now()
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", session)
    after = utc_now()

    events = session.recorder.events
    assert len(events) == 1
    event = events[0]

    assert event.event_type == "firmware_extracted"
    assert event.session_id == "session-c"
    assert event.command == "esptool.py"
    assert event.exit_code == 0
    # Server-generated: it falls inside the window this test measured around
    # the dispatch, and nothing in the request could have supplied it.
    assert before <= event.occurred_at <= after


def test_event_timestamps_are_timezone_aware_utc(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-c2", isolated_event_store)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", session)

    stamp = session.recorder.events[0].occurred_at
    assert stamp.tzinfo is not None, "a naive timestamp has no defined meaning"
    assert stamp.utcoffset() == timedelta(0)
    # One representation, round-trippable through storage and the wire.
    assert from_iso(to_iso(stamp)) == stamp


def test_the_event_is_persisted_and_reads_back_identically(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-c3", isolated_event_store)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", session)

    stored = isolated_event_store.events_for_session("session-c3")
    assert len(stored) == 1
    assert stored[0].event_type == "firmware_extracted"
    assert stored[0].session_id == "session-c3"
    assert stored[0].command == "esptool.py"
    # The durable copy and the live copy are the same row.
    assert stored[0].occurred_at == session.recorder.events[0].occurred_at
    assert stored[0].sequence == session.recorder.events[0].sequence


def test_the_command_itself_is_recorded_with_its_arguments(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-c4", isolated_event_store)
    run(router, f"nmap -p {TARGET_PORT} {TARGET_IP}", session)

    commands = isolated_event_store.commands_for_session("session-c4")
    assert len(commands) == 1
    assert commands[0].name == "nmap"
    assert commands[0].argv == ("nmap", "-p", str(TARGET_PORT), TARGET_IP)
    assert commands[0].exit_code == 0
    assert commands[0].handled is True


def test_opening_hack_mode_records_no_event_at_all(client: TestClient) -> None:
    """Section 7: entering Hack Mode is not `firmware_extracted`."""
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        session = session_manager._sessions[session_id]
        assert session.recorder.events == ()
        assert session.recorder.commands == ()


def test_polling_hardware_status_records_nothing(client: TestClient) -> None:
    """Device presence is infrastructure, not student activity.

    This is the Phase 1 silence guarantee restated at the event layer: a
    client may poll for the life of a session without adding a row.
    """
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        for _ in range(3):
            ws.send_json({"type": "hardware_status"})
            assert ws.receive_json()["type"] == "hardware"

        session = session_manager._sessions[session_id]
        assert session.recorder.events == ()
        assert session.recorder.commands == ()


# --- D: several actions, chronological order ------------------------------


def test_all_events_of_a_full_flow_are_stored_in_order(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-d", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    stored = isolated_event_store.events_for_session("session-d")
    assert types_of(stored) == types_of(session.recorder.events)
    assert types_of(stored) == [
        "firmware_extracted",
        "firmware_analyzed",
        "broker_discovered",
        "topic_discovered",
        "scan",
        "mqtt_observed",
        "spoof_attempted",
        "spoof_succeeded",
        "target_impacted",
        "attack_completed",
    ]


def test_sequences_are_strictly_increasing_and_timestamps_never_go_backwards(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-d2", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    stored = isolated_event_store.events_for_session("session-d2")
    sequences = [record.sequence for record in stored]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)

    stamps = [record.occurred_at for record in stored]
    assert stamps == sorted(stamps)
    assert all(stamp.tzinfo is not None for stamp in stamps)


def test_commands_and_events_share_one_ordering(
    router: CommandRouter, isolated_event_store
) -> None:
    """A command must sort before the transitions it caused."""
    session = new_session("session-d3", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    timeline = isolated_event_store.timeline_for_session("session-d3")
    sequences = [record.sequence for record in timeline]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)

    # The very first row is the first command, not one of its events.
    assert timeline[0].sequence == 1
    assert getattr(timeline[0], "name", None) == "esptool.py"
    assert timeline[1].event_type == "firmware_extracted"


def test_every_stored_event_carries_the_owning_session(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-d4", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    stored = isolated_event_store.events_for_session("session-d4")
    assert {record.session_id for record in stored} == {"session-d4"}
    assert all(record.occurred_at is not None for record in stored)


def test_repeating_a_completed_step_adds_no_duplicate_event(
    router: CommandRouter, isolated_event_store
) -> None:
    """Events mark transitions, not command invocations.

    Running `esptool.py read_flash` twice is two actions but one transition — the
    second run has nothing left to change. The command log records both; the
    event log records one.
    """
    session = new_session("session-d5", isolated_event_store)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", session)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", session)

    assert types_of(isolated_event_store.events_for_session("session-d5")) == [
        "firmware_extracted"
    ]
    assert len(isolated_event_store.commands_for_session("session-d5")) == 2


# --- E: a failed action is recorded as a failure --------------------------


def test_a_spoof_to_the_wrong_topic_is_attempted_and_rejected_never_succeeded(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-e", isolated_event_store)
    run_all(router, FULL_FLOW[:4], session)
    run(
        router,
        f'mosquitto_pub -h {TARGET_IP} -t wrong/topic -m "{{\\"temperature\\": 99}}"',
        session,
    )

    recorded = types_of(isolated_event_store.events_for_session("session-e"))
    assert "spoof_attempted" in recorded
    assert "spoof_rejected" in recorded
    assert "spoof_succeeded" not in recorded
    assert "target_impacted" not in recorded
    assert "attack_completed" not in recorded


def test_a_spoof_with_no_usable_reading_is_rejected(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-e2", isolated_event_store)
    run_all(router, FULL_FLOW[:4], session)
    run(router, f'mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m "hello"', session)

    recorded = types_of(isolated_event_store.events_for_session("session-e2"))
    assert recorded[-2:] == ["spoof_attempted", "spoof_rejected"]
    assert "spoof_succeeded" not in recorded


def test_a_scan_against_the_wrong_host_records_no_discovery(
    router: CommandRouter, isolated_event_store
) -> None:
    """Section 7: seeing a target is not `broker_discovered`."""
    session = new_session("session-e3", isolated_event_store)
    result = run(router, "nmap 10.0.0.1", session)

    assert result.exit_code != 0
    assert isolated_event_store.events_for_session("session-e3") == ()
    # The attempt is still on record — it is effort, and it failed.
    commands = isolated_event_store.commands_for_session("session-e3")
    assert len(commands) == 1
    assert commands[0].name == "nmap"
    assert commands[0].exit_code != 0
    assert commands[0].handled is True


def test_an_unknown_command_is_recorded_as_unhandled(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-e4", isolated_event_store)
    run(router, "sudo rm -rf /", session)

    commands = isolated_event_store.commands_for_session("session-e4")
    assert len(commands) == 1
    assert commands[0].name == "sudo"
    assert commands[0].handled is False
    assert commands[0].exit_code == 127
    assert isolated_event_store.events_for_session("session-e4") == ()


def test_an_unparseable_line_is_recorded_without_a_name(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-e5", isolated_event_store)
    run(router, "nmap 192.168.10.10 && whoami", session)

    commands = isolated_event_store.commands_for_session("session-e5")
    assert len(commands) == 1
    assert commands[0].name is None
    assert commands[0].argv == ()
    assert commands[0].handled is False
    assert isolated_event_store.events_for_session("session-e5") == ()


def test_a_blank_line_records_nothing(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-e6", isolated_event_store)
    for blank in ("", "   ", "\r\n"):
        run(router, blank, session)

    assert isolated_event_store.commands_for_session("session-e6") == ()
    assert isolated_event_store.events_for_session("session-e6") == ()


# --- F: session isolation --------------------------------------------------


def test_two_sessions_do_not_share_events(
    router: CommandRouter, isolated_event_store
) -> None:
    a = new_session("iso-a", isolated_event_store)
    b = new_session("iso-b", isolated_event_store)

    run_all(router, FULL_FLOW, a)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", b)

    a_events = isolated_event_store.events_for_session("iso-a")
    b_events = isolated_event_store.events_for_session("iso-b")

    assert types_of(b_events) == ["firmware_extracted"]
    assert len(a_events) > len(b_events)
    assert {record.session_id for record in a_events} == {"iso-a"}
    assert {record.session_id for record in b_events} == {"iso-b"}
    assert "attack_completed" in types_of(a_events)
    assert "attack_completed" not in types_of(b_events)


def test_one_sessions_progress_does_not_advance_anothers(
    router: CommandRouter, isolated_event_store
) -> None:
    """Isolation of state, not just of rows."""
    a = new_session("iso-c", isolated_event_store)
    b = new_session("iso-d", isolated_event_store)

    run_all(router, FULL_FLOW, a)

    assert a.scenario.state.completion.attack_successful is True
    assert b.scenario.state.completion.attack_successful is False
    assert b.scenario.state.discovery.firmware_extracted is False
    assert b.recorder.events == ()


def test_websocket_sessions_are_isolated_end_to_end(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as first:
        first_id = _open(first)
        _send(first, "esptool.py read_flash 0x0 0x400000 firmware.bin")
        _drain(first)

        with client.websocket_connect("/ws/hack") as second:
            second_id = _open(second)
            assert second_id != first_id

            a = session_manager._sessions[first_id]
            b = session_manager._sessions[second_id]

            assert types_of(a.recorder.events) == ["firmware_extracted"]
            assert b.recorder.events == ()


# --- G: the existing hack flow still works --------------------------------


def test_the_full_flow_still_reaches_the_existing_success_condition(
    router: CommandRouter, isolated_event_store
) -> None:
    """The Phase 2C scenario logic is untouched; 2B only observes it."""
    session = new_session("session-g", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    state = session.scenario.state
    assert state.discovery.firmware_analyzed is True
    assert state.discovery.broker_discovered is True
    assert state.discovery.topic_discovered is True
    assert state.discovery.mqtt_observed is True
    assert state.attack.spoof_successful is True
    assert state.completion.attack_successful is True
    assert state.environment.temperature == 99
    # Untouched fields stay untouched — only temperature is spoofable.
    assert state.environment.humidity == 65
    assert state.environment.pressure == 1008


def test_the_recorded_log_matches_the_engines_own_event_list(
    router: CommandRouter, isolated_event_store
) -> None:
    """The recorder adds a stamp and a session; it invents nothing.

    Same events, same order, same vocabulary as `Scenario.events` — which is
    what proves Phase 2B did not introduce a second, parallel event system.
    """
    session = new_session("session-g2", isolated_event_store)
    run_all(router, FULL_FLOW, session)

    engine_types = [event.type.value for event in session.scenario.events]
    assert types_of(isolated_event_store.events_for_session("session-g2")) == engine_types


def test_gating_still_holds_analyze_requires_extract(
    router: CommandRouter, isolated_event_store
) -> None:
    session = new_session("session-g3", isolated_event_store)
    result = run(router, "strings firmware.bin", session)

    assert result.exit_code != 0
    assert isolated_event_store.events_for_session("session-g3") == ()
    assert session.scenario.state.discovery.firmware_analyzed is False


def test_the_full_flow_over_the_websocket_delivers_server_timestamps(
    client: TestClient,
) -> None:
    """Section 5 end to end: what the UI shows is what the log holds."""
    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        events = []
        for line in FULL_FLOW:
            _send(ws, line)
            events.extend(_drain(ws))

        assert [frame["event"] for frame in events][-1] == "attack_completed"
        for frame in events:
            assert frame["occurred_at"], "every event frame carries a server stamp"
            assert frame["sequence"] is not None
            stamp = from_iso(frame["occurred_at"])
            assert stamp.tzinfo is not None
            assert stamp.utcoffset() == timedelta(0)

        session = session_manager._sessions[session_id]
        # The frames and the session's own log are the same rows.
        assert [frame["event"] for frame in events] == types_of(session.recorder.events)
        assert [frame["sequence"] for frame in events] == [
            record.sequence for record in session.recorder.events
        ]


def test_disconnect_stamps_the_session_as_ended(client: TestClient) -> None:
    from app.events import get_default_store

    with client.websocket_connect("/ws/hack") as ws:
        session_id = _open(ws)
        _send(ws, "esptool.py read_flash 0x0 0x400000 firmware.bin")
        _drain(ws)

    # Disconnect detaches; ending the session (explicit, or grace expiry) stamps it.
    session_manager.end(session_id)
    header = get_default_store().session(session_id)
    assert header is not None
    assert header.ended_at is not None
    assert header.ended_at >= header.started_at
    # The events outlive the connection — that is what "persistent" means.
    assert types_of(get_default_store().events_for_session(session_id)) == [
        "firmware_extracted"
    ]


# --- storage behaviour ------------------------------------------------------


def test_records_survive_reopening_the_database(tmp_path, router: CommandRouter) -> None:
    """Phase 2E will read these rows in a different process."""
    path = tmp_path / "events.sqlite3"
    store = SqliteEventStore(path)
    session = new_session("durable", store)
    run_all(router, FULL_FLOW, session)
    store.close()

    reopened = SqliteEventStore(path)
    try:
        assert "attack_completed" in types_of(reopened.events_for_session("durable"))
        assert reopened.session("durable") is not None
        assert len(reopened.commands_for_session("durable")) == len(FULL_FLOW)
    finally:
        reopened.close()


def test_a_broken_store_never_breaks_the_terminal(router: CommandRouter) -> None:
    """Recording is best-effort; the exercise is not.

    A full disk must cost the durable copy of a session, not the session.
    """

    class _BrokenStore:
        def open_session(self, record):
            raise StoreError("disk is full")

        def append_command(self, record):
            raise StoreError("disk is full")

        def append_event(self, record):
            raise StoreError("disk is full")

        def close_session(self, session_id, ended_at):
            raise StoreError("disk is full")

    session = HackSession(session_id="broken")
    session.recorder._store = _BrokenStore()

    for line in FULL_FLOW:
        result = run(router, line, session)
        assert "internal error" not in " ".join(result.lines)

    # The exercise completed normally and the live log is intact...
    assert session.scenario.state.completion.attack_successful is True
    assert "attack_completed" in types_of(session.recorder.events)
    # ...only the durable copy was lost, and that was noticed rather than hidden.
    assert session.recorder.store_failures > 0
    session.recorder.finish()


def test_the_clock_refuses_a_naive_timestamp() -> None:
    """A naive stamp has no defined meaning, so it is never stored."""
    with pytest.raises(ValueError):
        to_iso(datetime(2026, 1, 1, 12, 0, 0))
    assert to_iso(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)).endswith("+00:00")


def test_reads_are_scoped_to_one_session(router: CommandRouter) -> None:
    """There is no way to ask this store for 'all events'."""
    store = SqliteEventStore(MEMORY_PATH)
    try:
        run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", new_session("scope-a", store))
        run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", new_session("scope-b", store))

        assert len(store.events_for_session("scope-a")) == 1
        assert len(store.events_for_session("scope-b")) == 1
        assert store.events_for_session("scope-missing") == ()
        assert set(store.session_ids()) == {"scope-a", "scope-b"}
    finally:
        store.close()


def test_a_recorder_without_an_opened_session_still_records(
    isolated_event_store,
) -> None:
    """A directly-built recorder must not silently discard rows.

    There is no foreign key between the header and the rows, on purpose —
    see `SqliteEventStore`. A missing header is a missing header, not a
    reason to lose what the student did.
    """
    recorder = HackEventRecorder("orphan", store=isolated_event_store)
    recorder.record_command("nmap", ("nmap", TARGET_IP), 0, handled=True)

    assert len(isolated_event_store.commands_for_session("orphan")) == 1


# --- websocket helpers ------------------------------------------------------


def _open(ws) -> str:
    frame = ws.receive_json()
    assert frame["type"] == "session"
    assert frame["protocol_version"] >= 5
    assert ws.receive_json()["type"] == "output"
    return frame["session_id"]


def _send(ws, line: str) -> None:
    ws.send_json({"type": "input", "data": line + "\r"})


def _drain(ws) -> list[dict]:
    """Read one command's frames, returning just the `event` ones.

    A command produces output, then its events, then — only if it produced
    events — one `state` frame. Reading until `state` therefore consumes
    exactly one eventful command's frames; a command with no events is
    detected by its output frame being the last thing available.
    """
    events: list[dict] = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "event":
            events.append(frame)
        elif frame["type"] == "state":
            return events
