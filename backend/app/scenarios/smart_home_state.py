"""Per-session state for the Smart Home MQTT Control target (Panel 1, 2D.5).

Plain dataclasses, no behaviour — the same discipline `state.py` applies to
the Environmental Monitoring target. `SmartHomeMQTTScenario` owns one instance
and is the only thing that mutates it; everything else reads it through the
scenario's `snapshot()`.

DELIBERATELY SEPARATE FROM `state.py`. Panel 1 is a motor controller, not an
environmental sensor, and it must not inherit the Environmental target's
facts (a BME280 telemetry topic, temperature/humidity/pressure readings, an
"unauthenticated topic" vulnerability). Its state therefore lives in its own
module and imports nothing from `state.py`, so no Environmental value can leak
in — a property `tests/test_smart_home_scenario.py` asserts statically.

Every default is deterministic: a fresh session always starts motor-stopped
with nothing discovered, which is what makes the scenario reproducible in
tests without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class SmartHomeStage(IntEnum):
    """Coarse progress marker, derived from the discovery/attack flags.

    A *view* of the state, not a gate: commands are gated by the specific
    flags they need, never by this number. It exists so a future tutorial
    mode and the evaluation pipeline can name "where the student is" without
    re-deriving it from the booleans.
    """

    INITIAL = 0
    FIRMWARE_EXTRACTED = 1
    FIRMWARE_ANALYZED = 2
    MQTT_OBSERVED = 4
    TARGET_IMPACTED = 6
    ATTACK_SUCCESSFUL = 7


@dataclass
class MotorControlTarget:
    """The controller's network identity and the topics it trusts.

    These are the facts the student must *discover* (by analysing the
    firmware) and then *use* (as command arguments). They are owned here, not
    hard-coded in any command handler — a handler only compares the student's
    typed arguments against these through the scenario.

    `broker_auth_required` is True and stays True: the broker is
    authenticated. The vulnerability is not an open broker, it is that the
    command carries no per-sender authorization once a client can reach the
    topic. See `SmartHomeMQTTScenario`'s module docstring.
    """

    broker_host: str = "192.168.50.1"
    broker_port: int = 1883
    command_topic: str = "cybertrainer/smart-home/motor/control"
    state_topic: str = "cybertrainer/smart-home/motor/state"
    device_status: str = "online"
    broker_auth_required: bool = True


@dataclass
class MotorState:
    """The physical actuator's logical state.

    `running` is the green/red LED the panel shows; the buzzer chirps on a
    change. This is a LOGICAL model — nothing here drives a real GPIO, motor,
    or serial port. A fresh device is stopped.
    """

    running: bool = False
    last_command: str | None = None


@dataclass
class SmartHomeDiscovery:
    """What the student has learned about the controller so far.

    `service_discovered` (the `nmap` step) is distinct from
    `broker_discovered` (recovered from the firmware): confirming the service
    is reachable is a different fact from learning the broker exists.
    """

    firmware_extracted: bool = False
    firmware_analyzed: bool = False
    broker_discovered: bool = False
    topic_discovered: bool = False
    service_discovered: bool = False
    mqtt_observed: bool = False


@dataclass
class SmartHomeAttack:
    """The state of the forged-command attack against the controller."""

    spoof_attempted: bool = False
    spoof_successful: bool = False
    spoof_active: bool = False
    forged_command: str | None = None


@dataclass
class SmartHomeCompletion:
    """The single learning-objective outcome for the scenario."""

    attack_successful: bool = False


@dataclass
class SmartHomeState:
    """All per-session state for one Smart Home MQTT Control run."""

    target: MotorControlTarget = field(default_factory=MotorControlTarget)
    motor: MotorState = field(default_factory=MotorState)
    discovery: SmartHomeDiscovery = field(default_factory=SmartHomeDiscovery)
    attack: SmartHomeAttack = field(default_factory=SmartHomeAttack)
    completion: SmartHomeCompletion = field(default_factory=SmartHomeCompletion)

    @property
    def stage(self) -> SmartHomeStage:
        """The furthest stage the student has reached, as a coarse marker."""
        if self.completion.attack_successful:
            return SmartHomeStage.ATTACK_SUCCESSFUL
        if self.attack.spoof_successful:
            return SmartHomeStage.TARGET_IMPACTED
        if self.discovery.mqtt_observed:
            return SmartHomeStage.MQTT_OBSERVED
        if self.discovery.firmware_analyzed:
            return SmartHomeStage.FIRMWARE_ANALYZED
        if self.discovery.firmware_extracted:
            return SmartHomeStage.FIRMWARE_EXTRACTED
        return SmartHomeStage.INITIAL
