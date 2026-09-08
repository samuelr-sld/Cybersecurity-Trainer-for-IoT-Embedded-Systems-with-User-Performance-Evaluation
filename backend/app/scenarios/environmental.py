"""The Environmental Monitoring scenario — a fully simulated IoT target.

The conceptual target is an ESP32 with a BME280 sensor (temperature,
humidity, pressure) and an OLED display, reporting over Wi-Fi to an MQTT
broker. The learning objective is a data-integrity attack: the device trusts
whatever arrives on its telemetry topic, so an attacker who publishes a
manipulated reading can make the device report a value that was never
measured.

WHY THIS IS ALL SIMULATED. There is no real ESP32, no serial port, no
`esptool`, no network scan, and no MQTT broker anywhere in this module. Every
method computes a deterministic result from `ScenarioState` in memory. That
is a hard requirement — a student's command must never become a real
operating-system action — and it is also what makes the scenario testable
without hardware. The `Scenario` interface is the seam where a physical
target could later replace this class without changing anything above it.

The educational progression (extract firmware -> analyze -> recon -> observe
-> spoof -> impact) is adapted as inspiration from a well-known hobbyist IoT
hacking walkthrough; none of that project's code or hardware is reused.
"""

from __future__ import annotations

import json
from typing import Any

from app.scenarios.base import Scenario, ScenarioOutcome
from app.scenarios.events import ScenarioEvent, ScenarioEventType
from app.scenarios.payloads import is_reading, parse_env_payload
from app.scenarios.state import ScenarioState

# Length cap on any student-supplied value echoed back into output. The
# command parser already strips control characters, so echoing cannot inject
# ANSI escapes; this only stops a very long token from flooding a line.
_MAX_ECHO = 64

# The environmental field this scenario's vulnerability lets an attacker
# overwrite. Humidity and pressure are deliberately not spoofable here.
_SPOOFABLE_FIELD = "temperature"


def _short(value: str) -> str:
    """Truncate an echoed value so it cannot flood the terminal."""
    return value if len(value) <= _MAX_ECHO else value[:_MAX_ECHO] + "..."


