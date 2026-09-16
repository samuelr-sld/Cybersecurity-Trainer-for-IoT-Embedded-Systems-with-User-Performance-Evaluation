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

import pytest

from app import config
from app.events import MEMORY_PATH, SqliteEventStore, set_default_store


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
