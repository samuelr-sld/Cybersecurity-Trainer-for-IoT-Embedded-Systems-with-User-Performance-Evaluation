"""The composition seam: attaching a real MQTT link to a Hack session.

`configure_live_mqtt` is the one place the panel package's declared criterion,
the deployment's provisioned credential, and the selected scenario meet. These
tests prove it attaches a correctly-parameterised link when everything is in
place, stays a no-op (simulation) in every honest fallback, reuses the
package's own authenticated-but-unauthorized identity, and never logs a secret.
"""

from __future__ import annotations

import ast
import logging
import pathlib

from app.hack_live_mqtt import configure_live_mqtt
from app.panels import default_panel_package_loader
from app.scenario_selection import ScenarioSelection, ScenarioSource
from app.scenarios import EnvironmentalMonitoringScenario, SmartHomeMQTTScenario
from app.scenarios.smart_home_live import LiveMqttLink
from app.panels.service import PanelResourceStatus

PANEL_ONE_ID = "smart-home-mqtt-control"
FAKE_PASSWORD = "lab-guest-pw-should-never-be-logged"


def _package():
    return default_panel_package_loader().load(PANEL_ONE_ID)


def _panel_selection(scenario=None) -> ScenarioSelection:
    return ScenarioSelection(
        scenario=scenario if scenario is not None else SmartHomeMQTTScenario(),
        source=ScenarioSource.PANEL_PACKAGE,
        scenario_id=PANEL_ONE_ID,
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE_ID,
        package=_package(),
    )


def _default_selection() -> ScenarioSelection:
    return ScenarioSelection(
        scenario=EnvironmentalMonitoringScenario(),
        source=ScenarioSource.DEFAULT,
        scenario_id="legacy-environmental-monitoring",
        panel_status=PanelResourceStatus.NOT_CONNECTED,
        panel_id=None,
        package=None,
    )


class _FakeTransport:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):  # pragma: no cover - not exercised here
        pass

    def subscribe(self, topic):  # pragma: no cover
        pass

    def publish(self, topic, payload):  # pragma: no cover
        pass

    def next_message(self, timeout):  # pragma: no cover
        return None

    def close(self):  # pragma: no cover
        pass


def _factory(settings):
    return _FakeTransport(settings)


def _secret(name: str) -> str:
    # Only the criterion's provisioned names resolve; everything else is unset.
    provisioned = {
        "TRAINER_LAB_PANEL1_GUEST_PASSWORD": FAKE_PASSWORD,
        "TRAINER_LAB_PANEL1_VALIDATOR_PASSWORD": "validator-pw",
        "TRAINER_LAB_PANEL1_COMMAND_TOKEN": "PANEL1-CMD-AUTH-K7",
    }
    return provisioned.get(name, "")


# --- attaches when everything is in place -----------------------------------


def test_attaches_a_link_with_the_criterions_broker_and_topics() -> None:
    selection = _panel_selection()
    link = configure_live_mqtt(
        selection, secret_reader=_secret, transport_factory=_factory, enabled=True
    )
    assert isinstance(link, LiveMqttLink)
    criterion = _package().remediation.criterion
    assert link.settings.host == criterion.broker_host
    assert link.settings.port == criterion.broker_port
    assert link.state_topic == criterion.state_topic


def test_reuses_the_packages_unauthorized_identity_and_its_password() -> None:
    selection = _panel_selection()
    link = configure_live_mqtt(
        selection, secret_reader=_secret, transport_factory=_factory, enabled=True
    )
    criterion = _package().remediation.criterion
    # The attacker is the authenticated-but-unauthorized lab client.
    assert link.settings.username == criterion.unauthorized.username
    assert link.settings.username == "panel1-guest"
    assert link.settings.password == FAKE_PASSWORD


def test_the_selected_scenario_actually_goes_live() -> None:
    scenario = SmartHomeMQTTScenario()
    selection = _panel_selection(scenario=scenario)
    configure_live_mqtt(
        selection, secret_reader=_secret, transport_factory=_factory, enabled=True
    )
    # A publish now routes through the fake transport (live), not the sim:
    # the wrong-target refusal is a live-path message, and the sim would have
    # actuated instead.
    outcome = scenario.publish("10.0.0.99", None, "cybertrainer/smart-home/motor/control", "START")
    assert "does not match the discovered broker" in "\n".join(outcome.lines)


# --- honest fallbacks (stay simulated) --------------------------------------


def test_disabled_flag_attaches_nothing() -> None:
    selection = _panel_selection()
    link = configure_live_mqtt(
        selection, secret_reader=_secret, transport_factory=_factory, enabled=False
    )
    assert link is None


def test_missing_password_attaches_nothing() -> None:
    selection = _panel_selection()
    link = configure_live_mqtt(
        selection, secret_reader=lambda name: "", transport_factory=_factory, enabled=True
    )
    assert link is None


def test_default_selection_without_a_package_attaches_nothing() -> None:
    link = configure_live_mqtt(
        _default_selection(), secret_reader=_secret, transport_factory=_factory, enabled=True
    )
    assert link is None


def test_a_non_live_capable_scenario_is_left_simulated() -> None:
    # A (contrived) panel package with a criterion but a scenario that cannot
    # drive a live link: no attachment, and no crash.
    selection = ScenarioSelection(
        scenario=EnvironmentalMonitoringScenario(),
        source=ScenarioSource.PANEL_PACKAGE,
        scenario_id="legacy-environmental-monitoring",
        panel_status=PanelResourceStatus.READY,
        panel_id=PANEL_ONE_ID,
        package=_package(),
    )
    link = configure_live_mqtt(
        selection, secret_reader=_secret, transport_factory=_factory, enabled=True
    )
    assert link is None


# --- the password is never logged -------------------------------------------


def test_password_is_never_logged(caplog) -> None:
    selection = _panel_selection()
    with caplog.at_level(logging.DEBUG):
        configure_live_mqtt(
            selection, secret_reader=_secret, transport_factory=_factory, enabled=True
        )
    assert FAKE_PASSWORD not in caplog.text


# --- architecture: the Hack live-MQTT path never imports Build validation ---

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_HACK_MQTT_MODULES = (
    _APP / "mqtt" / "transport.py",
    _APP / "mqtt" / "__init__.py",
    _APP / "scenarios" / "smart_home_live.py",
    _APP / "hack_live_mqtt.py",
)


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_hack_mqtt_path_does_not_import_build_validation() -> None:
    """Hack Mode reuses Build's paho PATTERN, never its code (see the phase
    brief: 'Do NOT import app.build.validation.smart_home into Hack Mode')."""
    for path in _HACK_MQTT_MODULES:
        for imported in _imports(path):
            assert not imported.startswith("app.build"), f"{path.name} imports {imported}"


def test_generic_transport_imports_no_app_layer() -> None:
    """`app/mqtt/transport.py` is generic infrastructure: it must not depend on
    scenarios, panels, config, build, or hardware — only the stdlib and paho
    (the latter lazily, inside functions)."""
    for imported in _imports(_APP / "mqtt" / "transport.py"):
        assert not imported.startswith("app."), f"transport.py imports {imported}"
