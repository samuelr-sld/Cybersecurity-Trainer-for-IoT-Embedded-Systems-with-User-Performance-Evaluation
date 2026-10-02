"""Panel 1's scenario driven over a live MQTT link (against a fake transport).

The simulation path is covered by `test_smart_home_scenario.py` and is left
untouched. This file covers only the LIVE path: a `SmartHomeMQTTScenario` with
a `LiveMqttLink` injected, where publish/observe act over an (injected, fake)
transport and physical impact is decided by the device's own state topic.

The load-bearing claim under test is the honest one: a publish the broker
accepted is NOT physical impact. `spoof_succeeded`/`target_impacted` are
emitted only when the fake device actually reports the expected state.

Requirement map:
    I  scenario uses injected fake transport   test_scenario_goes_live_*
    J  observed RUNNING -> impact event        test_forged_start_confirmed_by_state_*
    K  publish w/o observed state -> no impact  test_publish_without_confirmation_*
    N  no MQTT password in records              test_no_mqtt_password_in_records
    O  other scenarios unaffected               test_environmental_scenario_*
"""

from __future__ import annotations

import asyncio
import time

from app.commands import CommandContext, default_router
from app.mqtt.transport import MqttMessage, MqttSettings, MqttTransportError
from app.scenarios import EnvironmentalMonitoringScenario, SmartHomeMQTTScenario
from app.scenarios.smart_home_live import LiveMqttCapable, LiveMqttLink
from app.sessions import HackSession

BROKER = "192.168.50.1"
PORT = 1883
CONTROL_TOPIC = "cybertrainer/smart-home/motor/control"
STATE_TOPIC = "cybertrainer/smart-home/motor/state"
TEST_AUTH_VALUE = "unit-test-value"


class FakeTransport:
    """A connected MqttTransport with no broker (see test_mqtt_transport)."""

    def __init__(
        self,
        settings: MqttSettings,
        *,
        connect_error: str | None = None,
        publish_error: str | None = None,
        inbox: list[MqttMessage] | None = None,
    ) -> None:
        self.settings = settings
        self._connect_error = connect_error
        self._publish_error = publish_error
        self._inbox = list(inbox or [])
        self.published: list[tuple[str, str]] = []
        self.retained: list[tuple[str, str]] = []
        self.subscribed: list[str] = []
        self.closed = False

    def connect(self) -> None:
        if self._connect_error:
            raise MqttTransportError(self._connect_error)

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._publish_error:
            raise MqttTransportError(self._publish_error)
        self.published.append((topic, payload))
        if retain:
            self.retained.append((topic, payload))

    def next_message(self, timeout: float):
        if self._inbox:
            return self._inbox.pop(0)
        time.sleep(min(timeout, 0.01))
        return None

    def close(self) -> None:
        self.closed = True


def _settings(password: str = TEST_AUTH_VALUE) -> MqttSettings:
    return MqttSettings(
        host=BROKER,
        port=PORT,
        username="panel1-guest",
        password=password,
        client_id="trainer-hack-test",
    )


def _attach(scenario: SmartHomeMQTTScenario, fake: FakeTransport) -> FakeTransport:
    def factory(settings: MqttSettings):
        try:
            fake.connect()
        except MqttTransportError:
            fake.close()
            raise
        return fake

    scenario.use_live_mqtt(
        LiveMqttLink(
            settings=_settings(),
            state_topic=STATE_TOPIC,
            transport_factory=factory,
            observe_timeout_seconds=0.1,
            listen_timeout_seconds=0.1,
        )
    )
    return fake


def _types(outcome) -> list[str]:
    return [event.type.value for event in outcome.events]


# --- I: the scenario goes live ---------------------------------------------


def test_scenario_is_live_capable_and_uses_the_injected_transport() -> None:
    scenario = SmartHomeMQTTScenario()
    assert isinstance(scenario, LiveMqttCapable)
    fake = _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "RUNNING")]))
    scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    # It really published through the injected transport, not the simulation.
    assert fake.published == [(CONTROL_TOPIC, "START")]
    assert fake.closed is True


