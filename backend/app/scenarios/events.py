"""Structured scenario events — the seam for Phase 2E evaluation.

The point of these is that a later event logger and scoring pass can tell what
a student did WITHOUT scraping terminal text. Each meaningful scenario
transition emits a typed `ScenarioEvent`; the engine keeps them in order, and
the evaluation phase will read that list.

PHASE 2E SCAFFOLDING. Nothing here persists events, times them, or scores
them — there is no database and no evaluation in this phase. `ScenarioEvent`
carries only what the transition means (`type`), a human-readable `message`
for logs, and a small `data` mapping for structured detail. Ordering is the
list order; timestamps and sequence numbers belong to the logger that has not
been built yet, so they are deliberately absent here.

Command-level facts (which command was attempted, and whether it succeeded)
are NOT scenario events: they are available at the router seam as the command
name plus the `CommandResult.exit_code`, so the logger can capture them there
without this module needing to know about `help`, `clear`, or parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ScenarioEventType(str, Enum):
    """The closed set of domain events the scenario can emit."""

    FIRMWARE_EXTRACTED = "firmware_extracted"
    FIRMWARE_ANALYZED = "firmware_analyzed"
    BROKER_DISCOVERED = "broker_discovered"
    TOPIC_DISCOVERED = "topic_discovered"
    MQTT_SERVICE_SCANNED = "mqtt_service_scanned"
    MQTT_OBSERVED = "mqtt_observed"
    SPOOF_ATTEMPTED = "spoof_attempted"
    SPOOF_REJECTED = "spoof_rejected"
    SPOOF_SUCCEEDED = "spoof_succeeded"
    TARGET_IMPACTED = "target_impacted"
    ATTACK_COMPLETED = "attack_completed"


@dataclass(frozen=True)
class ScenarioEvent:
    """One thing that happened in the scenario, recorded for evaluation.

    `data` is stored as a read-only mapping so a recorded event cannot be
    mutated after the fact by whoever reads the log.
    """

    type: ScenarioEventType
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def create(
        cls, type: ScenarioEventType, message: str = "", **data: Any
    ) -> "ScenarioEvent":
        """Build an event with an immutable snapshot of `data`."""
        return cls(type=type, message=message, data=MappingProxyType(dict(data)))
