"""Phase 2C verification: the Environmental Monitoring scenario engine.

Exercises the scenario through the real command router and CommandContext
(the way the WebSocket layer drives it), plus a few direct engine assertions
for state and events. Covers the deterministic happy path, every gate, the
invalid/out-of-order paths, per-session isolation, and the standing
no-execution security boundary.
"""

from __future__ import annotations

import asyncio
import pathlib
import tokenize

import pytest

from app.commands import (
    CommandContext,
    CommandResult,
    CommandRouter,
    build_default_registry,
)
from app.scenarios import (
    EnvironmentalMonitoringScenario,
    ScenarioEventType,
    ScenarioStage,
    create_default_scenario,
)
from app.sessions import HackSession

# Canonical target facts, read from a fresh scenario so the tests and the
# engine cannot silently disagree about them.
_T = EnvironmentalMonitoringScenario().state.target
TARGET_IP = _T.ip_address
TARGET_PORT = _T.mqtt_port
TARGET_TOPIC = _T.mqtt_topic

SCENARIOS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "scenarios"


class Terminal:
    """A session + router, driving the scenario as the WebSocket layer does."""

    def __init__(self) -> None:
        self.session = HackSession(session_id="scenario-test")
        self._router = CommandRouter(build_default_registry())

    def run(self, line: str) -> CommandResult:
        return asyncio.run(
            self._router.dispatch(line, CommandContext(session=self.session))
        )

    @property
    def scenario(self):
        return self.session.scenario

    @property
    def snapshot(self) -> dict:
        return self.scenario.snapshot()

    def event_types(self) -> list[str]:
        return [event.type.value for event in self.scenario.events]

    # The canonical progression, so tests can reach a stage without repeating
    # the exact command strings everywhere.
    def extract(self) -> CommandResult:
        return self.run("firmware-extract")

    def analyze(self) -> CommandResult:
        return self.run("firmware-analyze")

    def scan(self) -> CommandResult:
        return self.run(f"nmap -p {TARGET_PORT} {TARGET_IP}")

    def observe(self) -> CommandResult:
        return self.run(f"mosquitto_sub -h {TARGET_IP} -t {TARGET_TOPIC}")

    def spoof(self, temperature: int = 150) -> CommandResult:
        return self.run(
            f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m temperature={temperature}"
        )


@pytest.fixture
def term() -> Terminal:
    return Terminal()


def joined(result: CommandResult) -> str:
    return "\n".join(result.lines)


# --- 1: fresh session starts with deterministic target state --------------


def test_fresh_session_deterministic_initial_state(term: Terminal) -> None:
    snap = term.snapshot
    assert snap["environment"] == {"temperature": 28, "humidity": 65, "pressure": 1008}
    assert snap["target"] == {
        "ip_address": TARGET_IP,
        "mqtt_port": TARGET_PORT,
        "mqtt_topic": TARGET_TOPIC,
        "device_status": "online",
    }
    assert snap["stage"] == ScenarioStage.INITIAL
    assert not any(term.snapshot["discovery"].values())
    assert term.snapshot["completion"]["attack_successful"] is False
    assert term.scenario.events == ()


def test_two_fresh_scenarios_are_identical() -> None:
    assert create_default_scenario().snapshot() == create_default_scenario().snapshot()


# --- 2 & 18: two sessions have completely isolated scenario state ---------


def test_sessions_are_isolated(term: Terminal) -> None:
    other = Terminal()

    # Drive the first session all the way to a successful attack.
    term.extract()
    term.analyze()
    term.observe()
    term.spoof(150)
    assert term.snapshot["completion"]["attack_successful"] is True
    assert term.snapshot["environment"]["temperature"] == 150

    # The second session is entirely untouched.
    assert other.snapshot["environment"]["temperature"] == 28
    assert other.snapshot["completion"]["attack_successful"] is False
    assert not any(other.snapshot["discovery"].values())
    assert other.scenario.events == ()


