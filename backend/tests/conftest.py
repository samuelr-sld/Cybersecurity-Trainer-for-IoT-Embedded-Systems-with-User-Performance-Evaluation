"""Shared test fixtures.

Two autouse isolations live here, for the same reason: a test must never
reach the real classroom database or the real hardware, and both are reached
*indirectly*, by tests that never name them.

EVENT-STORE ISOLATION

Since Phase 2B every command dispatch records to the Hack Mode event log
(`app/events/`), and the log's process-wide default store resolves to a real
SQLite file under `backend/data/` — the classroom's evidence. A test run must
never write there, and tests must not see each other's rows. So every test
gets its own fresh in-memory store, installed before it runs and torn down
after.

Autouse rather than opt-in because the dependency is indirect: a test that
merely opens a WebSocket and types `help` records to the store without ever
naming it. Requiring each such test to remember a fixture would mean the
first one that forgot would silently start writing to the real database.
"""

from __future__ import annotations

import os

# Must precede the first `app.config` import: config loads backend/lab.env.local
# at import time, and the suite must never depend on a developer's real
# credentials. An empty path disables the bootstrap.
os.environ["TRAINER_LAB_ENV_PATH"] = ""

import pytest

from app import config
from app.events import MEMORY_PATH, SqliteEventStore, set_default_store
from app.hardware import device_monitor


@pytest.fixture(autouse=True)
def isolated_event_store():
    """Point the Hack Mode event log at a private in-memory database."""
    store = SqliteEventStore(MEMORY_PATH)
    set_default_store(store)
    try:
        yield store
    finally:
        set_default_store(None)
        store.close()


@pytest.fixture(autouse=True)
def no_leaked_sessions(isolated_event_store):
    """End every session a test left behind.

    A WebSocket disconnect now DETACHES a session (it stays resumable for a
    grace period) instead of discarding it, so sessions outlive the test that
    opened them. The process-wide managers must start each test empty, and
    the recorders are finished while this test's store is still installed
    (this fixture depends on `isolated_event_store`, so it tears down first).
    """
    yield
    from app.build_sessions import build_session_manager
    from app.sessions import session_manager

    for manager in (session_manager, build_session_manager):
        for session_id in list(manager._sessions):
            manager.end(session_id)


@pytest.fixture(autouse=True)
def no_startup_device_detection(monkeypatch: pytest.MonkeyPatch):
    """Stop `TestClient(app)` from detecting real hardware at startup.

    The application lifespan primes the shared device monitor (see
    `app/main.py`), which in a deployment is exactly right and in a test run
    would be a real `arduino-cli board list` — plus an `esptool read_mac`
    that RESETS whatever ESP32 happens to be plugged into the machine
    running the suite — on every single `TestClient` construction.

    Autouse for the same reason the store fixture is: the dependency is
    indirect. Dozens of tests build a TestClient to type `help` into a
    terminal and would silently acquire a subprocess and a board reset.

    Tests that are ABOUT the startup path re-enable it deliberately and
    point the monitor at a double first — see
    `tests/test_monitor_readiness.py`.
    """
    monkeypatch.setattr(config, "HARDWARE_STARTUP_DETECT", False)


@pytest.fixture(autouse=True)
def no_stale_uploader_reaping(monkeypatch: pytest.MonkeyPatch):
    """Stop the flasher's pre-flash port cleanup from killing real processes.

    `ArduinoCliFlasher.run_flash` now calls `reap_stale_uploaders`
    (`app/build/process.py`) before every upload, to clear a serial port a
    leftover esptool from an earlier session may still be holding. In a
    deployment that is exactly right; in a test run it would shell out to
    `taskkill`/`pkill` against whatever esptool is alive on the machine
    running the suite — including a real flash in progress from a live
    backend on the same box. Reached indirectly (every real-flasher test
    drives `run_flash`), so autouse, matching the hardware-isolation fixtures
    here. The unit tests that are ABOUT the reaper drive
    `app.build.process.reap_stale_uploaders` directly with the process killer
    faked, and the one flasher test that asserts the call re-installs its own
    recording double over this no-op.
    """
    monkeypatch.setattr("app.build.flasher.reap_stale_uploaders", lambda: False)


@pytest.fixture(autouse=True)
def reset_shared_device_monitor():
    """Reset the process-wide `device_monitor` before AND after every test.

    THE GAP THIS CLOSES. `no_startup_device_detection` above stops the
    application LIFESPAN from priming this singleton with a real detection,
    but nothing stopped a test from driving one directly — and one already
    does, by design: `app/websocket.py`'s `hardware_status` handler calls
    `device_monitor.refresh()` for REAL on every poll (never the passive
    `snapshot()`), because in production that is exactly what keeps the
    shared cache warm between the frontend's periodic header polls. A test
    that sends that message — `test_polling_hardware_status_records_nothing`
    does, on purpose, to prove polling is otherwise silent — leaves the
    singleton CONNECTED and IDENTIFIED afterward whenever a real ESP32 is
    physically attached to the machine running the suite, with nothing to
    clean it up. The NEXT test to open a session then reads that leaked
    state through `select_session_scenario()` and silently receives
    whichever panel's `Scenario` the real board resolves to, instead of the
    long-standing default it was written against.

    THE BUG THIS PINS. That is precisely what made
    `test_hack_events.py::test_the_full_flow_over_the_websocket_delivers_server_timestamps`
    hang when run after `test_polling_hardware_status_records_nothing` with
    Panel 1's board connected: the target test's session silently became a
    `SmartHomeMQTTScenario` instead of the `EnvironmentalMonitoringScenario`
    its `FULL_FLOW` arguments are written for, one command in that
    now-mismatched sequence produced zero events, and
    `test_hack_events.py::_drain` — which reads frames until a `state`
    frame arrives — blocked forever on a `state` frame a zero-event command
    never sends. Confirmed by direct reproduction: driving one real
    `device_monitor.refresh()` and then calling `select_session_scenario()`
    resolves Panel 1's `SmartHomeMQTTScenario` with no fixture involved.

    Resetting before AND after — not just after — means a test gains no
    benefit from, and leaves no trace for, whatever a real board happens to
    answer: the suite behaves as if no physical hardware exists, regardless
    of what is actually plugged into the machine running it. Cheap and
    synchronous (`DeviceMonitor.reset()` performs no I/O), so this adds no
    measurable time. Tests that deliberately exercise real hardware (see
    `tests/test_hardware_in_the_loop.py`) use their OWN private
    `DeviceMonitor` instances or restore this singleton themselves within
    the test, and are unaffected by the extra reset around them.
    """
    device_monitor.reset()
    yield
    device_monitor.reset()
