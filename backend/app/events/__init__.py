"""Hack Mode event logging — the Phase 2B foundation.

Position in the pipeline this module was scaffolded for, now filled in:

    Student Action -> Frontend -> FastAPI -> EVENT LOGGING -> SQLite
                                                                 |
                                                                 v
                                            (Phase 2E) Metric Computation
                                                      -> Performance Record

WHAT PHASE 2B ESTABLISHES
-------------------------
* `clock.py`    one server-side, timezone-aware UTC clock. Client timestamps
                are never accepted anywhere in this pipeline.
* `records.py`  the three row shapes — session, command, domain event — and
                why commands and events are recorded separately.
* `store.py`    durable append-only SQLite storage, and chronological reads.
* `recorder.py` one recorder per `HackSession`: assigns the per-session
                sequence, stamps the time, keeps the live copy, writes
                through to the store.

WHERE EVENTS COME FROM. `app/commands/router.py` — the backend's own command
handling layer — records each dispatched command and whatever
`ScenarioEvent`s the scenario engine emitted for it. Nothing reads xterm.js
output, and no event is inferred from terminal text: a transition is recorded
because `app/scenarios/environmental.py` decided one occurred, which is why a
failed spoof records `spoof_attempted` + `spoof_rejected` and can never
record `spoof_succeeded`.

WHAT PHASE 2B DELIBERATELY DOES NOT DO. It does not score anything. ACR, RE,
EAC and TTE (Hack Mode) and TTR, AID and DEI (Build Mode) are Phase 2E's, and
there is no arithmetic over these rows anywhere in this package. It also does
not touch the scenario engine, the simulated tools, the serial transport, or
Build Mode's own `BuildEvent` log (`app/build/events.py`), which keeps its
existing in-session behaviour unchanged.
"""

from __future__ import annotations

from app.events.clock import from_iso, to_iso, utc_now
from app.events.recorder import HackEventRecorder
from app.events.records import (
    HackCommandRecord,
    HackEventRecord,
    HackSessionRecord,
    ParticipantRecord,
)
from app.events.store import (
    MEMORY_PATH,
    SqliteEventStore,
    StoreError,
    get_default_store,
    set_default_store,
)

__all__ = [
    "HackCommandRecord",
    "HackEventRecord",
    "HackEventRecorder",
    "HackSessionRecord",
    "MEMORY_PATH",
    "ParticipantRecord",
    "SqliteEventStore",
    "StoreError",
    "from_iso",
    "get_default_store",
    "set_default_store",
    "to_iso",
    "utc_now",
]