def test_isolation_holds_after_interleaved_commands() -> None:
    a, b = Terminal(), Terminal()
    a.extract()
    b.run("help")
    a.analyze()
    b.extract()
    a.observe()
    # a has analyzed; b has only extracted.
    assert a.snapshot["discovery"]["firmware_analyzed"] is True
    assert b.snapshot["discovery"]["firmware_analyzed"] is False
    assert b.snapshot["discovery"]["firmware_extracted"] is True
    assert a.snapshot["discovery"]["mqtt_observed"] is True
    assert b.snapshot["discovery"]["mqtt_observed"] is False


# --- 3: firmware extraction changes the correct state ---------------------


def test_firmware_extract_sets_only_extraction_flag(term: Terminal) -> None:
    result = term.extract()
    assert result.exit_code == 0
    discovery = term.snapshot["discovery"]
    assert discovery["firmware_extracted"] is True
    assert discovery["firmware_analyzed"] is False
    assert discovery["broker_discovered"] is False
    assert discovery["topic_discovered"] is False
    assert ScenarioEventType.FIRMWARE_EXTRACTED.value in term.event_types()
    assert term.snapshot["stage"] == ScenarioStage.FIRMWARE_EXTRACTED


# --- 4: analysis exposes MQTT info only at the appropriate stage ----------


def test_analysis_requires_extraction_first(term: Terminal) -> None:
    result = term.analyze()
    assert result.exit_code != 0
    assert "firmware-extract" in joined(result)
    # No configuration leaked, no state advanced.
    assert TARGET_TOPIC not in joined(result)
    assert term.snapshot["discovery"]["firmware_analyzed"] is False
    assert term.snapshot["discovery"]["topic_discovered"] is False
    assert term.scenario.events == ()


def test_analysis_reveals_config_and_sets_discovery(term: Terminal) -> None:
    term.extract()
    result = term.analyze()
    assert result.exit_code == 0
    text = joined(result)
    assert TARGET_IP in text
    assert str(TARGET_PORT) in text
    assert TARGET_TOPIC in text
    discovery = term.snapshot["discovery"]
    assert discovery["firmware_analyzed"] is True
    assert discovery["broker_discovered"] is True
    assert discovery["topic_discovered"] is True
    for expected in (
        ScenarioEventType.FIRMWARE_ANALYZED,
        ScenarioEventType.BROKER_DISCOVERED,
        ScenarioEventType.TOPIC_DISCOVERED,
    ):
        assert expected.value in term.event_types()


def test_topic_not_revealed_before_analysis(term: Terminal) -> None:
    """The topic must be earned by analysis, not handed out by other tools."""
    term.extract()
    # nmap confirms the service but never names the topic.
    assert TARGET_TOPIC not in joined(term.scan())
    # mqtt-explorer connects but will not enumerate the topic pre-analysis.
    explorer = term.run(f"mqtt-explorer -h {TARGET_IP}")
    assert TARGET_TOPIC not in joined(explorer)
    assert term.snapshot["discovery"]["topic_discovered"] is False


# --- 5 & 6: broker and topic discovery -----------------------------------


def test_broker_and_topic_discovered_through_analysis(term: Terminal) -> None:
    term.extract()
    term.analyze()
    assert term.snapshot["discovery"]["broker_discovered"] is True
    assert term.snapshot["discovery"]["topic_discovered"] is True


# --- 7 & 8: nmap against correct vs incorrect target/port -----------------


def test_scan_of_correct_target_finds_mqtt(term: Terminal) -> None:
    result = term.scan()
    text = joined(result)
    assert f"{TARGET_PORT}/tcp" in text
    assert "open" in text
    assert "mqtt" in text.lower()
    assert result.exit_code == 0
    assert ScenarioEventType.MQTT_SERVICE_SCANNED.value in term.event_types()


def test_scan_of_wrong_host_finds_nothing(term: Terminal) -> None:
    result = term.run("nmap 10.20.30.40")
    assert result.exit_code != 0
    assert "open" not in joined(result)
    assert ScenarioEventType.MQTT_SERVICE_SCANNED.value not in term.event_types()


