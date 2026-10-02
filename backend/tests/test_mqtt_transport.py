"""The generic MQTT transport seam and the scenario-side live link.

These cover the transport plumbing WITHOUT a broker: `PahoMqttTransport` is
driven against a fake paho client (so connect/subscribe/publish/receive/close
are exercised as real code paths, but nothing touches the network), and
`LiveMqttLink`'s bounded, self-cleaning lifecycle is driven against a fake
transport.

Requirement map (see the phase brief):
    A connect success            test_connect_success_with_fake_client
    B connect failure truthful   test_connect_failure_is_a_truthful_error
    C publish success            test_publish_success
    D publish failure truthful   test_publish_failure_is_a_truthful_error
    E subscribe receives message test_subscribe_delivers_received_message
    F subscribe timeout bounded  test_next_message_times_out_and_is_truthful
    G cleanup after publish      test_link_closes_transport_after_publish
    H cleanup after exception    test_link_closes_transport_after_exception
"""

from __future__ import annotations

import time

import pytest

from app.mqtt import transport as transport_mod
from app.mqtt.transport import (
    MqttMessage,
    MqttSettings,
    MqttTransportError,
    PahoMqttTransport,
    open_mqtt_transport,
)
from app.scenarios.smart_home_live import LiveMqttLink


SETTINGS = MqttSettings(
    host="192.168.50.1",
    port=1883,
    username="panel1-guest",
    password="unit-test-secret",
    client_id="trainer-hack-test",
    connect_timeout_seconds=1.0,
    publish_timeout_seconds=1.0,
)


# --- a fake paho client -----------------------------------------------------


class FakeInfo:
    def __init__(self, rc: int = 0, raise_on_wait: bool = False) -> None:
        self.rc = rc
        self._raise = raise_on_wait
        self.waited = False

    def wait_for_publish(self, timeout: float | None = None) -> None:
        self.waited = True
        if self._raise:
            raise RuntimeError("publish never acknowledged")


class FakePahoClient:
    """Enough of paho's Client surface for the transport to drive."""

    def __init__(
        self,
        client_id: str,
        *,
        connect_error: bool = False,
        subscribe_code: int = 0,
        publish_rc: int = 0,
        publish_raises: bool = False,
    ) -> None:
        self.client_id = client_id
        self.on_message = None
        self._connect_error = connect_error
        self._subscribe_code = subscribe_code
        self._publish_rc = publish_rc
        self._publish_raises = publish_raises
        self.username = None
        self.password = None
        self.looping = False
        self.disconnected = False

    def username_pw_set(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def connect(self, host: str, port: int, keepalive: int) -> None:
        if self._connect_error:
            raise OSError("connection refused")

    def loop_start(self) -> None:
        self.looping = True

    def loop_stop(self) -> None:
        self.looping = False

    def subscribe(self, topic: str, qos: int = 0):
        return (self._subscribe_code, 1)

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False):
        if self._publish_raises:
            return FakeInfo(rc=0, raise_on_wait=True)
        return FakeInfo(rc=self._publish_rc)

    def disconnect(self) -> None:
        self.disconnected = True

    # test helper: simulate a message arriving on paho's network thread
    def deliver(self, topic: str, payload: bytes) -> None:
        message = type("M", (), {"topic": topic, "payload": payload})()
        self.on_message(self, None, message)


def _patch_client(monkeypatch, client: FakePahoClient) -> None:
    monkeypatch.setattr(transport_mod, "_client_class", lambda: (lambda cid: client))
    monkeypatch.setattr(transport_mod, "mqtt_available", lambda: True)


# --- A/B: connect -----------------------------------------------------------


def test_connect_success_with_fake_client(monkeypatch) -> None:
    client = FakePahoClient("id")
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        assert client.username == "panel1-guest"
        assert client.password == "unit-test-secret"
        assert client.looping is True
    finally:
        transport.close()
    assert client.disconnected is True


def test_connect_failure_is_a_truthful_error(monkeypatch) -> None:
    client = FakePahoClient("id", connect_error=True)
    _patch_client(monkeypatch, client)
    with pytest.raises(MqttTransportError) as exc:
        open_mqtt_transport(SETTINGS)
    assert "could not connect" in str(exc.value).lower()


# --- C/D: publish -----------------------------------------------------------


def test_publish_success(monkeypatch) -> None:
    client = FakePahoClient("id", publish_rc=0)
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        transport.publish("cybertrainer/smart-home/motor/control", "START")
    finally:
        transport.close()


def test_publish_failure_is_a_truthful_error(monkeypatch) -> None:
    client = FakePahoClient("id", publish_rc=1)
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        with pytest.raises(MqttTransportError) as exc:
            transport.publish("t", "START")
        assert "rejected a publish" in str(exc.value).lower()
    finally:
        transport.close()


def test_publish_that_never_acknowledges_is_a_truthful_error(monkeypatch) -> None:
    client = FakePahoClient("id", publish_raises=True)
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        with pytest.raises(MqttTransportError) as exc:
            transport.publish("t", "START")
        assert "did not complete" in str(exc.value).lower()
    finally:
        transport.close()


# --- E/F: subscribe + receive -----------------------------------------------