def test_forged_command_is_published_retained() -> None:
    """The real device never republishes a command on `CONTROL_TOPIC` (only
    its own state, on `STATE_TOPIC`), and a one-shot publish/observe pair
    cannot overlap in time unless something else is publishing concurrently.
    Retaining the forged command is what lets a `mosquitto_sub` run AFTER
    the attack still see it — this is the fix for objective 4 never
    completing against a live broker (see hackmode-panel1-attack-sequence
    memory / the 2026-10 live-MQTT session)."""
    scenario = SmartHomeMQTTScenario()
    fake = _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "RUNNING")]))
    scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert fake.retained == [(CONTROL_TOPIC, "START")]


def test_live_scenario_can_be_constructed_with_the_link() -> None:
    fake = FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "STOPPED")])

    def factory(settings):
        fake.connect()
        return fake

    link = LiveMqttLink(
        settings=_settings(), state_topic=STATE_TOPIC, transport_factory=factory,
        observe_timeout_seconds=0.1, listen_timeout_seconds=0.1,
    )
    scenario = SmartHomeMQTTScenario(live=link)
    scenario.publish(BROKER, None, CONTROL_TOPIC, "STOP")
    assert fake.published == [(CONTROL_TOPIC, "STOP")]


# --- J: observed state confirms physical impact ----------------------------


def test_forged_start_confirmed_by_state_emits_impact() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "RUNNING")]))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert outcome.success is True
    assert "spoof_succeeded" in _types(outcome)
    assert "target_impacted" in _types(outcome)
    assert scenario.state.motor.running is True
    assert scenario.state.attack.spoof_successful is True
    text = "\n".join(outcome.lines).lower()
    assert "physical target impact confirmed" in text
    assert "running" in text


def test_forged_stop_confirmed_by_state_stops_the_motor() -> None:
    scenario = SmartHomeMQTTScenario()
    # Motor starts running, then a forged STOP confirmed as STOPPED.
    _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "STOPPED")]))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "stop")  # case-insensitive
    assert outcome.success is True
    assert scenario.state.motor.running is False
    assert scenario.state.attack.forged_command == "STOP"


# --- K: a publish the broker accepted is NOT physical impact ----------------


def test_publish_without_confirmation_does_not_claim_impact() -> None:
    scenario = SmartHomeMQTTScenario()
    # Broker accepts the publish, but the device reports NOTHING back.
    fake = _attach(scenario, FakeTransport(_settings(), inbox=[]))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert fake.published == [(CONTROL_TOPIC, "START")]  # it really published
    assert outcome.success is False
    assert "spoof_succeeded" not in _types(outcome)
    assert "target_impacted" not in _types(outcome)
    assert scenario.state.motor.running is False
    assert scenario.state.attack.spoof_successful is False
    assert "not confirmed" in "\n".join(outcome.lines).lower()


def test_publish_with_wrong_state_reported_does_not_claim_impact() -> None:
    scenario = SmartHomeMQTTScenario()
    # A remediated device might report it stayed STOPPED after a forged START.
    _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "STOPPED")]))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert outcome.success is False
    assert "spoof_succeeded" not in _types(outcome)
    assert scenario.state.motor.running is False


# --- live edges: connection failure, wrong topic, bad payload, target -------


def test_connection_failure_is_truthful_and_claims_no_impact() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), connect_error="connection refused"))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert outcome.success is False
    assert "spoof_attempted" in _types(outcome)  # an attempt was made
    assert "spoof_succeeded" not in _types(outcome)
    assert "connection failed" in "\n".join(outcome.lines).lower()


def test_publish_to_wrong_topic_is_rejected_live() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[]))
    outcome = scenario.publish(BROKER, None, "cybertrainer/smart-home/motor/other", "START")
    assert outcome.success is False
    assert "spoof_rejected" in _types(outcome)
    assert "spoof_succeeded" not in _types(outcome)


def test_unrecognised_payload_is_rejected_live() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[]))
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "OPEN_GARAGE")
    assert outcome.success is False
    assert "spoof_rejected" in _types(outcome)
    assert scenario.state.motor.running is False


def test_wrong_broker_target_is_refused_before_any_connection() -> None:
    scenario = SmartHomeMQTTScenario()
    fake = _attach(scenario, FakeTransport(_settings(), inbox=[]))
    outcome = scenario.publish("10.0.0.1", None, CONTROL_TOPIC, "START")
    assert outcome.success is False
    assert outcome.fields_correct is False
    assert fake.published == []  # never reached out to a wrong host


