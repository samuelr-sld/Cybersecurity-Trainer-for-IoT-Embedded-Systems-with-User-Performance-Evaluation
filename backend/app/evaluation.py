"""Evaluation — one participant's recorded sessions and their metrics.

    participants / hack_* / build_* rows (SQLite, app/events/store.py)
        -> THIS MODULE (read + call app/metrics) -> /api/evaluation -> Dashboard

COMPOSITION ONLY. Every number in the report comes from one of the seven
existing Phase 2E metric functions (`app/metrics/`), called on rows the
Phase 2B/2E.3 recorders already persisted. This module adds no formula, no
weighting, no overall score or grade: it selects a participant's sessions,
hands each metric function exactly the inputs its docstring asks for, and
serialises the `MetricValue` it returns — status, value and detail — so an
absent value stays visibly absent rather than becoming a 0.

Which metric needs what:

    ACR  package.evaluation.objectives  + hack_events   (per session)
    RE   package.workflow[].phase        + hack_commands (per session)
    TTE  hack_sessions.started_at        + hack_events   (per session)
    EAC  every hack_session of (participant, scenario)  + their events
    TTR  build_sessions.started_at       + build_attempts (per session)
    AID  build_sessions start/end        + build_attempts (per session)
    DEI                                    build_attempts (per session)

The `PanelPackage` for ACR/RE is found by the session's recorded
`scenario_id` among the packages the built-in panels reference — the same
trusted, backend-owned manifests the rest of the platform loads, read with
the same passive loader (no device access, nothing executed). A session
whose scenario no package declares (the no-hardware development default)
gets ACR/RE NOT_APPLICABLE with that reason, never a guessed package.

READ-ONLY. Nothing here writes a row, opens a device, or starts anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.build.records import BuildAttemptRecord, BuildAttemptType, BuildSessionRecord
from app.events.clock import to_iso, utc_now
from app.events.records import HackSessionRecord, ParticipantRecord
from app.events.store import SqliteEventStore, get_default_store
from app.hardware.panels import PanelRegistry, default_panel_registry
from app.metrics import (
    MetricValue,
    compute_acr,
    compute_aid,
    compute_dei,
    compute_eac,
    compute_re,
    compute_tte,
    compute_ttr,
)
from app.metrics.eac import SUCCESS_EVENT_TYPE
from app.panels.loader import PanelPackageError, PanelPackageLoader, default_panel_package_loader
from app.panels.models import PanelPackage

logger = logging.getLogger(__name__)

#: Display metadata for each metric — names as the metric modules document
#: them (final manuscript, Section 3.10.1) and the unit each `MetricValue`
#: is expressed in. Presentation only; no computation reads this.
METRIC_INFO: dict[str, dict[str, str]] = {
    "ACR": {"name": "Attack Completion Rate", "unit": "%"},
    "RE": {"name": "Reconnaissance Efficiency", "unit": "%"},
    "EAC": {"name": "Exploitation Attempt Count", "unit": "sessions"},
    "TTE": {"name": "Time-to-Exploitation", "unit": "s"},
    "TTR": {"name": "Time-to-Resolution", "unit": "s"},
    "AID": {"name": "Attempt and Iteration Density", "unit": "attempts/min"},
    "DEI": {"name": "Debugging Efficiency Index", "unit": "s"},
}


def metric_payload(code: str, value: MetricValue) -> dict[str, Any]:
    """Serialise one `MetricValue` verbatim — status, value, detail."""
    return {
        "code": code,
        **METRIC_INFO[code],
        "status": value.status.value,
        "value": value.value,
        "detail": value.detail,
    }


def _duration(started: datetime, ended: datetime | None) -> float | None:
    return None if ended is None else (ended - started).total_seconds()


def _open_status(is_live: bool) -> str:
    """A session with no `ended_at`: still connected, or never closed.

    `ended_at` is stamped by the WebSocket teardown; one missing on a
    session this process is not serving means the backend stopped before
    the teardown ran (a crash or restart), so it is reported as interrupted
    rather than claimed to be in progress.
    """
    return "in_progress" if is_live else "interrupted"


@dataclass
class PackageCatalog:
    """Scenario id -> the `PanelPackage` that declares it, loaded on demand."""

    registry: PanelRegistry = field(default_factory=default_panel_registry)
    loader: PanelPackageLoader = field(default_factory=default_panel_package_loader)
    _by_scenario: dict[str, PanelPackage] | None = None

    def _index(self) -> dict[str, PanelPackage]:
        if self._by_scenario is None:
            index: dict[str, PanelPackage] = {}
            for panel in self.registry.panels:
                if panel.package_id is None:
                    continue
                try:
                    package = self.loader.load_for_panel(panel)
                except PanelPackageError:
                    logger.warning("evaluation: package for %s unavailable", panel.panel_id)
                    continue
                index.setdefault(package.scenario_id, package)
            self._by_scenario = index
        return self._by_scenario

    def for_scenario(self, scenario_id: str) -> PanelPackage | None:
        return self._index().get(scenario_id)

    def panel_name(self, panel_id: str | None) -> str | None:
        panel = None if panel_id is None else self.registry.get(panel_id)
        return None if panel is None else panel.display_name


def _no_package(scenario_id: str) -> MetricValue:
    return MetricValue.not_applicable(
        f"no panel package declares scenario {scenario_id!r}, so it has no "
        "declared objectives/workflow phases to measure against"
    )


@dataclass
class EvaluationService:
    """Builds evaluation reports from the store. Every dependency injectable."""

    store: SqliteEventStore | None = None
    catalog: PackageCatalog = field(default_factory=PackageCatalog)
    #: Whether a Hack / Build session id is currently being served by this
    #: process — the only fact not in the database. Defaults to "none are".
    is_hack_live: Callable[[str], bool] = lambda _session_id: False
    is_build_live: Callable[[str], bool] = lambda _session_id: False

    def _store(self) -> SqliteEventStore:
        return self.store if self.store is not None else get_default_store()

    # -- Hack Mode ---------------------------------------------------------

    def _hack_session(self, session: HackSessionRecord, events, commands) -> dict[str, Any]:
        package = self.catalog.for_scenario(session.scenario_id)
        acr = (
            compute_acr(package, session.session_id, events)
            if package is not None
            else _no_package(session.scenario_id)
        )
        re_ = (
            compute_re(package, session.session_id, commands)
            if package is not None
            else _no_package(session.scenario_id)
        )
        succeeded = any(event.event_type == SUCCESS_EVENT_TYPE for event in events)
        if succeeded:
            status = "completed"
        elif session.ended_at is None:
            status = _open_status(self.is_hack_live(session.session_id))
        else:
            status = "incomplete"
        panel_id = None if package is None else package.panel_id
        return {
            "session_id": session.session_id,
            "mode": "hack",
            "scenario_id": session.scenario_id,
            "panel_id": panel_id,
            "panel_name": self.catalog.panel_name(panel_id),
            "started_at": to_iso(session.started_at),
            "ended_at": None if session.ended_at is None else to_iso(session.ended_at),
            "duration_seconds": _duration(session.started_at, session.ended_at),
            "status": status,
            "command_count": len(commands),
            "event_count": len(events),
            "events": sorted({event.event_type for event in events}),
            "metrics": {
                "ACR": metric_payload("ACR", acr),
                "RE": metric_payload("RE", re_),
                "TTE": metric_payload("TTE", compute_tte(session, events)),
            },
        }

    def _hack(self, participant_id: str) -> dict[str, Any]:
        store = self._store()
        sessions = store.sessions_for_participant(participant_id)
        events_by_session = {s.session_id: store.events_for_session(s.session_id) for s in sessions}
        rendered = [
            self._hack_session(
                s, events_by_session[s.session_id], store.commands_for_session(s.session_id)
            )
            for s in sessions
        ]
        activities = []
        for scenario_id in dict.fromkeys(s.scenario_id for s in sessions):
            package = self.catalog.for_scenario(scenario_id)
            panel_id = None if package is None else package.panel_id
            activities.append(
                {
                    "scenario_id": scenario_id,
                    "panel_id": panel_id,
                    "panel_name": self.catalog.panel_name(panel_id),
                    "session_count": sum(1 for s in sessions if s.scenario_id == scenario_id),
                    "metrics": {
                        "EAC": metric_payload(
                            "EAC",
                            compute_eac(
                                sessions,
                                events_by_session,
                                participant_id=participant_id,
                                scenario_id=scenario_id,
                            ),
                        )
                    },
                }
            )
        # Newest first for display; EAC above already used chronological order.
        return {"sessions": list(reversed(rendered)), "activities": activities}

    # -- Build Mode --------------------------------------------------------

    @staticmethod
    def _attempt_counts(attempts: Iterable[BuildAttemptRecord]) -> dict[str, dict[str, int]]:
        counts = {kind.value: {"total": 0, "succeeded": 0} for kind in BuildAttemptType}
        for attempt in attempts:
            bucket = counts[attempt.attempt_type.value]
            bucket["total"] += 1
            bucket["succeeded"] += 1 if attempt.success else 0
        return counts

    def _build_session(self, session: BuildSessionRecord, attempts) -> dict[str, Any]:
        validated = any(
            a.attempt_type is BuildAttemptType.VALIDATION and a.success for a in attempts
        )
        if validated:
            status = "completed"
        elif session.ended_at is None:
            status = _open_status(self.is_build_live(session.session_id))
        else:
            status = "incomplete"
        return {
            "session_id": session.session_id,
            "mode": "build",
            "panel_id": session.panel_id,
            "panel_name": self.catalog.panel_name(session.panel_id),
            "started_at": to_iso(session.started_at),
            "ended_at": None if session.ended_at is None else to_iso(session.ended_at),
            "duration_seconds": _duration(session.started_at, session.ended_at),
            "status": status,
            "attempts": self._attempt_counts(attempts),
            "metrics": {
                "TTR": metric_payload("TTR", compute_ttr(session, attempts)),
                "AID": metric_payload("AID", compute_aid(session, attempts)),
                "DEI": metric_payload("DEI", compute_dei(session, attempts)),
            },
        }

    def _build(self, participant_id: str) -> dict[str, Any]:
        store = self._store()
        sessions = store.build_sessions_for_participant(participant_id)
        rendered = [
            self._build_session(s, store.build_attempts_for_session(s.session_id))
            for s in sessions
        ]
        return {"sessions": list(reversed(rendered))}

    # -- report ------------------------------------------------------------

    def report(self, participant: ParticipantRecord) -> dict[str, Any]:
        """The full evaluation for one registered participant."""
        return {
            "participant": participant.to_payload(),
            "generated_at": to_iso(utc_now()),
            "hack": self._hack(participant.participant_id),
            "build": self._build(participant.participant_id),
        }

    def summary(self, participant: ParticipantRecord) -> dict[str, Any]:
        """A one-row overview for the professor's list — counts, no metrics."""
        store = self._store()
        hack = store.sessions_for_participant(participant.participant_id)
        build = store.build_sessions_for_participant(participant.participant_id)
        stamps = [s.ended_at or s.started_at for s in (*hack, *build)]
        return {
            **participant.to_payload(),
            "hack_session_count": len(hack),
            "build_session_count": len(build),
            "last_activity": to_iso(max(stamps)) if stamps else None,
        }