def test_scan_of_wrong_port_is_closed(term: Terminal) -> None:
    result = term.run(f"nmap -p 22 {TARGET_IP}")
    text = joined(result)
    assert "closed" in text
    assert "1883/tcp open" not in text
    assert ScenarioEventType.MQTT_SERVICE_SCANNED.value not in term.event_types()


def test_scan_requires_a_target(term: Terminal) -> None:
    result = term.run("nmap")
    assert result.exit_code != 0
    assert "nmap" in joined(result)


# --- 9: observation exposes legitimate environmental data -----------------


def test_observe_correct_topic_yields_telemetry(term: Terminal) -> None:
    result = term.observe()
    text = joined(result)
    assert "temperature" in text
    assert "28" in text
    assert "65" in text
    assert "1008" in text
    assert term.snapshot["discovery"]["mqtt_observed"] is True
    assert ScenarioEventType.MQTT_OBSERVED.value in term.event_types()


def test_observe_wrong_topic_yields_nothing(term: Terminal) -> None:
    result = term.run(f"mosquitto_sub -h {TARGET_IP} -t bogus/topic")
    assert result.exit_code != 0
    assert term.snapshot["discovery"]["mqtt_observed"] is False
    assert ScenarioEventType.MQTT_OBSERVED.value not in term.event_types()


def test_observe_wrong_host_refuses_connection(term: Terminal) -> None:
    result = term.run(f"mosquitto_sub -h 10.0.0.9 -t {TARGET_TOPIC}")
    assert result.exit_code != 0
    assert "refused" in joined(result).lower()
    assert term.snapshot["discovery"]["mqtt_observed"] is False


def test_observe_wrong_port_refuses_connection(term: Terminal) -> None:
    result = term.run(f"mosquitto_sub -h {TARGET_IP} -p 1884 -t {TARGET_TOPIC}")
    assert result.exit_code != 0
    assert term.snapshot["discovery"]["mqtt_observed"] is False


# --- 10 & 13: publish to the correct topic triggers the vulnerability -----


def test_spoof_to_correct_topic_changes_temperature(term: Terminal) -> None:
    result = term.spoof(150)
    assert result.exit_code == 0
    assert term.snapshot["environment"]["temperature"] == 150
    attack = term.snapshot["attack"]
    assert attack["spoof_attempted"] is True
    assert attack["spoof_successful"] is True
    assert attack["spoof_active"] is True
    assert attack["spoofed_temperature"] == 150
    for expected in (
        ScenarioEventType.SPOOF_ATTEMPTED,
        ScenarioEventType.SPOOF_SUCCEEDED,
        ScenarioEventType.TARGET_IMPACTED,
    ):
        assert expected.value in term.event_types()


def test_spoof_accepts_json_payload(term: Terminal) -> None:
    result = term.run(
        f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m '{{\"temperature\": 99}}'"
    )
    assert result.exit_code == 0
    assert term.snapshot["environment"]["temperature"] == 99


# --- 11: incorrect topic does not trigger the vulnerability ---------------


def test_spoof_to_wrong_topic_has_no_effect(term: Terminal) -> None:
    result = term.run(
        f"mosquitto_pub -h {TARGET_IP} -t attacker/topic -m temperature=150"
    )
    assert result.exit_code != 0
    assert term.snapshot["environment"]["temperature"] == 28
    assert term.snapshot["attack"]["spoof_successful"] is False
    # It was still recorded as an attempt.
    assert term.snapshot["attack"]["spoof_attempted"] is True
    assert ScenarioEventType.SPOOF_SUCCEEDED.value not in term.event_types()


def test_spoof_to_wrong_host_is_refused(term: Terminal) -> None:
    result = term.run(
        f"mosquitto_pub -h 10.0.0.9 -t {TARGET_TOPIC} -m temperature=150"
    )
    assert result.exit_code != 0
    assert term.snapshot["environment"]["temperature"] == 28
    assert term.snapshot["attack"]["spoof_successful"] is False


