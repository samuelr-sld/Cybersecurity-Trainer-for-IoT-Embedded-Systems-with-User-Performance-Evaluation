"""Phase 2D.5 — the dedicated Smart Home MQTT Control scenario (Panel 1).

Phases 2D.3/2D.4 mapped Panel 1's `scenario_id` to
`EnvironmentalMonitoringScenario` as an explicit placeholder, to prove the
package -> scenario selection wiring end to end before Panel 1's own scenario
existed. This file covers the scenario that replaces that placeholder, and
that it slots into the *unchanged* generic contract.

The security model under test is Panel 1's, not the Environmental target's:
an ESP32 motor controller on an AUTHENTICATED MQTT broker whose command topic
carries no per-sender authorization, so a forged START/STOP is obeyed. The
lesson is "authentication is not authorization" — asserted here as behaviour,
not just wording.

Everything is deterministic and in memory: no sleeps, no real ESP32, no real
broker, no network, no subprocess. The scenario is exercised directly against
its `Scenario` interface and, for the wiring claims, through the real command
router.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re

import pytest

from app.commands import CommandContext, default_router
from app.panels import default_panel_package_loader
from app.scenarios import (
    EnvironmentalMonitoringScenario,
    Scenario,
    ScenarioEventType,
    SmartHomeMQTTScenario,
    default_scenario_registry,
)
from app.scenarios.base import EXIT_OK
from app.scenarios.smart_home_state import SmartHomeState
from app.sessions import HackSession

APP_SCENARIOS = pathlib.Path(__file__).resolve().parents[1] / "app" / "scenarios"

BROKER = "192.168.50.1"
PORT = 1883
COMMAND_TOPIC = "cybertrainer/smart-home/motor/control"
STATE_TOPIC = "cybertrainer/smart-home/motor/state"
PANEL_ONE_ID = "smart-home-mqtt-control"


def types(outcome) -> list[str]:
    return [event.type.value for event in outcome.events]


def fresh() -> SmartHomeMQTTScenario:
    return SmartHomeMQTTScenario()


def fully_owned() -> SmartHomeMQTTScenario:
    """A scenario driven through the whole intended workflow to completion."""
    sc = fresh()
    sc.extract_firmware()
    sc.analyze_firmware(None)
    sc.scan(BROKER, None)
    sc.observe(BROKER, None, COMMAND_TOPIC)
    sc.publish(BROKER, None, COMMAND_TOPIC, "START")
    return sc


# --- 1-3: construction and registry resolution ------------------------------


def test_scenario_satisfies_the_existing_contract() -> None:
    sc = fresh()
    assert isinstance(sc, Scenario)
    assert sc.scenario_id == PANEL_ONE_ID
    # Every abstract operation is present and callable.
    for name in (
        "extract_firmware",
        "analyze_firmware",
        "scan",
        "observe",
        "publish",
        "snapshot",
    ):
        assert callable(getattr(sc, name))
    assert isinstance(sc.state, SmartHomeState)
    assert sc.events == ()


def test_package_scenario_id_resolves_to_the_smart_home_scenario() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    scenario = default_scenario_registry.create_for_package(package)
    assert isinstance(scenario, SmartHomeMQTTScenario)
    assert not isinstance(scenario, EnvironmentalMonitoringScenario)


def test_environmental_still_resolves_to_the_environmental_scenario() -> None:
    scenario = default_scenario_registry.create("environmental-monitoring")
    assert isinstance(scenario, EnvironmentalMonitoringScenario)
    assert not isinstance(scenario, SmartHomeMQTTScenario)


def test_the_two_ids_resolve_to_different_classes() -> None:
    smart = default_scenario_registry.create(PANEL_ONE_ID)
    env = default_scenario_registry.create("environmental-monitoring")
    assert type(smart) is not type(env)


# --- 4: fresh initial state -------------------------------------------------


def test_a_fresh_scenario_is_in_its_initial_state() -> None:
    sc = fresh()
    state = sc.state
    assert state.stage.name == "INITIAL"
    assert state.motor.running is False
    assert state.motor.last_command is None
    for flag in (
        state.discovery.firmware_extracted,
        state.discovery.firmware_analyzed,
        state.discovery.broker_discovered,
        state.discovery.topic_discovered,
        state.discovery.service_discovered,
        state.discovery.mqtt_observed,
        state.attack.spoof_attempted,
        state.attack.spoof_successful,
        state.completion.attack_successful,
    ):
        assert flag is False
    # The broker is authenticated — this scenario never models it as open.
    assert state.target.broker_auth_required is True


# --- 5: firmware extraction -------------------------------------------------


def test_extract_firmware_sets_state_and_emits_event() -> None:
    sc = fresh()
    outcome = sc.extract_firmware()
    assert outcome.success and outcome.exit_code == EXIT_OK
    assert sc.state.discovery.firmware_extracted is True
    assert types(outcome) == ["firmware_extracted"]
    assert sc.state.stage.name == "FIRMWARE_EXTRACTED"


def test_extract_firmware_emits_the_event_only_once() -> None:
    sc = fresh()
    sc.extract_firmware()
    second = sc.extract_firmware()
    assert types(second) == []


# --- 6 & 7: strings and grep ------------------------------------------------


def test_strings_requires_the_firmware_to_have_been_read() -> None:
    sc = fresh()
    outcome = sc.analyze_firmware(None)
    assert outcome.success is False
    assert types(outcome) == []
    assert sc.state.discovery.firmware_analyzed is False


def test_strings_analyzes_and_emits_the_discovery_chain() -> None:
    sc = fresh()
    sc.extract_firmware()
    outcome = sc.analyze_firmware(None)
    assert outcome.success
    assert types(outcome) == [
        "firmware_analyzed",
        "broker_discovered",
        "topic_discovered",
    ]
    assert sc.state.discovery.broker_discovered is True
    assert sc.state.discovery.topic_discovered is True
    # The dump reveals the broker endpoint and both topics.
    text = "\n".join(outcome.lines)
    assert f"{BROKER}:{PORT}" in text
    assert COMMAND_TOPIC in text
    assert STATE_TOPIC in text


def test_grep_filters_to_the_matching_configuration_lines() -> None:
    sc = fresh()
    sc.extract_firmware()
    outcome = sc.analyze_firmware("command topic")
    assert outcome.success
    assert all("command topic" in line.lower() for line in outcome.lines)
    assert any(COMMAND_TOPIC in line for line in outcome.lines)


def test_grep_exposes_the_missing_authorization_finding() -> None:
    """The package's `missing-authentication` finding, modelled here as the
    firmware's own admission that commands carry no authorization."""
    sc = fresh()
    sc.extract_firmware()
    outcome = sc.analyze_firmware("authorization")
    assert outcome.success
    assert any("no per-sender authorization" in line.lower() for line in outcome.lines)


