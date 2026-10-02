"""The live MQTT link that turns Panel 1's forged-command lesson physical.

    SmartHomeMQTTScenario.publish/observe
              |
              v
    LiveMqttLink            (this module: per-command connect -> act -> close)
              |
              v
    MqttTransport           (app/mqtt/transport.py: generic paho wrapper)
              |
              v
    Mosquitto (192.168.50.1:1883) -> ESP32 motor controller -> state topic

WHY A SEPARATE OBJECT, AND WHY HERE. `SmartHomeMQTTScenario` decides what a
publish MEANS — whether a payload is a forged START/STOP, and what "the device
obeyed it" is. That is scenario behaviour and stays in the scenario. Opening a
real broker connection, publishing bytes, and reading the device's own state
publication is I/O, and it lives here so the scenario can be driven either
against this real link OR (its default) against nothing at all, in memory.

    scenario with `live=None`   ->  deterministic in-memory simulation
    scenario with a LiveMqttLink ->  real broker, real device, real evidence

This module imports ONLY the generic transport (`app.mqtt.transport`); it does
not import paho, `app.build`, `app.hardware`, `app.config`, `subprocess`, or
`os`. It holds no credential of its own — an `MqttSettings` is injected, and
whoever built it is responsible for sourcing the password safely.

PHYSICAL IMPACT IS EVIDENCED, NEVER ASSUMED. `publish_and_confirm` publishes
the student's payload and then WAITS on the device's own state topic for the
state a real actuation would produce. A publish the broker accepted is not the
same fact as a motor that moved, so the outcome reports them separately: the
scenario claims physical impact only when `observed_state` confirms it.

BOUNDED AND SELF-CLEANING. Every operation opens its own connection, does one
bounded thing, and closes it in a `finally` — on success, connection failure,
publish failure, timeout, or exception alike. No client thread outlives the
command that opened it, and no operation blocks without a deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.mqtt.transport import (
    MqttMessage,
    MqttSettings,
    MqttTransport,
    MqttTransportError,
    TransportFactory,
    open_mqtt_transport,
)

#: Default bounded window to watch the state topic for the device's reaction.
DEFAULT_OBSERVE_TIMEOUT_SECONDS = 5.0

#: Default bounded window a subscription listens for traffic before giving up.
DEFAULT_LISTEN_TIMEOUT_SECONDS = 5.0

#: Upper bound on messages one observation returns, so a flooded topic cannot
#: grow an unbounded result.
MAX_OBSERVED_MESSAGES = 64


@runtime_checkable
class LiveMqttCapable(Protocol):
    """A scenario that can drive its target over a real MQTT link.

    The composition layer offers a `LiveMqttLink` to whatever scenario the
    attached panel selected; a scenario that implements this stores it and
    goes live, and one that does not is simply never offered anything it can
    use. This is what lets the wiring stay free of a `if scenario is Panel 1`
    branch — capability, not identity, decides.
    """

    def use_live_mqtt(self, link: "LiveMqttLink") -> None:
        """Attach a live MQTT link, replacing the in-memory simulation."""
        ...


@dataclass(frozen=True)
class LivePublishOutcome:
    """What one real forged-command publish actually achieved.

    The three questions the scenario must answer separately and truthfully:
    did we reach the broker, did the publish complete, and did the device's
    OWN state topic then report the state a real actuation would cause.
    `observed_state` is None when the device reported nothing within the
    window — which is exactly the case the scenario must NOT read as impact.
    """

    connected: bool
    published: bool
    observed_state: str | None
    error: str | None = None


@dataclass(frozen=True)
class LiveObserveOutcome:
    """What one real subscription observed within its bounded window."""

    connected: bool
    messages: tuple[MqttMessage, ...] = ()
    error: str | None = None


@dataclass
class LiveMqttLink:
    """A live connection recipe for one panel's broker, opened per command.

    Carries the connection `settings` (host/port/credential) and the topic the
    device publishes its state on. `transport_factory` is injectable purely so
    tests drive the whole path against a fake transport with no broker; in
    production it is the real paho factory.
    """

    settings: MqttSettings
    state_topic: str
    transport_factory: TransportFactory = open_mqtt_transport
    observe_timeout_seconds: float = DEFAULT_OBSERVE_TIMEOUT_SECONDS
    listen_timeout_seconds: float = DEFAULT_LISTEN_TIMEOUT_SECONDS
    _poll_interval_seconds: float = field(default=0.25, repr=False)

    def publish_and_confirm(
        self, topic: str, payload: str, expected_state: str | None
    ) -> LivePublishOutcome:
        """Publish `payload` to `topic`, then watch the state topic for proof.

        Connects, subscribes to the state topic, publishes, and — when an
        `expected_state` is given — waits a bounded time for the device to
        report exactly that state. Returns what actually happened; never
        raises for a transport failure (that is one of the reportable
        outcomes), and always closes the connection.

        Published retained. The real device never republishes a command it
        receives (only its own state, on `state_topic`), and this publish
        itself is gone from the wire the instant the broker delivers it — so
        a `mosquitto_sub` on `topic` started even a moment later would
        otherwise see no legitimate way to ever observe this command, no
        matter how many times it is reissued. Retaining it is ordinary MQTT
        behaviour (the broker genuinely holds this exact message as the
        topic's last-known value), not a fabricated observation: a later
        subscriber really is being told the truth about what was last
        published here.
        """
        try:
            transport = self.transport_factory(self.settings)
        except MqttTransportError as error:
            return LivePublishOutcome(
                connected=False, published=False, observed_state=None, error=str(error)
            )
        try:
            transport.subscribe(self.state_topic)
            try:
                transport.publish(topic, payload, retain=True)
            except MqttTransportError as error:
                return LivePublishOutcome(
                    connected=True, published=False, observed_state=None, error=str(error)
                )
            observed = self._await_state(transport, expected_state)
            return LivePublishOutcome(
                connected=True, published=True, observed_state=observed, error=None
            )
        except MqttTransportError as error:
            # A subscribe failure, or the link dropping mid-observation.
            return LivePublishOutcome(
                connected=True, published=False, observed_state=None, error=str(error)
            )
        finally:
            transport.close()

    def observe(self, topic: str) -> LiveObserveOutcome:
        """Subscribe to `topic` and collect what arrives within the window.

        Bounded: it listens `listen_timeout_seconds` and returns whatever it
        saw — possibly nothing, which is reported honestly rather than filled
        with invented traffic. Always closes the connection.
        """
        try:
            transport = self.transport_factory(self.settings)
        except MqttTransportError as error:
            return LiveObserveOutcome(connected=False, messages=(), error=str(error))
        try:
            transport.subscribe(topic)
            messages = self._collect(transport)
            return LiveObserveOutcome(connected=True, messages=messages, error=None)
        except MqttTransportError as error:
            return LiveObserveOutcome(connected=True, messages=(), error=str(error))
        finally:
            transport.close()

    # -- internals ---------------------------------------------------------

    def _await_state(
        self, transport: MqttTransport, expected_state: str | None
    ) -> str | None:
        """Return the last state the device reported within the window.

        Watches only the state topic. If `expected_state` is given, returns as
        soon as that state is seen (case-insensitively); otherwise returns the
        last state observed before the window elapsed, or None if the device
        said nothing at all.
        """
        deadline = _Deadline(self.observe_timeout_seconds)
        last_state: str | None = None
        want = expected_state.strip().upper() if expected_state else None
        while not deadline.expired():
            message = transport.next_message(
                min(self._poll_interval_seconds, deadline.remaining())
            )
            if message is None:
                continue
            if message.topic != self.state_topic:
                continue
            last_state = message.payload.strip()
            if want is not None and last_state.upper() == want:
                return last_state
        return last_state

    def _collect(self, transport: MqttTransport) -> tuple[MqttMessage, ...]:
        """Collect up to `MAX_OBSERVED_MESSAGES` within the listen window."""
        deadline = _Deadline(self.listen_timeout_seconds)
        messages: list[MqttMessage] = []
        while not deadline.expired() and len(messages) < MAX_OBSERVED_MESSAGES:
            message = transport.next_message(
                min(self._poll_interval_seconds, deadline.remaining())
            )
            if message is not None:
                messages.append(message)
        return tuple(messages)


class _Deadline:
    """A small monotonic countdown, so waits are always bounded."""

    def __init__(self, seconds: float) -> None:
        import time

        self._end = time.monotonic() + max(seconds, 0.0)

    def remaining(self) -> float:
        import time

        return max(self._end - time.monotonic(), 0.0)

    def expired(self) -> bool:
        return self.remaining() <= 0.0
