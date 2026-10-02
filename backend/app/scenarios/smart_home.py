"""The Smart Home MQTT Control scenario — Panel 1's dedicated target (2D.5).

The conceptual target is the Smart Home MQTT Control System: an ESP32 that
drives a small DC motor through an L293D driver, with local START/STOP
buttons, a green "running" LED, a red "stopped" LED, and a buzzer that chirps
on a state change. It is remote-controlled over MQTT on a command topic
(`cybertrainer/smart-home/motor/control`) and republishes its state on a
state topic (`cybertrainer/smart-home/motor/state`).

THE VULNERABILITY IS AUTHORIZATION, NOT AUTHENTICATION. This is the whole
reason Panel 1 is not the Environmental Monitoring target and must not
inherit its assumptions. The MQTT *broker* requires authentication — this
scenario never pretends otherwise. What is missing is per-command
authorization: the firmware's MQTT callback applies any `START`/`STOP` it
receives on the command topic with no token, signature, or sender check. So a
client that can reach the topic — using broker credentials that the firmware
itself carries, recoverable by the very analysis this activity teaches — can
forge a control command the device obeys. The lesson is precise:

    discovery of an AUTHENTICATED MQTT service
        does not establish AUTHORIZATION to issue its commands.

SIMULATION IS THE DEFAULT; A REAL BROKER IS AN OPTIONAL INJECTION. Constructed
with no argument (as the scenario registry does, and as every test does), this
class is a pure in-memory simulation: no real ESP32, serial port, `esptool`,
network scan, MQTT client, or broker is touched, every method computes a
deterministic result from `SmartHomeState`, and the whole scenario is testable
without hardware. That path is unchanged.

When a `LiveMqttLink` is injected (`use_live_mqtt`, wired at connect by
`app/hack_live_mqtt.py` only when Panel 1 is attached and the lab credentials
are provisioned), `publish` and `observe` instead act over a REAL authenticated
connection to the training broker, and physical impact is decided by the
device's OWN state publication — never by the fact that a publish succeeded.
The firmware-analysis (`extract_firmware`/`analyze_firmware`) and `scan` steps
remain simulated in both modes: they teach offline reverse-engineering and
recon, not live actuation.

Either way this class fits the *existing* `Scenario` interface unchanged: the
generic Hack Engine (command registry, parser, router, session, events)
dispatches the same six operations to it that it dispatches to every scenario,
and knows nothing about motors, START/STOP, Panel 1, or MQTT.

THE VULNERABILITY IS NEVER "FIXED" BY THE LIVE PATH. The real attack connects
as an authenticated-but-unauthorized lab client and publishes a bare
START/STOP; a vulnerable device obeys it, and that is the whole demonstration.
The broker stays authenticated; what is absent is per-command authorization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.scenarios.base import Scenario, ScenarioOutcome, readout_row
from app.scenarios.events import ScenarioEvent, ScenarioEventType
from app.scenarios.smart_home_state import SmartHomeState

if TYPE_CHECKING:  # pragma: no cover - type-only; no runtime MQTT dependency
    from app.scenarios.smart_home_live import LiveMqttLink

# Length cap on any student-supplied value echoed back into output. The
# command parser already strips control characters, so echoing cannot inject
# ANSI escapes; this only stops a very long token from flooding a line.
_MAX_ECHO = 64

# The two control commands the device's MQTT callback recognises. Anything
# else on the command topic is ignored by the firmware, so the scenario
# treats it as a rejected (ineffective) spoof rather than a successful one.
_START = "START"
_STOP = "STOP"
_VALID_COMMANDS = frozenset({_START, _STOP})

# The state the device's own state topic reports once a command has actuated.
# Used only on the live path, to know which state publication CONFIRMS that a
# forged command physically took effect. START/STOP is the scenario's own
# vocabulary; RUNNING/STOPPED is the device's — this is the one place they meet.
_STATE_FOR_COMMAND = {_START: "RUNNING", _STOP: "STOPPED"}


def _short(value: str) -> str:
    """Truncate an echoed value so it cannot flood the terminal."""
    return value if len(value) <= _MAX_ECHO else value[:_MAX_ECHO] + "..."


def _normalize_command(message: str) -> str | None:
    """The control command a payload carries, or None if it is not one.

    The firmware compares case-insensitively and ignores surrounding
    whitespace, so `" start "` actuates exactly as `START` does; any other
    payload reaches the callback and is dropped.
    """
    token = message.strip().upper()
    return token if token in _VALID_COMMANDS else None


class SmartHomeMQTTScenario(Scenario):
    """In-memory simulation of the Smart Home MQTT Control target (Panel 1)."""

    scenario_id = "smart-home-mqtt-control"

    def __init__(self, live: "LiveMqttLink | None" = None) -> None:
        self._state = SmartHomeState()
        self._events: list[ScenarioEvent] = []
        #: When set, `publish`/`observe` act over a real broker instead of the
        #: in-memory simulation. None is the default and the only thing the
        #: scenario registry ever constructs, so every existing caller and
        #: test stays a pure simulation. Injected post-construction via
        #: `use_live_mqtt`, because the registry builds scenarios with no args.
        self._live: "LiveMqttLink | None" = live

    def use_live_mqtt(self, link: "LiveMqttLink") -> None:
        """Go live against a real broker (see `LiveMqttCapable`).

        Called once, at connect, by `app/hack_live_mqtt.py` when the attached
        panel is Panel 1 and the lab credentials are provisioned. Idempotent
        in effect: the last link injected wins, and passing nothing keeps the
        simulation.
        """
        self._live = link

    # -- interface: introspection -----------------------------------------

    @property
    def state(self) -> SmartHomeState:
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
        """Re-evaluate the learning objective after any state change.

        Completion is the full objective, never merely "a command ran": the
        controller must have been understood (firmware analyzed, broker and
        command topic recovered), its control traffic observed, and a forged
        command accepted by the device. This is exactly the conjunction the
        package's three success conditions require between them
        (`config-recovered`, `traffic-observed`, `spoof-demonstrated`), so
        `attack_completed` firing here is what satisfies the last of them.

        Deliberately not gated on the `nmap`/`scan` step: the package makes
        service discovery part of the workflow but names it in no success
        condition, so requiring it here would be stricter than the package
        declares.
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
                "attack objective completed: forged command accepted by the device",
                forged_command=attack.forged_command,
            )

    # -- target helpers ----------------------------------------------------

    def _reaches_broker(self, host: str | None, port: int | None) -> bool:
        """True if host/port address the target broker.

        `port is None` means the student did not pass `-p`; the tools default
        to 1883, so an unspecified port reaches the broker. Only a *wrong*
        explicit port fails to connect.
        """
        target = self._state.target
        if host != target.broker_host:
            return False
        return port is None or port == target.broker_port

    def _motor_report(self) -> str:
        """The controller's current state, as its indicators show it."""
        motor = self._state.motor
        if motor.running:
            return "motor RUNNING (green LED on, red LED off)"
        return "motor STOPPED (red LED on, green LED off)"

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
                "firmware image extracted from the controller",
            )

        # Styled after real `esptool.py read_flash` output — the command
        # handler (`app/commands/handlers/esptool_py.py`) only validates the
        # subcommand; what a read returns is scenario data.
        lines = [
            "esptool.py v4.7.0",
            "Connecting....",
            "Chip is ESP32-D0WDQ6 (revision v1.0)",
            "Uploading stub...",
            "Running stub...",
            "Stub running...",
            "Reading 4194304 bytes at 0x00000000 in flash (4194304 remaining)...",
            "Read 4194304 bytes at 0x00000000 in 41.9 seconds (800.7 kbit/s)...",
            "Hard resetting via RTS pin...",
        ]
        if not first_time:
            lines.append("firmware.bin already exists locally; overwriting.")
        return ScenarioOutcome.ok(*lines, events=tuple(bucket))

    # -- interface: stage 2, firmware analysis -----------------------------

    def analyze_firmware(self, search: str | None = None) -> ScenarioOutcome:
        """Back the `strings` (bare) and `grep <pattern>` commands.

        `search=None` is `strings firmware.bin`: every printable string in
        the image, unfiltered. A `search` term is `grep <pattern>
        firmware.bin`: only matching lines, silent with a non-zero exit if
        nothing matches, exactly like real `grep`. Discovery requires the
        image to have been *read*, not searched a particular way, so both
        complete the same discovery flags on first use.
        """
        bucket: list[ScenarioEvent] = []
        discovery = self._state.discovery
        target = self._state.target

        if not discovery.firmware_extracted:
            return ScenarioOutcome.failed(
                "firmware.bin: No such file or directory",
                "Run 'esptool.py read_flash 0x0 0x400000 firmware.bin' first to obtain it.",
            )

        first_time = not discovery.firmware_analyzed
        discovery.firmware_analyzed = True
        discovery.broker_discovered = True
        discovery.topic_discovered = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.FIRMWARE_ANALYZED,
                "firmware analyzed; controller configuration recovered",
            )
            self._emit(
                bucket,
                ScenarioEventType.BROKER_DISCOVERED,
                "MQTT broker recovered from firmware",
                broker_host=target.broker_host,
                broker_port=target.broker_port,
            )
            self._emit(
                bucket,
                ScenarioEventType.TOPIC_DISCOVERED,
                "MQTT control and state topics recovered from firmware",
                command_topic=target.command_topic,
                state_topic=target.state_topic,
            )
        # Discovery is a completion prerequisite, so re-check the objective:
        # a student who reaches analysis last (having observed and forged out
        # of order) completes the chain here.
        self._recompute_completion(bucket)

        strings_found = self._firmware_strings()

        if search is None:
            return ScenarioOutcome.ok(*strings_found, events=tuple(bucket))

        matches = tuple(s for s in strings_found if search.lower() in s.lower())
        if not matches:
            # Real grep prints nothing and exits 1 when nothing matches.
            return ScenarioOutcome.failed(events=tuple(bucket))
        return ScenarioOutcome.ok(*matches, events=tuple(bucket))

    def _firmware_strings(self) -> tuple[str, ...]:
        """Printable strings a `strings(1)` pass over firmware.bin would show.

        Scenario-owned content for `strings`/`grep` — the generic engine
        never reads this method; only this scenario's `analyze_firmware`
        does. Every value is a Smart Home / motor-control fact computed from
        live target state; nothing here mentions the Environmental Monitoring
        target, so that scenario's assumptions cannot leak in.

        Together these reveal the package's four expected findings: the
        broker endpoint, the command topic, the state topic, and — the point
        of the exercise — that command handling carries no per-sender
        authorization, so broker authentication alone does not protect it.
        """
        target = self._state.target
        return (
            "ESP32-WROOM-32 / Smart Home MQTT Motor Controller firmware v1.0.3",
            "L293D motor driver initialised (START/STOP actuator)",
            "GPIO map: green LED = running, red LED = stopped, buzzer = state-change",
            "WiFi: connecting to configured SSID...",
            "PubSubClient MQTT client v2.8",
            f"MQTT broker: {target.broker_host}:{target.broker_port} (authentication required)",
            f"MQTT command topic: {target.command_topic}",
            f"MQTT state topic: {target.state_topic}",
            "Accepted commands: START, STOP",
            "WARNING: command handler applies START/STOP with no per-sender authorization",
            "NOTE: broker credentials are embedded in this image (the only access control)",
        )

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
        if host != target.broker_host or target.device_status != "online":
            return ScenarioOutcome.failed(
                f"Starting Nmap scan against {echoed}",
                f"Nmap scan report for {echoed}",
                "Host seems to be down or filtered.",
                "Nmap done: 1 IP address (0 hosts up) scanned",
                fields_correct=False,
            )

        if port is not None and port != target.broker_port:
            # Realistic nmap behaviour: scanning a reachable host on the
            # WRONG port is a successful scan that correctly reports the
            # port closed (`success=True` stays unchanged — no existing test
            # asserts otherwise). But the student's `-p` argument was still
            # wrong, which `success`/`exit_code` alone cannot say — this is
            # exactly RE's "correct tool, correct host, incorrect port"
            # example, so `fields_correct` says it explicitly.
            return ScenarioOutcome.ok(
                f"Starting Nmap scan against {echoed}",
                f"Nmap scan report for {echoed}",
                "Host is up (0.011s latency).",
                "PORT      STATE   SERVICE",
                f"{port}/tcp closed  unknown",
                "Nmap done: 1 IP address (1 host up) scanned",
                fields_correct=False,
            )

        first_time = not self._state.discovery.service_discovered
        self._state.discovery.service_discovered = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.SCAN,
                "MQTT service confirmed reachable by scan",
                broker_host=target.broker_host,
                broker_port=target.broker_port,
            )
        return ScenarioOutcome.ok(
            f"Starting Nmap scan against {echoed}",
            f"Nmap scan report for {echoed}",
            "Host is up (0.011s latency).",
            "PORT      STATE  SERVICE",
            f"{target.broker_port}/tcp open   mqtt",
            # Authentication IS present on the broker — the vulnerability is
            # not an open broker, it is the unauthorized command handler.
            "Service detected: Mosquitto MQTT broker (authentication required)",
            "Nmap done: 1 IP address (1 host up) scanned",
            events=tuple(bucket),
            fields_correct=True,
        )

    # -- interface: stage 4, MQTT observation ------------------------------

    def observe(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        """Subscribe to a broker topic — simulated, or over the real broker.

        Argument validation and target-fact checking are identical in both
        modes; only what happens once the arguments are known differs. With no
        live link this is the long-standing in-memory simulation; with one it
        subscribes to the real broker and shows what actually arrives.
        """
        if self._live is not None:
            return self._observe_live(host, port, topic)
        return self._observe_simulated(host, port, topic)

    def _observe_simulated(
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
            shown_port = port if port is not None else target.broker_port
            return ScenarioOutcome.failed(
                f"Error: Unable to connect to {_short(host)}:{shown_port} (connection refused).",
                fields_correct=False,
            )

        connected = f"Client connected to {target.broker_host}:{target.broker_port}."

        # The state topic carries the device's retained status, not the
        # control traffic the activity is about; subscribing there is valid
        # (`success` stays True — no existing test asserts otherwise) but
        # does not count as observing the command protocol, so the `-t`
        # argument was still the wrong required field for this objective —
        # the same "reached the target, wrong required field" shape as
        # nmap's wrong-port case above.
        if topic == target.state_topic:
            return ScenarioOutcome.ok(
                connected,
                f"Subscribed to '{target.state_topic}'.",
                f"{target.state_topic} {_STOP if not self._state.motor.running else _START}",
                "(retained device state; no control traffic here)",
                fields_correct=False,
            )

        if topic != target.command_topic:
            return ScenarioOutcome.failed(
                connected,
                f"Subscribed to '{_short(topic)}'.",
                "Waiting for messages... (no publisher on this topic)",
                fields_correct=False,
            )

        first_time = not self._state.discovery.mqtt_observed
        self._state.discovery.mqtt_observed = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.MQTT_OBSERVED,
                "legitimate MQTT control traffic observed",
                command_topic=target.command_topic,
            )
        self._recompute_completion(bucket)

        return ScenarioOutcome.ok(
            connected,
            f"Subscribed to '{target.command_topic}'.",
            f"{target.command_topic} {_START}",
            f"{target.command_topic} {_STOP}",
            "(the local buttons publish START/STOP here; the device acts on every message)",
            events=tuple(bucket),
            fields_correct=True,
        )

    # -- interface: stages 5-6, spoofing and physical impact ---------------

    def publish(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        """The forged-command attack — simulated, or over the real broker.

        With no live link this computes the outcome from in-memory state
        (unchanged). With one, it publishes the student's payload to the real
        broker as an authenticated-but-unauthorized lab client and confirms
        physical impact from the device's own state topic — claiming success
        only when that evidence arrives.
        """
        if self._live is not None:
            return self._publish_live(host, port, topic, message)
        return self._publish_simulated(host, port, topic, message)

    def _publish_simulated(
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

        # Any publish carrying a payload is a forged-command attempt,
        # regardless of whether it will connect or take effect.
        if not attack.spoof_attempted:
            attack.spoof_attempted = True
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_ATTEMPTED,
                "student attempted to publish a forged control command",
            )

        if not self._reaches_broker(host, port):
            shown_port = port if port is not None else target.broker_port
            return ScenarioOutcome.failed(
                f"Error: Unable to connect to {_short(host)}:{shown_port} (connection refused).",
                events=tuple(bucket),
                fields_correct=False,
            )

        published = (
            f"Client published 1 message to '{_short(topic)}' on "
            f"{target.broker_host}:{target.broker_port}."
        )

        # Correct broker, wrong topic: the broker accepts the publish, but the
        # device is not subscribed there, so nothing actuates.
        if topic != target.command_topic:
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "publish sent to a topic the device does not consume",
                topic=_short(topic),
            )
            return ScenarioOutcome.failed(
                published,
                "The controller is not subscribed to this topic; the command has no effect.",
                events=tuple(bucket),
                fields_correct=False,
            )

        # Correct topic, but the payload must be a command the firmware's
        # callback recognises. An unrecognised payload reaches the device and
        # is dropped — this is the firmware ignoring garbage, not authorizing.
        command = _normalize_command(message)
        if command is None:
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "payload was not a recognised START/STOP command",
                payload=_short(message),
            )
            return ScenarioOutcome.failed(
                published,
                "The controller received an unrecognised command and ignored it "
                "(it accepts only START or STOP).",
                events=tuple(bucket),
                fields_correct=False,
            )

        # The vulnerability: the command topic has no per-sender
        # authorization, so the device obeys the forged command exactly as it
        # obeys its own local buttons.
        self._state.motor.running = command == _START
        self._state.motor.last_command = command
        attack.spoof_successful = True
        attack.spoof_active = True
        attack.forged_command = command
        self._emit(
            bucket,
            ScenarioEventType.SPOOF_SUCCEEDED,
            "device accepted the forged command with no authorization check",
            forged_command=command,
        )
        self._emit(
            bucket,
            ScenarioEventType.TARGET_IMPACTED,
            "controller actuated by the forged command",
            forged_command=command,
            motor_running=self._state.motor.running,
        )
        self._recompute_completion(bucket)

        buzzer = "Buzzer chirps to signal the state change."
        return ScenarioOutcome.ok(
            published,
            f"The controller obeyed the forged '{command}' command: {self._motor_report()}.",
            buzzer,
            "Broker authentication did not prevent this: the command carried no authorization.",
            events=tuple(bucket),
            fields_correct=True,
        )

    # -- live path: real broker + real device evidence ---------------------

    def _observe_live(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        """Subscribe to the REAL broker and report what actually arrives.

        The connection is bounded and self-closing (see `LiveMqttLink`). No
        traffic within the window is reported honestly as "nothing observed",
        never as invented messages, and `mqtt_observed` is emitted only when
        real control traffic was actually seen on the command topic.
        """
        assert self._live is not None
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
            shown_port = port if port is not None else target.broker_port
            return ScenarioOutcome.failed(
                f"[mqtt] target {_short(host)}:{shown_port} does not match the "
                f"discovered broker; aim at {target.broker_host}:{target.broker_port}.",
                fields_correct=False,
            )

        outcome = self._live.observe(topic)
        lines = [f"[mqtt] connecting to {target.broker_host}:{target.broker_port}..."]
        if not outcome.connected:
            lines.append(f"[mqtt] connection failed: {outcome.error}")
            return ScenarioOutcome.failed(*lines, fields_correct=True)

        lines.append("[mqtt] authenticated")
        lines.append(f"[mqtt] subscribed to {_short(topic)}")
        for message in outcome.messages:
            lines.append(f"{message.topic} {message.payload}")

        # The state topic carries retained status, not the control protocol —
        # a valid subscription, but not "observing control traffic".
        if topic == target.state_topic:
            lines.append(
                "(retained device state; no control traffic here)"
                if outcome.messages
                else "[mqtt] the device published no state within the window."
            )
            return ScenarioOutcome.ok(*lines, fields_correct=False)

        if topic != target.command_topic:
            if not outcome.messages:
                lines.append("[mqtt] no messages received on this topic within the window.")
                return ScenarioOutcome.failed(*lines, fields_correct=False)
            return ScenarioOutcome.ok(*lines, fields_correct=False)

        # The command topic. Only REAL observed traffic counts as observed.
        if not outcome.messages:
            lines.append(
                "[mqtt] no control traffic observed within the window "
                "(trigger a command, or wait for the device to publish)."
            )
            return ScenarioOutcome.ok(*lines, fields_correct=True)

        first_time = not self._state.discovery.mqtt_observed
        self._state.discovery.mqtt_observed = True
        if first_time:
            self._emit(
                bucket,
                ScenarioEventType.MQTT_OBSERVED,
                "legitimate MQTT control traffic observed",
                command_topic=target.command_topic,
            )
        self._recompute_completion(bucket)
        return ScenarioOutcome.ok(*lines, events=tuple(bucket), fields_correct=True)

    def _publish_live(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        """Forge a command over the REAL broker; confirm impact from evidence.

        Publishes the student's payload as the injected authenticated lab
        client, then reads the device's own state topic. `spoof_succeeded` /
        `target_impacted` are emitted ONLY when the device reports the state a
        real actuation would produce — a broker-accepted publish alone never
        claims physical impact.
        """
        assert self._live is not None
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

        # Any publish carrying a payload is a forged-command attempt.
        if not attack.spoof_attempted:
            attack.spoof_attempted = True
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_ATTEMPTED,
                "student attempted to publish a forged control command",
            )

        # The student must aim at the discovered broker. The link only ever
        # connects to that one broker; refusing a mismatch keeps the
        # target-fact lesson honest and never has the backend reach out to a
        # host the student merely typed.
        if not self._reaches_broker(host, port):
            shown_port = port if port is not None else target.broker_port
            return ScenarioOutcome.failed(
                f"[mqtt] target {_short(host)}:{shown_port} does not match the "
                f"discovered broker; aim at {target.broker_host}:{target.broker_port}.",
                events=tuple(bucket),
                fields_correct=False,
            )

        command = _normalize_command(message)
        expected_state = _STATE_FOR_COMMAND.get(command) if command else None
        outcome = self._live.publish_and_confirm(topic, message, expected_state)

        lines = [f"[mqtt] connecting to {target.broker_host}:{target.broker_port}..."]
        if not outcome.connected:
            lines.append(f"[mqtt] connection failed: {outcome.error}")
            # Target was correct; the broker was unreachable — not a wrong field.
            return ScenarioOutcome.failed(*lines, events=tuple(bucket), fields_correct=True)

        lines.append("[mqtt] authenticated")
        if not outcome.published:
            lines.append(f"[mqtt] publish failed: {outcome.error}")
            return ScenarioOutcome.failed(*lines, events=tuple(bucket), fields_correct=True)

        lines.append(f"[mqtt] published {_short(message)} to {_short(topic)}")

        # Wrong topic: the broker accepted it, but the device is not subscribed.
        if topic != target.command_topic:
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "publish sent to a topic the device does not consume",
                topic=_short(topic),
            )
            lines.append(
                "[mqtt] the controller is not subscribed to this topic; no effect."
            )
            return ScenarioOutcome.failed(*lines, events=tuple(bucket), fields_correct=False)

        # Right topic, unrecognised payload: the firmware drops it.
        if command is None:
            self._emit(
                bucket,
                ScenarioEventType.SPOOF_REJECTED,
                "payload was not a recognised START/STOP command",
                payload=_short(message),
            )
            lines.append(
                "[mqtt] the controller accepts only START or STOP; the payload was ignored."
            )
            return ScenarioOutcome.failed(*lines, events=tuple(bucket), fields_correct=False)

        # Right topic, valid command. Physical impact requires the device's
        # OWN state topic to confirm the expected state — a broker-accepted
        # publish is NOT that fact.
        observed = outcome.observed_state
        confirmed = (
            observed is not None
            and expected_state is not None
            and observed.strip().upper() == expected_state.upper()
        )
        if not confirmed:
            lines.append(
                f"[mqtt] observed device state: {observed}"
                if observed
                else "[mqtt] no device state observed within the confirmation window"
            )
            lines.append(
                "[attack] publish accepted by the broker, but the device did not confirm "
                "the expected state — physical impact NOT confirmed."
            )
            return ScenarioOutcome.failed(*lines, events=tuple(bucket), fields_correct=True)

        # Confirmed. State reflects the device's own report, not the command.
        self._state.motor.running = observed.strip().upper() == "RUNNING"
        self._state.motor.last_command = command
        attack.spoof_successful = True
        attack.spoof_active = True
        attack.forged_command = command
        self._emit(
            bucket,
            ScenarioEventType.SPOOF_SUCCEEDED,
            "device accepted the forged command with no authorization check",
            forged_command=command,
        )
        self._emit(
            bucket,
            ScenarioEventType.TARGET_IMPACTED,
            "controller actuated by the forged command",
            forged_command=command,
            motor_running=self._state.motor.running,
        )
        self._recompute_completion(bucket)

        lines.append(f"[mqtt] observed device state: {observed}")
        lines.append(f"[attack] physical target impact confirmed: {self._motor_report()}.")
        lines.append(
            "[attack] broker authentication did not prevent this: the command carried no "
            "authorization."
        )
        return ScenarioOutcome.ok(*lines, events=tuple(bucket), fields_correct=True)

    # -- interface: state serialisation for Phase 2D -----------------------

    def snapshot(self) -> dict[str, Any]:
        state = self._state
        return {
            "scenario_id": self.scenario_id,
            "stage": int(state.stage),
            "stage_name": state.stage.name,
            "target": {
                "broker_host": state.target.broker_host,
                "broker_port": state.target.broker_port,
                "command_topic": state.target.command_topic,
                "state_topic": state.target.state_topic,
                "device_status": state.target.device_status,
                "broker_auth_required": state.target.broker_auth_required,
            },
            "motor": {
                "running": state.motor.running,
                "last_command": state.motor.last_command,
            },
            "discovery": {
                "firmware_extracted": state.discovery.firmware_extracted,
                "firmware_analyzed": state.discovery.firmware_analyzed,
                "broker_discovered": state.discovery.broker_discovered,
                "topic_discovered": state.discovery.topic_discovered,
                "service_discovered": state.discovery.service_discovered,
                "mqtt_observed": state.discovery.mqtt_observed,
            },
            "attack": {
                "spoof_attempted": state.attack.spoof_attempted,
                "spoof_successful": state.attack.spoof_successful,
                "spoof_active": state.attack.spoof_active,
                "forged_command": state.attack.forged_command,
            },
            "completion": {
                "attack_successful": state.completion.attack_successful,
            },
            # What the TARGET DEVICE panel shows, gated by discovery here so the
            # page holds no per-device knowledge. Display only: computed from
            # the same state the keys above report, it changes no behaviour.
            "readout": [
                readout_row(
                    "status", "Status", str(state.target.device_status).upper(), revealed=True
                ),
                readout_row(
                    "broker",
                    "Broker",
                    f"{state.target.broker_host}:{state.target.broker_port}",
                    revealed=state.discovery.broker_discovered,
                ),
                readout_row(
                    "command_topic",
                    "Command topic",
                    state.target.command_topic,
                    revealed=state.discovery.topic_discovered,
                ),
                readout_row(
                    "motor",
                    "Motor",
                    ("RUNNING" if state.motor.running else "STOPPED")
                    + (" (SPOOFED)" if state.attack.spoof_active else ""),
                    revealed=state.discovery.mqtt_observed,
                ),
            ],
        }