def test_grep_with_no_match_is_silent_and_nonzero() -> None:
    sc = fresh()
    sc.extract_firmware()
    outcome = sc.analyze_firmware("BME280")  # an Environmental fact
    assert outcome.success is False
    assert outcome.exit_code != EXIT_OK
    assert outcome.lines == ()


# --- 8: nmap ----------------------------------------------------------------


def test_nmap_discovers_the_authenticated_mqtt_service() -> None:
    sc = fresh()
    outcome = sc.scan(BROKER, None)
    assert outcome.success
    assert types(outcome) == ["scan"]
    assert sc.state.discovery.service_discovered is True
    text = "\n".join(outcome.lines).lower()
    assert "mqtt" in text
    # Authentication is present — the scenario must NOT report an open broker.
    assert "authentication required" in text
    assert "no authentication" not in text


def test_nmap_against_the_wrong_host_finds_nothing() -> None:
    sc = fresh()
    outcome = sc.scan("10.0.0.1", None)
    assert outcome.success is False
    assert types(outcome) == []
    assert sc.state.discovery.service_discovered is False


def test_nmap_on_a_wrong_explicit_port_reports_it_closed() -> None:
    sc = fresh()
    outcome = sc.scan(BROKER, 22)
    assert types(outcome) == []
    assert "closed" in "\n".join(outcome.lines)


# --- 9: mosquitto_sub -------------------------------------------------------


def test_mosquitto_sub_observes_the_control_traffic() -> None:
    sc = fresh()
    outcome = sc.observe(BROKER, None, COMMAND_TOPIC)
    assert outcome.success
    assert types(outcome) == ["mqtt_observed"]
    assert sc.state.discovery.mqtt_observed is True


def test_mosquitto_sub_on_the_state_topic_does_not_count_as_observed() -> None:
    sc = fresh()
    outcome = sc.observe(BROKER, None, STATE_TOPIC)
    assert outcome.success
    assert types(outcome) == []
    assert sc.state.discovery.mqtt_observed is False


def test_mosquitto_sub_against_an_unreachable_broker_fails() -> None:
    sc = fresh()
    outcome = sc.observe("10.0.0.1", None, COMMAND_TOPIC)
    assert outcome.success is False
    assert sc.state.discovery.mqtt_observed is False