# --- 12: invalid payload does not mutate state ----------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "humidity=42",  # right shape, wrong field
        "'{\"temperature\": \"hot\"}'",  # non-numeric temperature
        "'{\"temperature\": true}'",  # boolean is not a reading
        "'{\"humidity\": 10}'",  # no temperature field
        "notapayload",  # unparseable
        "'{not json}'",  # malformed json
    ],
)
def test_invalid_payload_does_not_change_temperature(
    term: Terminal, payload: str
) -> None:
    result = term.run(f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m {payload}")
    assert result.exit_code != 0
    assert term.snapshot["environment"]["temperature"] == 28
    assert term.snapshot["attack"]["spoof_successful"] is False
    assert ScenarioEventType.SPOOF_SUCCEEDED.value not in term.event_types()


def test_publish_requires_a_payload(term: Terminal) -> None:
    result = term.run(f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC}")
    assert result.exit_code != 0
    assert term.snapshot["attack"]["spoof_attempted"] is False


# --- 14: unrelated environmental values remain unchanged ------------------


def test_spoof_leaves_humidity_and_pressure_unchanged(term: Terminal) -> None:
    term.spoof(150)
    assert term.snapshot["environment"]["humidity"] == 65
    assert term.snapshot["environment"]["pressure"] == 1008


# --- 15: attack success requires the complete chain -----------------------


def test_attack_success_requires_full_chain(term: Terminal) -> None:
    term.extract()
    term.analyze()
    term.observe()
    term.spoof(150)
    assert term.snapshot["completion"]["attack_successful"] is True
    assert term.snapshot["stage"] == ScenarioStage.ATTACK_SUCCESSFUL
    assert ScenarioEventType.ATTACK_COMPLETED.value in term.event_types()


def test_spoof_without_observation_is_not_full_success(term: Terminal) -> None:
    """A spoof can succeed technically while the objective stays incomplete."""
    term.extract()
    term.analyze()
    # Skip observation entirely.
    term.spoof(150)
    assert term.snapshot["attack"]["spoof_successful"] is True
    assert term.snapshot["environment"]["temperature"] == 150
    assert term.snapshot["completion"]["attack_successful"] is False
    assert ScenarioEventType.ATTACK_COMPLETED.value not in term.event_types()


def test_spoof_without_analysis_is_not_full_success(term: Terminal) -> None:
    """Guessing the topic can flip the value but not complete the objective."""
    term.spoof(150)
    assert term.snapshot["attack"]["spoof_successful"] is True
    assert term.snapshot["completion"]["attack_successful"] is False


# --- 16: repeated valid commands do not corrupt state ---------------------


def test_repeated_commands_are_stable(term: Terminal) -> None:
    term.extract()
    term.extract()
    term.analyze()
    term.analyze()
    term.observe()
    term.observe()
    term.spoof(150)
    term.spoof(150)
    snap = term.snapshot
    assert snap["completion"]["attack_successful"] is True
    assert snap["environment"]["temperature"] == 150
    assert snap["environment"]["humidity"] == 65
    # Each discovery event is emitted once, not once per repeat.
    types = term.event_types()
    assert types.count(ScenarioEventType.FIRMWARE_EXTRACTED.value) == 1
    assert types.count(ScenarioEventType.MQTT_OBSERVED.value) == 1
    assert types.count(ScenarioEventType.ATTACK_COMPLETED.value) == 1


def test_re_spoof_updates_the_reported_value(term: Terminal) -> None:
    term.extract()
    term.analyze()
    term.observe()
    term.spoof(150)
    term.spoof(200)
    assert term.snapshot["environment"]["temperature"] == 200
    assert term.snapshot["attack"]["spoofed_temperature"] == 200
    assert term.snapshot["completion"]["attack_successful"] is True


# --- 17: out-of-order commands are handled deterministically --------------


def test_out_of_order_reverse_sequence(term: Terminal) -> None:
    # Attempt everything backwards; nothing should crash or half-succeed.
    r_spoof = term.spoof(150)  # guesses topic; flips value, not objective
    r_obs = term.observe()  # observes the (now spoofed) telemetry
    r_ana = term.analyze()  # fails: no firmware yet
    r_ext = term.extract()
    r_ana2 = term.analyze()  # now works

    assert r_spoof.exit_code == 0
    assert r_obs.exit_code == 0
    assert r_ana.exit_code != 0
    assert r_ext.exit_code == 0
    assert r_ana2.exit_code == 0
    # Reaching every prerequisite, even out of order, completes the objective.
    assert term.snapshot["completion"]["attack_successful"] is True


def test_a_full_run_reaches_success_regardless_of_scan(term: Terminal) -> None:
    """nmap is a recon aid, not a gate on the objective."""
    term.extract()
    term.analyze()
    term.observe()
    term.spoof(150)
    assert term.snapshot["completion"]["attack_successful"] is True


# --- mqtt-explorer as an alternate observation path -----------------------


def test_mqtt_explorer_after_analysis_observes(term: Terminal) -> None:
    term.extract()
    term.analyze()
    result = term.run(f"mqtt-explorer -h {TARGET_IP}")
    assert result.exit_code == 0
    assert TARGET_TOPIC in joined(result)
    assert term.snapshot["discovery"]["mqtt_observed"] is True


def test_mqtt_explorer_wrong_host_fails(term: Terminal) -> None:
    result = term.run("mqtt-explorer -h 10.9.9.9")
    assert result.exit_code != 0
    assert term.snapshot["discovery"]["mqtt_observed"] is False


# --- 19 & 20: no execution primitives in the scenario layer ---------------

FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "os",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "eval",
        "exec",
        "compile",
        "shell",
        "socket",
        "importlib",
        "__import__",
    }
)

