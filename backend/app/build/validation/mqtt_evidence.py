"""The evidence channel a per-command authorization check reads (Phase B8).

    SmartHomeAuthorizationValidator          decides what the evidence MEANS
              |
              v
    AuthorizationEvidence   (this module's Protocol)
              |
              +-- MqttStateTopicEvidence    real: paho-mqtt -> Mosquitto -> ESP32
              |
              +-- (a test double)           deterministic, no broker, no board

WHY THE TRANSPORT IS A SEAM. The question "did the remediated firmware obey
this command?" is answered by reading a physical device's own state
publication. That is genuinely an I/O problem — a broker, credentials, a
network, timing — and the VERDICT logic (which probes, in what order, what
counts as a pass) is not. Separating them is what lets B8 unit-test the whole
decision table deterministically while the real channel stays a thin,
honest wrapper around paho-mqtt, exactly as `CompilerAdapter`/`FlasherAdapter`
let `BuildService` be tested without spawning `arduino-cli`.

IT DOES NOT FABRICATE. There is no simulated device here and no canned
success: `MqttStateTopicEvidence` either talks to a real broker or raises
`EvidenceChannelError`, which the validator reports as ERROR or UNAVAILABLE.
The test double lives in the test suite, never in this package, so nothing
shipped can produce a passing verdict without hardware.

paho-mqtt IS IMPORTED LAZILY, exactly as `pyserial` is in
`app/hardware/serial_transport.py` and for the same reason: a backend without
it must still start, and a check that cannot run must say so rather than
crash the session. `available()` is the question `unavailable_reason` asks.

NOT A SHELL, NOT A PROCESS. This module spawns nothing, imports no
`subprocess`/`os`, evaluates nothing, and builds no command line. It opens a
TCP connection to the one broker the criterion names and publishes plain
payload bytes onto one topic.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Mapping, Protocol, runtime_checkable

from app.build.validation.criteria import AuthorizationCriterion

logger = logging.getLogger(__name__)

#: How long a broker connection attempt may take before it is abandoned.
CONNECT_TIMEOUT_SECONDS = 10.0

#: MQTT keepalive for the short-lived validator connections.
KEEPALIVE_SECONDS = 30


class EvidenceChannelError(RuntimeError):
    """The evidence channel could not be used, or stopped working mid-check.

    Always an ERROR outcome, never a FAILURE: a broker that refused a
    connection, a credential the deployment got wrong, or a lost link has
    proved nothing whatever about the student's firmware.
    """


@runtime_checkable
class AuthorizationEvidence(Protocol):
    """What a validator needs from the channel it observes a device through.

    Deliberately blocking and deliberately small. The validator runs it in a
    worker thread (`asyncio.to_thread`), the same way Hack Mode's serial
    transport keeps pyserial's blocking reads off the event loop.
    """

    def state(self) -> str | None:
        """The most recently observed device state, or None if none yet."""
        ...

    def await_state(self, expected: str, timeout: float) -> str | None:
        """Wait up to `timeout` for the device to report `expected`.

        Returns the state observed when waiting stopped — `expected` if it
        arrived, otherwise whatever the last observation was. Never raises
        for a state that simply did not arrive; that is a verdict, not an
        error.
        """
        ...

    def await_any_state(self, timeout: float) -> str | None:
        """Wait up to `timeout` for the device to report ANY state.

        The baseline read: before a single probe is sent, the check has to
        establish that the evidence channel is actually carrying this
        device's state. None means it never did, which is an ERROR about the
        channel rather than a verdict about the firmware.
        """
        ...

    def publish(self, identity_id: str, topic: str, payload: str) -> None:
        """Publish one payload as the named declared identity."""
        ...

    def settle(self, seconds: float) -> None:
        """Wait out the window in which an obeyed command would have shown."""
        ...

    def close(self) -> None:
        """Release the channel. Safe to call twice; never raises."""
        ...


def available() -> bool:
    """Whether the MQTT client library this channel needs is installed."""
    try:
        import paho.mqtt.client  # noqa: F401
    except Exception:  # pragma: no cover - import environment dependent
        return False
    return True


def _client_class():
    """paho's `Client`, constructed for whichever major version is installed.

    paho-mqtt 2.x requires an explicit callback API version; 1.x has no such
    argument. Probing the attribute rather than the version string keeps this
    working across both without a version comparison to get wrong.
    """
    import paho.mqtt.client as mqtt

    api_version = getattr(mqtt, "CallbackAPIVersion", None)
    if api_version is None:  # paho-mqtt 1.x
        return lambda client_id: mqtt.Client(client_id=client_id)
    return lambda client_id: mqtt.Client(api_version.VERSION1, client_id=client_id)


class MqttStateTopicEvidence:
    """A real authenticated MQTT session against the trainer's own broker.

    ONE OBSERVER, TWO PUBLISHERS, AND THEY ARE DIFFERENT ACCOUNTS ON PURPOSE.
    The observer subscribes to the device's state topic as the authorized lab
    controller. Each declared identity gets its own authenticated connection,
    because the whole point of the check is that BOTH can connect — the
    unauthorized one is authenticated to the broker and still must not be
    obeyed. An unauthorized identity that cannot authenticate is an
    `EvidenceChannelError`, not a pass: it would mean the broker, rather than
    the firmware, blocked the command, and the student's fix was never tested.
    """

    def __init__(
        self,
        criterion: AuthorizationCriterion,
        secrets: Mapping[str, str],
        *,
        client_id_prefix: str = "trainer-validator",
    ) -> None:
        self._criterion = criterion
        self._secrets = dict(secrets)
        self._prefix = client_id_prefix
        self._lock = threading.Lock()
        self._observed: str | None = None
        self._changed = threading.Event()
        self._clients: dict[str, object] = {}
        self._observer = None
        self._closed = False

    # --- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        """Connect the observer and every declared identity. Raises on failure."""
        criterion = self._criterion
        self._observer = self._connect(criterion.authorized, role="observer")
        self._subscribe(self._observer, criterion.state_topic)
        for identity in (criterion.authorized, criterion.unauthorized):
            self._clients[identity.identity_id] = self._connect(identity, role="publisher")

    def _password(self, env_name: str) -> str:
        secret = self._secrets.get(env_name, "")
        if not secret:
            # Should be unreachable: `unavailable_reason` checks every name
            # before a check is allowed to start. Kept because connecting with
            # a blank password would reach the broker as an anonymous attempt
            # and produce a misleading refusal.
            raise EvidenceChannelError(
                f"lab fixture {env_name} is not provisioned, so this check cannot connect"
            )
        return secret

    def _connect(self, identity, *, role: str):
        build = _client_class()
        client = build(f"{self._prefix}-{role}-{identity.identity_id}")
        client.username_pw_set(identity.username, self._password(identity.password_env))
        client.on_message = self._on_message
        try:
            client.connect(
                self._criterion.broker_host,
                self._criterion.broker_port,
                KEEPALIVE_SECONDS,
            )
        except Exception as error:  # noqa: BLE001 - every transport failure is one outcome
            raise EvidenceChannelError(
                f"could not connect to the broker at {self._criterion.broker_host}:"
                f"{self._criterion.broker_port} as {identity.identity_id}: {error}"
            ) from error
        client.loop_start()
        return client

    def _subscribe(self, client, topic: str) -> None:
        result = client.subscribe(topic, qos=1)
        code = result[0] if isinstance(result, tuple) else result
        if code != 0:
            raise EvidenceChannelError(
                f"could not subscribe to the evidence topic {topic} (code {code})"
            )

    def _on_message(self, client, userdata, message) -> None:  # pragma: no cover - callback
        try:
            text = message.payload.decode("utf-8", errors="replace").strip().upper()
        except Exception:  # noqa: BLE001 - a malformed retained message is not fatal
            return
        with self._lock:
            self._observed = text
        self._changed.set()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        clients = [self._observer, *self._clients.values()]
        for client in clients:
            if client is None:
                continue
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:  # noqa: BLE001 - teardown must never raise
                logger.debug("mqtt evidence client did not close cleanly", exc_info=True)

    # --- observation --------------------------------------------------------

    def state(self) -> str | None:
        with self._lock:
            return self._observed

    def await_state(self, expected: str, timeout: float) -> str | None:
        return self._await(timeout, lambda current: current == expected)

    def await_any_state(self, timeout: float) -> str | None:
        return self._await(timeout, lambda current: current is not None)

    def _await(self, timeout: float, satisfied) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            current = self.state()
            if satisfied(current):
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return current
            # Cleared before re-reading, so a message that arrives between
            # the read above and the wait below is not missed.
            self._changed.clear()
            self._changed.wait(min(remaining, 0.25))

    def publish(self, identity_id: str, topic: str, payload: str) -> None:
        client = self._clients.get(identity_id)
        if client is None:
            raise EvidenceChannelError(f"no connected client for identity {identity_id!r}")
        info = client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001
            raise EvidenceChannelError(
                f"publishing as {identity_id!r} did not complete: {error}"
            ) from error
        if getattr(info, "rc", 0) != 0:
            raise EvidenceChannelError(
                f"the broker rejected a publish from {identity_id!r} (rc {info.rc})"
            )

    def settle(self, seconds: float) -> None:
        time.sleep(seconds)


def open_mqtt_evidence(
    criterion: AuthorizationCriterion, secrets: Mapping[str, str]
) -> AuthorizationEvidence:
    """The default factory: a real, connected MQTT evidence channel.

    Raises `EvidenceChannelError` for anything that stops the channel opening
    — a missing client library, a refused connection, a rejected credential.
    """
    if not available():
        raise EvidenceChannelError(
            "the MQTT client library (paho-mqtt) is not installed in this backend"
        )
    channel = MqttStateTopicEvidence(criterion, secrets)
    try:
        channel.open()
    except EvidenceChannelError:
        channel.close()
        raise
    except Exception as error:  # noqa: BLE001 - never leak a half-open channel
        channel.close()
        raise EvidenceChannelError(f"the evidence channel could not be opened: {error}") from error
    return channel
