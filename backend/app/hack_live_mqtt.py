"""Wire a Hack Mode session's scenario to the REAL training broker (the seam).

    select_session_scenario()            app/scenario_selection.py
        -> ScenarioSelection             (scenario + the package it came from)
              |
              v
    configure_live_mqtt(selection)       THIS module
              |
        reads: package.remediation.criterion   (broker, topics, lab identity)
        reads: config.lab_secret(...)           (the provisioned password)
              |
              v
    LiveMqttLink -> scenario.use_live_mqtt(link)    (scenario goes live)

WHY THIS IS ITS OWN MODULE, LIKE `build_validation_selection.py`. Three layers
must meet here and none may import the others directly: the scenario layer
(`app.scenarios`) knows nothing of packages or config; the package layer
(`app.panels`) knows nothing of scenarios or MQTT credentials; and the generic
transport (`app.mqtt`) knows nothing of any of them. Composing them needs a
third place, and this is it — the exact shape `app/scenario_selection.py` and
`app/build_validation_selection.py` already use.

THE ATTACKER IS A PROVISIONED, AUTHENTICATED-BUT-UNAUTHORIZED LAB CLIENT. The
identity used for the real attack is the criterion's own `unauthorized` client
— the same account B8's remediation check uses to prove a fixed device rejects
it. That is the whole lesson made physical: this client authenticates to the
broker successfully and still must not be able to actuate the motor, yet the
vulnerable firmware obeys it. Reusing that one declared identity means there is
NO second credential architecture, and no panel-specific literal here.

CREDENTIALS NEVER REACH THE CLIENT OR THE LOG. The password is read from a
`TRAINER_LAB_*` variable through `config.lab_secret` at connect and handed to
the transport; it is never sent to the frontend, never written to a Hack Mode
command/event record (the student's typed command line carries no password —
the backend supplies it), and never logged. This module logs only the broker
endpoint and the identity's account name.

IT IS SAFE BY FALLBACK. If the live path is disabled, or paho is not
installed, or the panel declares no criterion, or the credential is not
provisioned, or the selected scenario cannot use a live link, this attaches
nothing and the session stays the deterministic in-memory simulation — the
ordinary no-hardware development flow. It never raises into the connection.

NO PANEL BRANCH. There is no `if panel_id == ...`. A scenario goes live iff it
implements `LiveMqttCapable`; the link is built from whatever criterion the
attached panel's package declared. Adding a live panel is a package with a
criterion plus a scenario that implements the capability — never a branch here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

from app import config
from app.mqtt.transport import MqttSettings, TransportFactory, mqtt_available, open_mqtt_transport
from app.scenario_selection import ScenarioSelection
from app.scenarios.smart_home_live import LiveMqttCapable, LiveMqttLink

logger = logging.getLogger(__name__)


def configure_live_mqtt(
    selection: ScenarioSelection,
    *,
    secret_reader: Callable[[str], str] | None = None,
    transport_factory: TransportFactory | None = None,
    enabled: bool | None = None,
) -> LiveMqttLink | None:
    """Attach a real MQTT link to the selection's scenario, or leave it be.

    Returns the attached `LiveMqttLink` (also useful to a test), or None when
    the session stays simulated. Mutates the selection's scenario in place via
    `use_live_mqtt`; `ScenarioSelection` is frozen but the scenario it holds is
    not. Never raises — every reason to stay simulated is a return, logged.

    `secret_reader`, `transport_factory` and `enabled` are injected for tests,
    exactly as `SmartHomeAuthorizationValidator` injects its evidence factory
    and secret reader; production passes none and gets config + real paho.
    """
    read_secret = secret_reader if secret_reader is not None else config.lab_secret
    factory = transport_factory if transport_factory is not None else open_mqtt_transport
    is_enabled = config.HACK_LIVE_MQTT_ENABLED if enabled is None else enabled

    if not is_enabled:
        logger.debug("hack live MQTT disabled by configuration; using simulation")
        return None

    package = selection.package
    if package is None:
        # A default (no-panel) selection, or a fallback: nothing to configure.
        return None

    remediation = getattr(package, "remediation", None)
    criterion = getattr(remediation, "criterion", None) if remediation is not None else None
    if criterion is None:
        logger.debug(
            "panel %s declares no authorization criterion; hack MQTT stays simulated",
            selection.panel_id,
        )
        return None

    scenario = selection.scenario
    if not isinstance(scenario, LiveMqttCapable):
        # The declared scenario cannot drive a live link (e.g. a panel with a
        # criterion but a non-MQTT scenario). No branch on which panel it is.
        logger.debug(
            "scenario %r cannot use a live MQTT link; staying simulated",
            selection.scenario_id,
        )
        return None

    if factory is open_mqtt_transport and not mqtt_available():
        logger.warning(
            "hack live MQTT wanted for panel %s but paho-mqtt is not installed; "
            "using simulation",
            selection.panel_id,
        )
        return None

    identity = criterion.unauthorized
    password = read_secret(identity.password_env)
    if not password:
        logger.warning(
            "hack live MQTT wanted for panel %s but the lab credential %s is not "
            "provisioned; using simulation",
            selection.panel_id,
            identity.password_env,
        )
        return None

    settings = MqttSettings(
        host=criterion.broker_host,
        port=criterion.broker_port,
        username=identity.username,
        password=password,
        client_id=f"trainer-hack-{identity.identity_id}-{uuid.uuid4().hex[:8]}",
        connect_timeout_seconds=config.HACK_MQTT_CONNECT_TIMEOUT_SECONDS,
    )
    link = LiveMqttLink(
        settings=settings,
        state_topic=criterion.state_topic,
        transport_factory=factory,
        observe_timeout_seconds=config.HACK_MQTT_OBSERVE_TIMEOUT_SECONDS,
        listen_timeout_seconds=config.HACK_MQTT_LISTEN_TIMEOUT_SECONDS,
    )
    scenario.use_live_mqtt(link)
    logger.info(
        "hack live MQTT enabled for panel %s: %s@%s:%s (attacker=%s), state topic %s",
        selection.panel_id,
        identity.username,
        criterion.broker_host,
        criterion.broker_port,
        identity.identity_id,
        criterion.state_topic,
    )
    return link
