"""Structured Build Mode events — the seam for future evaluation.

Mirrors `app/scenarios/events.py`: the point is that a later event logger and
scoring pass can tell what a student did in Build Mode without scraping
editor state. Each meaningful workspace transition emits a typed
`BuildEvent`; `app/build_sessions.py` keeps a per-session log of them and
`app/build_websocket.py` forwards each one to the client as an `event` frame.

PHASE 3A added the session/workspace lifecycle events: `BUILD_SESSION_STARTED`,
`WORKSPACE_LOADED`, `CODE_EDITED`, `SECURITY_REGION_EDITED`,
`BUILD_SESSION_ENDED`. PHASE 3B adds the three compile events —
`COMPILE_STARTED`, `COMPILE_SUCCEEDED`, `COMPILE_FAILED` — emitted by
`app/build/service.py::compile_workspace` around a real `arduino-cli`
invocation (`app/build/compiler.py`).

PHASE 3C adds the three flash events — `FLASH_STARTED`, `FLASH_SUCCEEDED`,
`FLASH_FAILED` — emitted by `app/build/service.py::flash_workspace` around
real device discovery and a real `arduino-cli upload` (`app/build/
flasher.py`). Device detection deliberately gets no event of its own: it is
a step *inside* one flash attempt, and its result is already carried
truthfully by the closing event's `port` and `category` (a `no_device`
category on `FLASH_FAILED` says exactly what happened), so a fourth event
would add a row to the Activity Log without adding a fact to it.

Every other member below remains reserved vocabulary for
validation/testing in later phases — declaring them now means the wire
protocol and the frontend's event-label lookup do not need to change shape
when those phases fill them in, but nothing in this codebase emits one yet,
and none must until the operation it names is real. In particular, no flash
event asserts anything about whether the uploaded firmware works or is
secure; `FLASH_SUCCEEDED` means the upload process exited 0, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class BuildEventType(str, Enum):
    """The full Build Mode event vocabulary, current and reserved."""

    # -- emitted since Phase 3A ---------------------------------------------
    BUILD_SESSION_STARTED = "build_session_started"
    WORKSPACE_LOADED = "workspace_loaded"
    SECURITY_REGION_EDITED = "security_region_edited"
    CODE_EDITED = "code_edited"
    BUILD_SESSION_ENDED = "build_session_ended"

    # -- emitted since Phase 3B ----------------------------------------------
    COMPILE_STARTED = "compile_started"
    COMPILE_FAILED = "compile_failed"
    COMPILE_SUCCEEDED = "compile_succeeded"

    # -- emitted since Phase 3C ----------------------------------------------
    FLASH_STARTED = "flash_started"
    FLASH_FAILED = "flash_failed"
    FLASH_SUCCEEDED = "flash_succeeded"

    # -- reserved for later phases — do not emit until implemented ---------
    VALIDATION_STARTED = "validation_started"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_SUCCEEDED = "validation_succeeded"
    SECURITY_TEST_STARTED = "security_test_started"
    SECURITY_TEST_FAILED = "security_test_failed"
    SECURITY_TEST_SUCCEEDED = "security_test_succeeded"
    FUNCTIONAL_TEST_STARTED = "functional_test_started"
    FUNCTIONAL_TEST_FAILED = "functional_test_failed"
    FUNCTIONAL_TEST_SUCCEEDED = "functional_test_succeeded"
    BUILD_COMPLETED = "build_completed"


@dataclass(frozen=True)
class BuildEvent:
    """One thing that happened in a Build Mode session, for evaluation.

    `data` is stored as a read-only mapping so a recorded event cannot be
    mutated after the fact by whoever reads the log — the same discipline
    `app/scenarios/events.py` uses for Hack Mode.
    """

    type: BuildEventType
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def create(
        cls, type: BuildEventType, message: str = "", **data: Any
    ) -> "BuildEvent":
        """Build an event with an immutable snapshot of `data`."""
        return cls(type=type, message=message, data=MappingProxyType(dict(data)))
