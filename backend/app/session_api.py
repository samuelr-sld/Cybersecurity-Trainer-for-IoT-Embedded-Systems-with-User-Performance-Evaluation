"""HTTP endpoints for the lifecycle of a running Hack/Build session.

    GET  /api/sessions/{mode}/{session_id}        is it still running?
    POST /api/sessions/{mode}/{session_id}/end    end it for good

A WebSocket closing never ends a session (`app/session_residency.py`): a
reload has to be able to come back to its work. So ending one on purpose —
leaving the mode, going back to the menu, signing out — is its own explicit
request, and the page asks before resuming so it never re-enters a session
that is gone. Session ids are unguessable uuid4 values, the same capability
the WebSockets already rely on; no other data is returned.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.build_sessions import build_session_manager
from app.sessions import session_manager

router = APIRouter(prefix="/api/sessions")

Mode = Literal["hack", "build"]


def _manager(mode: Mode):
    return session_manager if mode == "hack" else build_session_manager


@router.get("/{mode}/{session_id}")
def session_status(mode: Mode, session_id: str) -> dict:
    return {"live": _manager(mode).is_live(session_id)}


@router.post("/{mode}/{session_id}/end")
def end_session(mode: Mode, session_id: str) -> dict:
    return {"ended": _manager(mode).end(session_id) is not None}
