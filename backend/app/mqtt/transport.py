"""A generic, bounded MQTT client wrapper over paho-mqtt.

See this package's `__init__` for why it exists and what it deliberately does
NOT know about. In short: it connects to one broker with one credential and
moves plain bytes on plain topics, and it is safe to open and close many
times because every client thread it starts is stopped again on `close()`.

LIFECYCLE DISCIPLINE (borrowed from `serial_transport.py` and
`mqtt_evidence.py`). paho is blocking and callback-driven, so:

  * paho is imported lazily — a backend without it still starts, and a caller
    asks `mqtt_available()` first.
  * `connect()` bounds the connection attempt with a timeout and raises
    `MqttTransportError` on any failure rather than hanging.
  * received messages arrive on paho's network thread and are handed to a
    thread-safe queue; `next_message(timeout)` blocks a bounded time for one.
  * `publish()` waits a bounded time for the broker to acknowledge and raises
    on rejection or timeout — it never reports a publish that did not complete.
  * `close()` is idempotent, never raises, and stops paho's network thread, so
    no client thread outlives the operation that opened it.

TRUTHFUL ERRORS. Every transport failure — a refused connection, a rejected
credential, a publish the broker did not acknowledge — is a single
`MqttTransportError`. The wrapper never fabricates a success and never invents
a received message.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: How long a broker connection attempt may take before it is abandoned.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0

#: How long `publish` waits for the broker to acknowledge before giving up.
DEFAULT_PUBLISH_TIMEOUT_SECONDS = 10.0

#: MQTT keepalive for these short-lived connections.
DEFAULT_KEEPALIVE_SECONDS = 30

#: Upper bound on messages buffered from the broker between reads, so a
#: talkative topic cannot grow memory without bound; the oldest are dropped.
DEFAULT_MAX_BUFFERED_MESSAGES = 512


class MqttTransportError(RuntimeError):
    """The transport could not be used, or stopped working mid-operation.

    A refused connection, a rejected credential, a publish the broker never
    acknowledged, or a lost link. Always a truthful "this did not work",
    never a disguised success.
    """


@dataclass(frozen=True)
class MqttSettings:
    """Everything needed to open one authenticated broker connection.

    Plain values, injected by the caller. This carries a password because a
    live connection needs one; the caller is responsible for sourcing it
    safely (never from client input, never into a log) and for not retaining
    it longer than the connection.
    """

    host: str
    port: int
    username: str
    password: str
    client_id: str
    keepalive_seconds: int = DEFAULT_KEEPALIVE_SECONDS
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    publish_timeout_seconds: float = DEFAULT_PUBLISH_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("mqtt host must be a non-empty string")
        if not isinstance(self.port, int) or not (1 <= self.port <= 65535):
            raise ValueError("mqtt port must be a valid TCP port")
        for name in ("username", "client_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"mqtt {name} must be a non-empty string")
        # A password may legitimately be any string; only its presence as a
        # str is required. It is never validated for content or logged.
        if not isinstance(self.password, str):
            raise ValueError("mqtt password must be a string")


@dataclass(frozen=True)
class MqttMessage:
    """One message received from the broker: its topic and decoded payload."""

    topic: str
    payload: str


@runtime_checkable
class MqttTransport(Protocol):
    """The narrow, blocking surface a caller drives.

    Deliberately blocking: a caller that must not block the event loop runs it
    on a worker thread (`asyncio.to_thread`), exactly as Hack Mode's serial
    transport keeps pyserial's blocking reads off the loop.
    """

    def connect(self) -> None:
        """Open and authenticate the connection. Raises `MqttTransportError`."""
        ...

    def subscribe(self, topic: str) -> None:
        """Subscribe to a topic. Raises `MqttTransportError` on rejection."""
        ...

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        """Publish one payload and wait for the broker to acknowledge it.

        `retain` asks the broker to keep this as the topic's last-known
        message, delivered immediately to a client that subscribes later —
        ordinary MQTT retain semantics, not something this wrapper invents.

        Raises `MqttTransportError` if the publish is rejected or not
        acknowledged within the settings' publish timeout.
        """
        ...

    def next_message(self, timeout: float) -> MqttMessage | None:
        """Wait up to `timeout` for the next received message, or None."""
        ...

    def close(self) -> None:
        """Release the connection. Safe to call twice; never raises."""
        ...


#: How a caller injects a different transport — a test double, most often.
TransportFactory = Callable[[MqttSettings], MqttTransport]


def mqtt_available() -> bool:
    """Whether the MQTT client library this transport needs is installed."""
    try:
        import paho.mqtt.client  # noqa: F401
    except Exception:  # pragma: no cover - import environment dependent
        return False
    return True


def _client_class():
    """paho's `Client`, constructed for whichever major version is installed.

    paho-mqtt 2.x requires an explicit callback API version; 1.x has no such
    argument. Probing the attribute rather than the version string keeps this
    working across both, exactly as `mqtt_evidence.py` does.
    """
    import paho.mqtt.client as mqtt

    api_version = getattr(mqtt, "CallbackAPIVersion", None)
    if api_version is None:  # paho-mqtt 1.x
        return lambda client_id: mqtt.Client(client_id=client_id)
    return lambda client_id: mqtt.Client(api_version.VERSION1, client_id=client_id)


class PahoMqttTransport:
    """A real authenticated MQTT connection to one broker.

    One connection, opened by `connect()` and released by `close()`. Received
    messages land on a bounded queue from paho's network thread; the caller
    drains them with `next_message`.
    """

    def __init__(
        self,
        settings: MqttSettings,
        *,
        max_buffered_messages: int = DEFAULT_MAX_BUFFERED_MESSAGES,
    ) -> None:
        self._settings = settings
        self._messages: "queue.Queue[MqttMessage]" = queue.Queue(
            maxsize=max_buffered_messages
        )
        self._client = None
        self._closed = False
        self._lock = threading.Lock()

    def connect(self) -> None:
        build = _client_class()
        client = build(self._settings.client_id)
        client.username_pw_set(self._settings.username, self._settings.password)
        client.on_message = self._on_message
        try:
            client.connect(
                self._settings.host,
                self._settings.port,
                self._settings.keepalive_seconds,
            )
        except Exception as error:  # noqa: BLE001 - one transport-failure outcome
            raise MqttTransportError(
                f"could not connect to the MQTT broker at "
                f"{self._settings.host}:{self._settings.port}: {error}"
            ) from error
        client.loop_start()
        self._client = client

    def _on_message(self, client, userdata, message) -> None:  # pragma: no cover - callback
        try:
            payload = message.payload.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - a malformed payload is not fatal
            return
        item = MqttMessage(topic=message.topic, payload=payload)
        try:
            self._messages.put_nowait(item)
        except queue.Full:
            # Drop the oldest to keep the most recent, matching the serial
            # transport's bounded-buffer discipline.
            try:
                self._messages.get_nowait()
                self._messages.put_nowait(item)
            except queue.Empty:  # pragma: no cover - race, harmless
                pass

    def subscribe(self, topic: str) -> None:
        if self._client is None:
            raise MqttTransportError("cannot subscribe before connect()")
        result = self._client.subscribe(topic, qos=1)
        code = result[0] if isinstance(result, tuple) else result
        if code != 0:
            raise MqttTransportError(
                f"the broker rejected a subscription to {topic!r} (code {code})"
            )

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._client is None:
            raise MqttTransportError("cannot publish before connect()")
        info = self._client.publish(topic, payload, qos=1, retain=retain)
        try:
            info.wait_for_publish(timeout=self._settings.publish_timeout_seconds)
        except Exception as error:  # noqa: BLE001
            raise MqttTransportError(
                f"publishing to {topic!r} did not complete: {error}"
            ) from error
        if getattr(info, "rc", 0) != 0:
            raise MqttTransportError(
                f"the broker rejected a publish to {topic!r} (rc {info.rc})"
            )

    def next_message(self, timeout: float) -> MqttMessage | None:
        try:
            return self._messages.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:  # noqa: BLE001 - teardown must never raise
            logger.debug("mqtt transport did not close cleanly", exc_info=True)


def open_mqtt_transport(settings: MqttSettings) -> MqttTransport:
    """The default factory: a real, connected paho transport.

    Raises `MqttTransportError` for anything that stops it opening — a missing
    client library, a refused connection, a rejected credential. Never leaks a
    half-open connection.
    """
    if not mqtt_available():
        raise MqttTransportError(
            "the MQTT client library (paho-mqtt) is not installed in this backend"
        )
    transport = PahoMqttTransport(settings)
    try:
        transport.connect()
    except MqttTransportError:
        transport.close()
        raise
    except Exception as error:  # noqa: BLE001 - never leak a half-open transport
        transport.close()
        raise MqttTransportError(f"the MQTT transport could not be opened: {error}") from error
    return transport