def test_subscribe_delivers_received_message(monkeypatch) -> None:
    client = FakePahoClient("id")
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        transport.subscribe("cybertrainer/smart-home/motor/state")
        client.deliver("cybertrainer/smart-home/motor/state", b"RUNNING")
        message = transport.next_message(timeout=1.0)
        assert message == MqttMessage(
            topic="cybertrainer/smart-home/motor/state", payload="RUNNING"
        )
    finally:
        transport.close()


def test_subscribe_rejection_is_truthful(monkeypatch) -> None:
    client = FakePahoClient("id", subscribe_code=1)
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        with pytest.raises(MqttTransportError):
            transport.subscribe("t")
    finally:
        transport.close()


def test_next_message_times_out_and_is_truthful(monkeypatch) -> None:
    client = FakePahoClient("id")
    _patch_client(monkeypatch, client)
    transport = open_mqtt_transport(SETTINGS)
    try:
        start = time.monotonic()
        message = transport.next_message(timeout=0.1)
        elapsed = time.monotonic() - start
        assert message is None
        assert elapsed < 1.0  # bounded, not a hang
    finally:
        transport.close()


# --- a fake transport, for the link's lifecycle -----------------------------


class FakeTransport:
    """Implements the MqttTransport surface without a broker."""

    def __init__(
        self,
        settings: MqttSettings,
        *,
        connect_error: str | None = None,
        subscribe_error: str | None = None,
        publish_error: str | None = None,
        inbox: list[MqttMessage] | None = None,
    ) -> None:
        self.settings = settings
        self._connect_error = connect_error
        self._subscribe_error = subscribe_error
        self._publish_error = publish_error
        self._inbox = list(inbox or [])
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.closed = False

    def connect(self) -> None:
        if self._connect_error:
            raise MqttTransportError(self._connect_error)

    def subscribe(self, topic: str) -> None:
        if self._subscribe_error:
            raise MqttTransportError(self._subscribe_error)
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._publish_error:
            raise MqttTransportError(self._publish_error)
        self.published.append((topic, payload))

    def next_message(self, timeout: float):
        if self._inbox:
            return self._inbox.pop(0)
        time.sleep(min(timeout, 0.01))
        return None

    def close(self) -> None:
        self.closed = True


STATE_TOPIC = "cybertrainer/smart-home/motor/state"
CONTROL_TOPIC = "cybertrainer/smart-home/motor/control"


def _link(fake: FakeTransport) -> LiveMqttLink:
    def factory(settings: MqttSettings):
        # Mirror `open_mqtt_transport`'s contract exactly: return a CONNECTED
        # transport, or close it and raise (leaking nothing on a failed open).
        try:
            fake.connect()
        except MqttTransportError:
            fake.close()
            raise
        return fake

    return LiveMqttLink(
        settings=SETTINGS,
        state_topic=STATE_TOPIC,
        transport_factory=factory,
        observe_timeout_seconds=0.1,
        listen_timeout_seconds=0.1,
    )


# --- G/H: link lifecycle ----------------------------------------------------


def test_link_closes_transport_after_publish() -> None:
    fake = FakeTransport(SETTINGS, inbox=[MqttMessage(STATE_TOPIC, "RUNNING")])
    outcome = _link(fake).publish_and_confirm(CONTROL_TOPIC, "START", "RUNNING")
    assert outcome.connected and outcome.published
    assert outcome.observed_state == "RUNNING"
    assert fake.published == [(CONTROL_TOPIC, "START")]
    assert fake.closed is True  # cleanup after success


def test_link_closes_transport_after_exception() -> None:
    fake = FakeTransport(SETTINGS, subscribe_error="broker dropped the subscription")
    outcome = _link(fake).publish_and_confirm(CONTROL_TOPIC, "START", "RUNNING")
    assert outcome.published is False
    assert outcome.error is not None
    assert fake.closed is True  # cleanup even when a step raised


def test_link_reports_connection_failure_without_publishing() -> None:
    fake = FakeTransport(SETTINGS, connect_error="connection refused")
    outcome = _link(fake).publish_and_confirm(CONTROL_TOPIC, "START", "RUNNING")
    assert outcome.connected is False
    assert outcome.published is False
    assert "connection refused" in (outcome.error or "")
    assert fake.closed is True


def test_link_publish_without_observed_state_returns_none_observed() -> None:
    fake = FakeTransport(SETTINGS, inbox=[])  # device says nothing
    outcome = _link(fake).publish_and_confirm(CONTROL_TOPIC, "START", "RUNNING")
    assert outcome.connected and outcome.published
    assert outcome.observed_state is None  # not fabricated
    assert fake.closed is True


def test_link_observe_returns_received_messages() -> None:
    fake = FakeTransport(SETTINGS, inbox=[MqttMessage(CONTROL_TOPIC, "START")])
    outcome = _link(fake).observe(CONTROL_TOPIC)
    assert outcome.connected is True
    assert outcome.messages == (MqttMessage(CONTROL_TOPIC, "START"),)
    assert fake.closed is True


def test_link_observe_timeout_is_bounded_and_empty() -> None:
    fake = FakeTransport(SETTINGS, inbox=[])
    start = time.monotonic()
    outcome = _link(fake).observe(CONTROL_TOPIC)
    elapsed = time.monotonic() - start
    assert outcome.connected is True
    assert outcome.messages == ()  # no invented traffic
    assert elapsed < 2.0  # bounded by listen_timeout_seconds
    assert fake.closed is True