# --- 10: publish before prerequisites does not complete the attack ----------


def test_publish_before_prerequisites_does_not_complete_the_attack() -> None:
    """A forged command to the right topic is obeyed (that IS the flaw), but
    the learning objective is not complete until the discovery/observation
    chain the package's success conditions require has happened."""
    sc = fresh()
    outcome = sc.publish(BROKER, None, COMMAND_TOPIC, "START")
    assert "spoof_succeeded" in types(outcome)
    assert "attack_completed" not in types(outcome)
    assert sc.state.completion.attack_successful is False


# --- 11-13: spoof attempt, success, events ----------------------------------


def test_any_publish_records_a_spoof_attempt() -> None:
    sc = fresh()
    outcome = sc.publish("10.0.0.1", None, COMMAND_TOPIC, "START")  # unreachable
    assert "spoof_attempted" in types(outcome)
    assert sc.state.attack.spoof_attempted is True


def test_publish_to_the_wrong_topic_is_rejected() -> None:
    sc = fresh()
    outcome = sc.publish(BROKER, None, "cybertrainer/smart-home/motor/other", "START")
    assert outcome.success is False
    assert "spoof_rejected" in types(outcome)
    assert "spoof_succeeded" not in types(outcome)
    assert sc.state.attack.spoof_successful is False


def test_publish_of_an_unrecognised_command_is_rejected() -> None:
    """The command topic is right, but the firmware only acts on START/STOP;
    anything else reaches the callback and is dropped."""
    sc = fresh()
    outcome = sc.publish(BROKER, None, COMMAND_TOPIC, "OPEN_GARAGE")
    assert outcome.success is False
    assert "spoof_rejected" in types(outcome)
    assert sc.state.attack.spoof_successful is False
    assert sc.state.motor.running is False


def test_a_valid_forged_command_succeeds_and_actuates() -> None:
    sc = fresh()
    outcome = sc.publish(BROKER, None, COMMAND_TOPIC, "START")
    assert outcome.success
    assert "spoof_succeeded" in types(outcome)
    assert "target_impacted" in types(outcome)
    assert sc.state.attack.spoof_successful is True
    assert sc.state.motor.running is True
    assert sc.state.attack.forged_command == "START"


def test_a_forged_stop_command_stops_the_motor() -> None:
    sc = fresh()
    sc.publish(BROKER, None, COMMAND_TOPIC, "START")
    assert sc.state.motor.running is True
    sc.publish(BROKER, None, COMMAND_TOPIC, "stop")  # case-insensitive
    assert sc.state.motor.running is False
    assert sc.state.motor.last_command == "STOP"


# --- 14: completion follows the package-defined success conditions ----------


def test_the_full_workflow_completes_the_attack() -> None:
    sc = fully_owned()
    assert sc.state.completion.attack_successful is True
    assert sc.state.stage.name == "ATTACK_SUCCESSFUL"
    assert "attack_completed" in [e.type.value for e in sc.events]