class EnvironmentalMonitoringScenario(Scenario):
    """In-memory simulation of the Environmental Monitoring target."""

    scenario_id = "environmental-monitoring"

    def __init__(self) -> None:
        self._state = ScenarioState()
        self._events: list[ScenarioEvent] = []

    # -- interface: introspection -----------------------------------------

    @property
    def state(self) -> ScenarioState:
        return self._state

    @property
    def events(self) -> tuple[ScenarioEvent, ...]:
        return tuple(self._events)

    # -- event helpers -----------------------------------------------------

    def _emit(
        self,
        bucket: list[ScenarioEvent],
        type: ScenarioEventType,
        message: str = "",
        **data: Any,
    ) -> None:
        """Record one domain event in both the call's bucket and the log."""
        event = ScenarioEvent.create(type, message, **data)
        self._events.append(event)
        bucket.append(event)

    def _recompute_completion(self, bucket: list[ScenarioEvent]) -> None:
        """Re-evaluate the single success condition after any state change.

        Success is the full learning objective, never merely "a command ran":
        the target must have been understood (firmware analyzed, broker and
        topic discovered), its MQTT traffic observed, and a spoof accepted so
        the target's reported state actually changed.
        """
        discovery = self._state.discovery
        attack = self._state.attack
        completion = self._state.completion

        complete = (
            discovery.firmware_analyzed
            and discovery.broker_discovered
            and discovery.topic_discovered
            and discovery.mqtt_observed
            and attack.spoof_successful
        )
        if complete and not completion.attack_successful:
            completion.attack_successful = True
            self._emit(
                bucket,
                ScenarioEventType.ATTACK_COMPLETED,
                "attack objective completed: manipulated data accepted by target",
                spoofed_temperature=attack.spoofed_temperature,
            )

    # -- target helpers ----------------------------------------------------

    def _telemetry(self) -> str:
        """The device's current telemetry payload, as it publishes it."""
        env = self._state.environment
        return json.dumps(
            {
                "temperature": env.temperature,
                "humidity": env.humidity,
                "pressure": env.pressure,
            }
        )

    def _reaches_broker(self, host: str | None, port: int | None) -> bool:
        """True if host/port address the target broker.

        `port is None` means the student did not pass `-p`; the tools default
        to 1883, so an unspecified port reaches the broker. Only a *wrong*
        explicit port fails to connect.
        """
        target = self._state.target
        if host != target.ip_address:
            return False
        return port is None or port == target.mqtt_port

    # -- interface: stage 1, firmware extraction ---------------------------

    def extract_firmware(self) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        discovery = self._state.discovery

        first_time = not discovery.firmware_extracted
        discovery.firmware_extracted = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.FIRMWARE_EXTRACTED,
                "firmware image extracted from target",
            )

        lines = [
            "[firmware-extract] Attaching to target over the simulated debug interface...",
            "[firmware-extract] Reading flash 0x00000000 - 0x00400000 ................ done",
            "[firmware-extract] Firmware image written to firmware.bin (4194304 bytes).",
        ]
        if not first_time:
            lines.append(
                "[firmware-extract] Firmware already extracted; reusing firmware.bin."
            )
        return ScenarioOutcome.ok(*lines, events=tuple(bucket))

    # -- interface: stage 2, firmware analysis -----------------------------

    def analyze_firmware(self) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        discovery = self._state.discovery
        target = self._state.target

        if not discovery.firmware_extracted:
            return ScenarioOutcome.failed(
                "[firmware-analyze] No firmware image found.",
                "[firmware-analyze] Run 'firmware-extract' first to obtain firmware.bin.",
            )

        first_time = not discovery.firmware_analyzed
        discovery.firmware_analyzed = True
        discovery.broker_discovered = True
        discovery.topic_discovered = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.FIRMWARE_ANALYZED,
                "firmware analyzed; configuration recovered",
            )
            self._emit(
                bucket,
                ScenarioEventType.BROKER_DISCOVERED,
                "MQTT broker recovered from firmware",
                ip_address=target.ip_address,
                mqtt_port=target.mqtt_port,
            )
            self._emit(
                bucket,
                ScenarioEventType.TOPIC_DISCOVERED,
                "MQTT topic recovered from firmware",
                mqtt_topic=target.mqtt_topic,
            )
        # Discovery is a completion prerequisite, so re-check the objective:
        # if the student reached this step last (having already observed and
        # spoofed out of order), analysis is what completes the chain.
        self._recompute_completion(bucket)

        lines = [
            "[firmware-analyze] Extracting printable strings from firmware.bin...",
            "[firmware-analyze] Device    : Environmental Monitor (ESP32 + BME280)",
            "[firmware-analyze] Sensors   : temperature, humidity, pressure",
            f"[firmware-analyze] MQTT host : {target.ip_address}",
            f"[firmware-analyze] MQTT port : {target.mqtt_port}",
            f"[firmware-analyze] MQTT topic: {target.mqtt_topic}",
            "[firmware-analyze] Note: incoming values on this topic are applied without validation.",
        ]
        return ScenarioOutcome.ok(*lines, events=tuple(bucket))

    # -- interface: stage 3, network reconnaissance ------------------------

    def scan(self, host: str | None, port: int | None) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        target = self._state.target

        if host is None:
            return ScenarioOutcome.usage(
                "nmap: no target specified.",
                "Usage: nmap [-p <port>] <host>",
            )

        echoed = _short(host)
        if host != target.ip_address or target.device_status != "online":
            return ScenarioOutcome.failed(
                f"Starting Nmap scan against {echoed}",
                f"Nmap scan report for {echoed}",
                "Host seems to be down or filtered.",
                "Nmap done: 1 IP address (0 hosts up) scanned",
            )

        if port is not None and port != target.mqtt_port:
            return ScenarioOutcome.ok(
                f"Starting Nmap scan against {echoed}",
                f"Nmap scan report for {echoed}",
                "Host is up (0.011s latency).",
                "PORT      STATE   SERVICE",
                f"{port}/tcp closed  unknown",
                "Nmap done: 1 IP address (1 host up) scanned",
            )

        self._emit(
            bucket,
            ScenarioEventType.MQTT_SERVICE_SCANNED,
            "MQTT service confirmed reachable by scan",
            ip_address=target.ip_address,
            mqtt_port=target.mqtt_port,
        )
        return ScenarioOutcome.ok(
            f"Starting Nmap scan against {echoed}",
            f"Nmap scan report for {echoed}",
            "Host is up (0.011s latency).",
            "PORT      STATE  SERVICE",
            f"{target.mqtt_port}/tcp open   mqtt",
            "Service detected: Mosquitto MQTT broker (no authentication)",
            "Nmap done: 1 IP address (1 host up) scanned",
            events=tuple(bucket),
        )

    # -- interface: stage 4, MQTT observation ------------------------------

    def observe(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        target = self._state.target

        if host is None:
            return ScenarioOutcome.usage(
                "mosquitto_sub: a broker host is required.",
                "Usage: mosquitto_sub -h <host> [-p <port>] -t <topic>",
            )
        if topic is None:
            return ScenarioOutcome.usage(
                "mosquitto_sub: a topic is required (-t <topic>)."
            )

        if not self._reaches_broker(host, port):
            shown_port = port if port is not None else target.mqtt_port
            return ScenarioOutcome.failed(
                f"Error: Unable to connect to {_short(host)}:{shown_port} (connection refused)."
            )

        connected = f"Client connected to {target.ip_address}:{target.mqtt_port}."
        if topic != target.mqtt_topic:
            return ScenarioOutcome.failed(
                connected,
                f"Subscribed to '{_short(topic)}'.",
                "Waiting for messages... (no publisher on this topic)",
            )

        first_time = not self._state.discovery.mqtt_observed
        self._state.discovery.mqtt_observed = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.MQTT_OBSERVED,
                "legitimate MQTT telemetry observed",
                mqtt_topic=target.mqtt_topic,
            )
        self._recompute_completion(bucket)

        return ScenarioOutcome.ok(
            connected,
            f"Subscribed to '{target.mqtt_topic}'.",
            f"{target.mqtt_topic} {self._telemetry()}",
            "(the device republishes telemetry periodically)",
            events=tuple(bucket),
        )

    # -- interface: stages 5-6, spoofing and impact ------------------------

    def publish(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        target = self._state.target
        attack = self._state.attack

        if host is None:
            return ScenarioOutcome.usage(
                "mosquitto_pub: a broker host is required.",
                "Usage: mosquitto_pub -h <host> [-p <port>] -t <topic> -m <payload>",
            )
        if topic is None:
            return ScenarioOutcome.usage(
                "mosquitto_pub: a topic is required (-t <topic>)."
            )
        if message is None:
            return ScenarioOutcome.usage(
                "mosquitto_pub: a message payload is required (-m <payload>)."
            )

        # Any publish carrying a payload is an injection attempt, regardless
        # of whether it will connect or take effect.
        if not attack.spoof_attempted:
            attack.spoof_attempted = True
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_ATTEMPTED,
                "student attempted to publish a manipulated payload",
            )

        if not self._reaches_broker(host, port):
            shown_port = port if port is not None else target.mqtt_port
            return ScenarioOutcome.failed(
                f"Error: Unable to connect to {_short(host)}:{shown_port} (connection refused).",
                events=tuple(bucket),
            )

        published = f"Client published 1 message to '{_short(topic)}' on {target.ip_address}:{target.mqtt_port}."

        # Correct broker, wrong topic: the broker accepts the publish, but the
        # device is not subscribed there, so nothing changes.
        if topic != target.mqtt_topic:
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "publish sent to a topic the device does not consume",
                topic=_short(topic),
            )
            return ScenarioOutcome.failed(
                published,
                "The target device is not subscribed to this topic; it has no effect.",
                events=tuple(bucket),
            )

        # Correct topic, but the payload must actually carry a numeric reading
        # for the device's (unvalidated) parser to apply it.
        payload = parse_env_payload(message)
        if payload is None or not is_reading(payload.get(_SPOOFABLE_FIELD)):
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "payload did not carry a usable temperature reading",
            )
            return ScenarioOutcome.failed(
                published,
                "The device received a payload with no usable temperature field and ignored it.",
                events=tuple(bucket),
            )

        # The vulnerability: the device trusts the incoming reading.
        spoofed = payload[_SPOOFABLE_FIELD]
        self._state.environment.temperature = spoofed
        attack.spoof_successful = True
        attack.spoof_active = True
        attack.spoofed_temperature = spoofed
        self._emit(
            bucket,
            ScenarioEventType.SPOOF_SUCCEEDED,
            "target accepted the manipulated temperature",
            spoofed_temperature=spoofed,
        )
        self._emit(
            bucket,
            ScenarioEventType.TARGET_IMPACTED,
            "target reported state changed",
            temperature=spoofed,
        )
        self._recompute_completion(bucket)

        return ScenarioOutcome.ok(
            published,
            f"Target accepted the manipulated data: reported temperature is now {spoofed}°C.",
            "Humidity and pressure are unchanged.",
            events=tuple(bucket),
        )

    # -- interface: MQTT explorer (visual observation) ---------------------

    def explore(self, host: str | None, port: int | None) -> ScenarioOutcome:
        bucket: list[ScenarioEvent] = []
        target = self._state.target
        discovery = self._state.discovery

        if host is None:
            return ScenarioOutcome.usage(
                "mqtt-explorer: a broker host is required.",
                "Usage: mqtt-explorer -h <host> [-p <port>]",
            )

        if not self._reaches_broker(host, port):
            shown_port = port if port is not None else target.mqtt_port
            return ScenarioOutcome.failed(
                f"mqtt-explorer: unable to connect to {_short(host)}:{shown_port}."
            )

        header = [
            f"mqtt-explorer: connected to {target.ip_address}:{target.mqtt_port}.",
        ]

        # The topic is only visible here once it has been recovered from the
        # firmware. The broker does not advertise a directory of topics, so
        # the explorer cannot hand the student the topic before analysis.
        if not discovery.topic_discovered:
            return ScenarioOutcome.ok(
                *header,
                "No active topics are advertised by the broker.",
                "Recover the device's topic from its firmware, then observe it.",
            )

        first_time = not discovery.mqtt_observed
        discovery.mqtt_observed = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.MQTT_OBSERVED,
                "telemetry observed via mqtt-explorer",
                mqtt_topic=target.mqtt_topic,
            )
        self._recompute_completion(bucket)

        return ScenarioOutcome.ok(
            *header,
            "Topic tree:",
            f"  {target.mqtt_topic}",
            f"    last payload: {self._telemetry()}",
            events=tuple(bucket),
        )

    # -- interface: state serialisation for Phase 2D -----------------------

    def snapshot(self) -> dict[str, Any]:
        state = self._state
        return {
            "scenario_id": self.scenario_id,
            "stage": int(state.stage),
            "stage_name": state.stage.name,
            "target": {
                "ip_address": state.target.ip_address,
                "mqtt_port": state.target.mqtt_port,
                "mqtt_topic": state.target.mqtt_topic,
                "device_status": state.target.device_status,
            },
            "environment": {
                "temperature": state.environment.temperature,
                "humidity": state.environment.humidity,
                "pressure": state.environment.pressure,
            },
            "discovery": {
                "firmware_extracted": state.discovery.firmware_extracted,
                "firmware_analyzed": state.discovery.firmware_analyzed,
                "broker_discovered": state.discovery.broker_discovered,
                "topic_discovered": state.discovery.topic_discovered,
                "mqtt_observed": state.discovery.mqtt_observed,
            },
            "attack": {
                "spoof_attempted": state.attack.spoof_attempted,
                "spoof_successful": state.attack.spoof_successful,
                "spoof_active": state.attack.spoof_active,
                "spoofed_temperature": state.attack.spoofed_temperature,
            },
            "completion": {
                "attack_successful": state.completion.attack_successful,
            },
        }