# --- live observe -----------------------------------------------------------


def test_observe_command_topic_with_traffic_records_observation() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(CONTROL_TOPIC, "START")]))
    outcome = scenario.observe(BROKER, None, CONTROL_TOPIC)
    assert outcome.success is True
    assert "mqtt_observed" in _types(outcome)
    assert scenario.state.discovery.mqtt_observed is True
    assert any("START" in line for line in outcome.lines)


def test_observe_command_topic_without_traffic_is_honest() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[]))
    outcome = scenario.observe(BROKER, None, CONTROL_TOPIC)
    # No traffic seen -> not observed, and no invented messages.
    assert "mqtt_observed" not in _types(outcome)
    assert scenario.state.discovery.mqtt_observed is False
    assert "no control traffic" in "\n".join(outcome.lines).lower()


def test_forge_then_observe_completes_the_attack_live() -> None:
    """The regression this retain fix closes: against a real broker, the
    device itself never republishes a command (only its own state), and a
    student's single sequential terminal cannot run `mosquitto_sub` and
    `mosquitto_pub` at the same instant — so before this fix, `mqtt_observed`
    (and therefore `attack_completed`, which requires it) could never fire
    through any realistic, single-session student action. The forged publish
    is retained, so a `mosquitto_sub` run AFTER the attack still receives it,
    exactly as a real broker would redeliver a retained message to a new
    subscriber. The inbox below models that redelivery: the device's state
    confirmation is consumed during `publish`, then the retained command
    message is what the broker hands back on the very next connection.
    """
    scenario = SmartHomeMQTTScenario()
    fake = _attach(
        scenario,
        FakeTransport(
            _settings(),
            inbox=[MqttMessage(STATE_TOPIC, "RUNNING"), MqttMessage(CONTROL_TOPIC, "START")],
        ),
    )
    # The rest of the discovery chain (pure in-memory, no transport) — a
    # realistic student has already done this before ever touching MQTT.
    scenario.extract_firmware()
    scenario.analyze_firmware(None)

    publish_outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    assert "spoof_succeeded" in _types(publish_outcome)
    assert "attack_completed" not in _types(publish_outcome)  # not observed yet
    assert fake.retained == [(CONTROL_TOPIC, "START")]

    observe_outcome = scenario.observe(BROKER, None, CONTROL_TOPIC)
    assert "mqtt_observed" in _types(observe_outcome)
    assert "attack_completed" in _types(observe_outcome)
    assert scenario.state.completion.attack_successful is True


# --- N: the provisioned password never reaches the activity log -------------


def _dispatch(session: HackSession, line: str):
    return asyncio.run(default_router.dispatch(line, CommandContext(session=session)))


def test_no_mqtt_password_in_records() -> None:
    scenario = SmartHomeMQTTScenario()
    _attach(scenario, FakeTransport(_settings(), inbox=[MqttMessage(STATE_TOPIC, "RUNNING")]))
    session = HackSession(session_id="live-panel-one", scenario=scenario)
    session.recorder.start()

    result = _dispatch(
        session, f"mosquitto_pub -h {BROKER} -t {CONTROL_TOPIC} -m START"
    )

    # The command succeeded over the (fake) live path...
    assert "spoof_succeeded" in [e.type.value for e in result.events]
    # ...and the provisioned password appears NOWHERE: not in the recorded
    # command argv, not in any event record, not in the terminal output.
    haystack = "\n".join(
        [repr(c) for c in session.recorder.commands]
        + [repr(e) for e in session.recorder.events]
        + list(result.lines)
    )
    assert TEST_AUTH_VALUE not in haystack


# --- O: other scenarios are unaffected --------------------------------------


def test_environmental_scenario_is_not_live_capable() -> None:
    env = EnvironmentalMonitoringScenario()
    assert not isinstance(env, LiveMqttCapable)
    assert not hasattr(env, "use_live_mqtt")


def test_smart_home_without_a_link_is_pure_simulation() -> None:
    # The default construction (what the registry builds) never goes live.
    scenario = SmartHomeMQTTScenario()
    outcome = scenario.publish(BROKER, None, CONTROL_TOPIC, "START")
    # Simulation obeys immediately, with no transport involved.
    assert outcome.success is True
    assert scenario.state.motor.running is True
    assert "spoof_succeeded" in _types(outcome)
