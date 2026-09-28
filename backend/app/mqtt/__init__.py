"""A small, generic MQTT transport seam for the backend.

This package exists so that a caller who needs to talk to a real MQTT broker
does so through ONE thin, honest wrapper around paho-mqtt, instead of every
caller importing paho and re-implementing connect/publish/subscribe/teardown.

    caller -> MqttTransport (this package) -> paho-mqtt -> Mosquitto -> device

WHAT IT IS, AND WHAT IT IS NOT. `transport.py` is generic: it accepts a host,
a port and a credential, and it publishes and receives plain payload bytes on
plain topics. It holds no knowledge of any panel, scenario, motor, command
word, topic name, or vulnerability — those belong to whatever drives it. It
imports no other `app` layer (not `app.config`, not `app.build`, not
`app.scenarios`), so it cannot grow a dependency on the courseware it happens
to carry bytes for.

WHY IT MIRRORS `app/build/validation/mqtt_evidence.py` RATHER THAN IMPORTING
IT. Build Mode's B8 remediation check already established the exact paho
pattern this needs — lazy import, the 2.x callback-API shim, bounded connect
and publish timeouts, `loop_start`/`loop_stop`, `wait_for_publish`, a clean
disconnect, and truthful errors. But that module is a Build-validation
concern (it knows about `AuthorizationCriterion` and evidence verdicts), and
Hack Mode may not import Build validation. So this package reuses the
*pattern*, not the code: a generic transport both a future Hack path and any
other caller can use without dragging Build's verdict logic along.

NOT A SHELL, NOT A PROCESS. Nothing here spawns a process, imports
`subprocess`/`os`, evaluates a string, or builds a command line. It opens a
TCP connection and moves bytes.
"""

from app.mqtt.transport import (
    MqttMessage,
    MqttSettings,
    MqttTransport,
    MqttTransportError,
    PahoMqttTransport,
    TransportFactory,
    mqtt_available,
    open_mqtt_transport,
)

__all__ = [
    "MqttMessage",
    "MqttSettings",
    "MqttTransport",
    "MqttTransportError",
    "PahoMqttTransport",
    "TransportFactory",
    "mqtt_available",
    "open_mqtt_transport",
]