def test_completion_satisfies_every_package_success_condition() -> None:
    """The scenario's emitted events must cover the events each declared
    success condition names — the package is authoritative for these."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    sc = fully_owned()
    emitted = {e.type.value for e in sc.events}

    declared = package.evaluation.success_conditions
    assert declared, "the package must declare success conditions"
    for condition in declared:
        # required_events are canonical event-name strings on the package.
        required = set(condition.required_events)
        assert required <= emitted, (
            f"success condition {condition.condition_id!r} needs {required}, "
            f"scenario emitted {sorted(emitted)}"
        )


def test_attack_completed_is_emitted_only_once() -> None:
    sc = fully_owned()
    completions = [
        e for e in sc.events if e.type is ScenarioEventType.ATTACK_COMPLETED
    ]
    assert len(completions) == 1


def test_completion_works_when_the_chain_is_finished_out_of_order() -> None:
    """A student who forges first and analyses last still completes: the
    objective is a conjunction of state, not a fixed command order."""
    sc = fresh()
    sc.extract_firmware()
    sc.publish(BROKER, None, COMMAND_TOPIC, "START")  # succeeds, not complete
    sc.observe(BROKER, None, COMMAND_TOPIC)
    assert sc.state.completion.attack_successful is False
    outcome = sc.analyze_firmware(None)  # completes the chain here
    assert "attack_completed" in types(outcome)
    assert sc.state.completion.attack_successful is True


# --- 15: unrelated / malformed invocations do not corrupt state -------------


def test_usage_errors_do_not_mutate_state() -> None:
    sc = fresh()
    before = sc.snapshot()
    sc.scan(None, None)
    sc.observe(None, None, None)
    sc.observe(BROKER, None, None)
    sc.publish(None, None, None, None)
    sc.publish(BROKER, None, COMMAND_TOPIC, None)
    assert sc.snapshot() == before
    assert sc.events == ()


# --- 16: no Environmental Monitoring facts leak ------------------------------


def test_no_environmental_facts_appear_in_output_or_state() -> None:
    sc = fresh()
    sc.extract_firmware()
    surfaces = list(sc.analyze_firmware(None).lines)
    surfaces += [str(sc.snapshot())]
    haystack = "\n".join(surfaces).lower()
    for env_fact in (
        "bme280",
        "temperature",
        "humidity",
        "pressure",
        "sensors/bme280",
        "environmental",
    ):
        assert env_fact not in haystack, env_fact


def test_the_scenario_module_imports_no_environmental_state() -> None:
    """Structural guarantee behind the leak test: Panel 1's scenario and its
    state module never import the Environmental state module, so its facts
    cannot reach them."""
    for module in ("smart_home.py", "smart_home_state.py"):
        tree = ast.parse((APP_SCENARIOS / module).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "app.scenarios.state" not in imported, module
        assert "app.scenarios.environmental" not in imported, module


# --- 17: session isolation --------------------------------------------------


def test_two_scenarios_have_independent_state() -> None:
    a, b = fresh(), fresh()
    a.extract_firmware()
    a.publish(BROKER, None, COMMAND_TOPIC, "START")
    assert a.state.motor.running is True
    assert b.state.motor.running is False
    assert b.state.discovery.firmware_extracted is False
    assert a.state is not b.state


def test_the_registry_builds_a_fresh_instance_each_time() -> None:
    a = default_scenario_registry.create(PANEL_ONE_ID)
    b = default_scenario_registry.create(PANEL_ONE_ID)
    assert a is not b
    assert a.state is not b.state


def test_a_hack_session_over_this_scenario_records_its_id() -> None:
    session = HackSession(session_id="panel-one", scenario=fresh())
    assert isinstance(session.scenario, SmartHomeMQTTScenario)
    session.recorder.start()  # writes the session row with the scenario id


# --- 18: no dynamic execution -----------------------------------------------


@pytest.mark.parametrize("module", ["smart_home.py", "smart_home_state.py"])
def test_scenario_module_uses_no_dynamic_execution(module: str) -> None:
    tree = ast.parse((APP_SCENARIOS / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__"}, module
        if isinstance(func, ast.Attribute):
            assert func.attr not in {
                "system",
                "popen",
                "spawn",
                "spawnv",
                "import_module",
                "Popen",
                "run",
            }, module
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ), module


@pytest.mark.parametrize("module", ["smart_home.py", "smart_home_state.py"])
def test_scenario_module_imports_nothing_that_executes_or_connects(module: str) -> None:
    tree = ast.parse((APP_SCENARIOS / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = (
        "subprocess",
        "socket",
        "serial",
        "paho",
        "importlib",
        "os",
        "app.build",
        "app.hardware",
        "app.commands",
    )
    offenders = sorted(
        name
        for name in imported
        for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"{module} imports {offenders}"


# --- 19 & 20: no hardware, serial, or real MQTT/network at runtime -----------


def test_running_the_whole_scenario_touches_no_shared_hardware_state() -> None:
    """A full run is pure in-memory computation: it must not consult, mutate,
    or depend on the process-wide device monitor in any way."""
    from app.hardware import device_monitor

    before = device_monitor.snapshot()
    fully_owned()
    assert device_monitor.snapshot() is before


# --- through the real generic engine (architecture, not a private call) -----


def _dispatch(session: HackSession, line: str):
    # The router is async; the repo's convention (see tests/test_command_router.py)
    # is to drive one dispatch synchronously with asyncio.run — no event loop
    # fixture and no pytest-asyncio dependency.
    return asyncio.run(default_router.dispatch(line, CommandContext(session=session)))


def test_the_generic_router_drives_the_panel_one_scenario_unchanged() -> None:
    """The same command router, parser and handlers that drive every scenario
    drive this one — no panel-specific handler, no new command. This is the
    Phase 2C genericity claim, re-proven for Panel 1."""
    session = HackSession(session_id="router-panel-one", scenario=fresh())

    _dispatch(session, "esptool.py read_flash 0x0 0x400000 firmware.bin")
    _dispatch(session, "strings firmware.bin")
    _dispatch(session, "nmap 192.168.50.1")
    _dispatch(session, "mosquitto_sub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control")
    result = _dispatch(
        session,
        "mosquitto_pub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control -m START",
    )

    emitted = [e.type.value for e in result.events]
    assert "spoof_succeeded" in emitted
    assert "attack_completed" in emitted
    assert session.scenario.state.motor.running is True


# --- Phase 2H.1: package / scenario / firmware source coherence -------------
#
# `panel.json`, `SmartHomeMQTTScenario`/`SmartHomeState`, and the committed
# `.ino` firmware source used to disagree: the package and scenario always
# described a motor controller on `capstone/panel1/motor` with START/STOP,
# but the firmware resource was a relay/light placeholder on
# `home/livingroom/light/set` with ON/OFF — explicitly self-documented as
# "deliberately NOT the finalized vulnerable firmware". These tests pin the
# one coherent definition Phase 2H.1 aligned them to, by reading the actual
# committed `.ino` text (no compilation, no execution) rather than assuming
# it matches.


def _firmware_source() -> str:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    sketch = package.firmware_sketch_path
    assert sketch is not None, "package declares no firmware sketch"
    ino_files = list(sketch.glob("*.ino"))
    assert len(ino_files) == 1, f"expected exactly one .ino, found {ino_files}"
    return ino_files[0].read_text(encoding="utf-8")


def test_firmware_source_uses_the_packages_declared_broker_and_topics() -> None:
    source = _firmware_source()
    target = fresh().state.target
    assert f'"{target.broker_host}"' in source
    assert str(target.broker_port) in source
    assert f'"{target.command_topic}"' in source
    assert f'"{target.state_topic}"' in source


def test_firmware_source_speaks_start_stop_not_the_old_on_off_vocabulary() -> None:
    source = _firmware_source()
    assert "START" in source
    assert "STOP" in source
    # The retired relay/light placeholder's vocabulary must not reappear.
    assert "home/livingroom" not in source
    assert '"ON"' not in source
    assert '"OFF"' not in source


def test_firmware_source_authenticates_to_the_broker() -> None:
    """The vulnerability is AUTHORIZATION, not authentication (see
    `SmartHomeMQTTScenario`'s module docstring and `MotorControlTarget.
    broker_auth_required`) — so the firmware's own MQTT client must actually
    present credentials, not connect anonymously. A bare
    `mqtt.connect("some-id")` (the old placeholder's call, one argument) is
    exactly the wrong shape for this activity: it would make the broker
    itself unauthenticated, which is the interpretation this activity
    explicitly rejects.
    """
    source = _firmware_source()
    assert fresh().state.target.broker_auth_required is True
    assert re.search(r"client\.connect\([^)]*,[^)]*,[^)]*\)", source), (
        "expected client.connect(client_id, username, password) — an "
        "authenticated connection, not an anonymous one"
    )
    # Panel 1's firmware is self-contained: the real lab MQTT password is a
    # literal here (see the .ino's file header), not a placeholder rewritten
    # at compile time.
    assert 'MQTT_PASSWORD = "cybertrainer"' in source


def test_firmware_source_applies_a_command_with_no_per_sender_check() -> None:
    """The one thing that must be true for the lesson to hold: the message
    handler acts on ANY message it receives on the command topic, with no
    token/signature/sender comparison anywhere in its CODE — mirroring
    exactly what `SmartHomeMQTTScenario.publish` already simulates. Comments
    are stripped first: the file's own header prose names these words while
    explaining the vulnerability it deliberately does NOT implement, which
    must not trip a scan meant to catch the words appearing in real code.
    """
    source = _firmware_source()
    without_block_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code_only = re.sub(r"//[^\n]*", "", without_block_comments)
    forbidden_authorization_hints = ("token", "signature", "hmac", "authorized_sender")
    lowered = code_only.lower()
    assert not any(hint in lowered for hint in forbidden_authorization_hints), code_only


def test_firmware_source_matches_the_remediation_declarations_vulnerability_model() -> None:
    """`panel.json`'s own `remediation.vulnerability` text is the authoritative
    one-sentence description; this pins that it still says what the
    architecture has always intended, not the retired "open broker"
    interpretation."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    assert package.remediation is not None
    vulnerability = package.remediation.vulnerability.lower()
    assert "per-command authorization" in vulnerability
    assert "unauthenticated broker" not in vulnerability
    assert "open broker" not in vulnerability