# The scenario must stay independent of the transport and of the web layer.
FORBIDDEN_IMPORTS = frozenset(
    {"fastapi", "starlette", "websockets", "app.websocket", "app.commands"}
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def _scenario_modules() -> list[pathlib.Path]:
    paths = sorted(SCENARIOS_DIR.rglob("*.py"))
    assert paths, "no scenario modules found"
    return paths


def test_scenario_layer_has_no_execution_primitives() -> None:
    offenders = [
        f"{path.relative_to(SCENARIOS_DIR)}: {name}"
        for path in _scenario_modules()
        for name in sorted(_code_names(path) & FORBIDDEN_NAMES)
    ]
    assert offenders == [], f"execution primitive in scenario layer: {offenders}"


def test_scenario_layer_does_not_import_transport_or_commands() -> None:
    offenders = []
    for path in _scenario_modules():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            offenders.extend(
                f"{path.name}: {stripped}"
                for forbidden in FORBIDDEN_IMPORTS
                if forbidden in stripped
            )
    assert offenders == [], f"forbidden import in scenario layer: {offenders}"


def test_hostile_publish_payload_stays_inert(term: Terminal) -> None:
    """A payload full of shell/code text is just rejected data, never run."""
    term.extract()
    term.analyze()
    term.observe()
    result = term.run(
        f"mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} "
        "-m 'temperature=__import__(1)'"
    )
    assert result.exit_code != 0
    assert term.snapshot["environment"]["temperature"] == 28
    assert term.snapshot["completion"]["attack_successful"] is False


# --- events feed is structured, not scraped from text ---------------------


def test_events_are_structured_and_carry_data(term: Terminal) -> None:
    term.extract()
    term.analyze()
    broker_events = [
        e for e in term.scenario.events if e.type is ScenarioEventType.BROKER_DISCOVERED
    ]
    assert len(broker_events) == 1
    assert broker_events[0].data["ip_address"] == TARGET_IP
    assert broker_events[0].data["mqtt_port"] == TARGET_PORT
