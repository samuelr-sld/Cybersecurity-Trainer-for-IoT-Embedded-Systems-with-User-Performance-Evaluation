"""Per-session scenario state for the Environmental Monitoring target.

Plain dataclasses, no behaviour. The scenario engine
(`app/scenarios/environmental.py`) owns an instance of `ScenarioState` and is
the only thing that mutates it; everything else reads it through the engine's
`snapshot()`. Keeping the state passive and separate from the engine is what
lets a future physical-ESP32 engine expose the *same* shape without inheriting
the simulation's logic.

Every default is deterministic. A fresh session is always in exactly the same
starting state, which is what makes the whole scenario reproducible in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ScenarioStage(IntEnum):
    """Coarse progress marker, derived from the discovery/attack flags.

    This is a *view* of the state, not a gate: commands are gated by the
    specific flags they need, never by this number. It exists so a future
    tutorial mode and the evaluation pipeline can talk about "where the
    student is" without re-deriving it from a dozen booleans.
    """

    INITIAL = 0
    FIRMWARE_EXTRACTED = 1
    FIRMWARE_ANALYZED = 2
    MQTT_OBSERVED = 4
    TARGET_IMPACTED = 6
    ATTACK_SUCCESSFUL = 7


@dataclass
class TargetInfo:
    """The simulated device's network identity.

    These are the facts the student must *discover* (via firmware analysis)
    and then *use* (as command arguments). They are owned here, not hard-coded
    in any command handler — a handler only ever compares the student's typed
    arguments against these through the engine.
    """

    ip_address: str = "192.168.10.10"
    mqtt_port: int = 1883
    mqtt_topic: str = "sensors/bme280/telemetry"
    device_status: str = "online"


@dataclass
class EnvironmentReading:
    """The environmental values the target reports and displays.

    `temperature` is the field the vulnerability lets an attacker overwrite;
    `humidity` and `pressure` are here so the scenario can prove they stay
    untouched when only temperature is spoofed.
    """

    temperature: float = 28
    humidity: float = 65
    pressure: float = 1008


@dataclass
class DiscoveryState:
    """What the student has learned about the target so far."""

    firmware_extracted: bool = False
    firmware_analyzed: bool = False
    broker_discovered: bool = False
    topic_discovered: bool = False
    mqtt_observed: bool = False


@dataclass
class AttackState:
    """The state of the data-spoofing attack against the target."""

    spoof_attempted: bool = False
    spoof_successful: bool = False
    spoof_active: bool = False
    spoofed_temperature: float | None = None


@dataclass
class CompletionState:
    """The single learning-objective outcome for the scenario."""

    attack_successful: bool = False


@dataclass
class ScenarioState:
    """All per-session state for one Environmental Monitoring run."""

    target: TargetInfo = field(default_factory=TargetInfo)
    environment: EnvironmentReading = field(default_factory=EnvironmentReading)
    discovery: DiscoveryState = field(default_factory=DiscoveryState)
    attack: AttackState = field(default_factory=AttackState)
    completion: CompletionState = field(default_factory=CompletionState)

    @property
    def stage(self) -> ScenarioStage:
        """The furthest stage the student has reached, as a coarse marker."""
        if self.completion.attack_successful:
            return ScenarioStage.ATTACK_SUCCESSFUL
        if self.attack.spoof_successful:
            return ScenarioStage.TARGET_IMPACTED
        if self.discovery.mqtt_observed:
            return ScenarioStage.MQTT_OBSERVED
        if self.discovery.firmware_analyzed:
            return ScenarioStage.FIRMWARE_ANALYZED
        if self.discovery.firmware_extracted:
            return ScenarioStage.FIRMWARE_EXTRACTED
        return ScenarioStage.INITIAL
