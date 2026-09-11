"""Hack Mode session lifecycle."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config
from app.hack_hardware import HackHardwareStatus
from app.scenarios import Scenario, create_default_scenario


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HackSession:
    """State for a single connected Hack Mode terminal."""

    session_id: str
    created_at: datetime = field(default_factory=_utc_now)
    cols: int = config.DEFAULT_TERMINAL_COLS
    rows: int = config.DEFAULT_TERMINAL_ROWS
    scenario: Scenario = field(default_factory=create_default_scenario)
    hardware_status: HackHardwareStatus = HackHardwareStatus.NOT_CHECKED
    hardware_port: str | None = None

    @property
    def hardware_ready(self) -> bool:
        return self.hardware_status is HackHardwareStatus.CONNECTED

    def set_hardware(self, status: HackHardwareStatus, port: str | None = None) -> None:
        self.hardware_status = status
        self.hardware_port = port if status is HackHardwareStatus.CONNECTED else None

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, HackSession] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> HackSession:
        session = HackSession(session_id=str(uuid.uuid4()))
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> HackSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove(self, session_id: str) -> HackSession | None:
        async with self._lock:
            return self._sessions.pop(session_id, None)

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)


session_manager = SessionManager()
