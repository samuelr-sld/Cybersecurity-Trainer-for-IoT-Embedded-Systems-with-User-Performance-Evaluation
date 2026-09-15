"""Shared test fixtures.

The one thing here is event-store isolation, and it is autouse on purpose.

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
