"""Hack Mode's scenario briefing: panel-driven, present at connect, honest.

The Hack Mode screen used to wait for the first scenario event before it
could say anything about the target, hardcoded Panel 1's objective wording and
a toolbox of Panel 1's tools in the page, and sniffed motor fields out of the
state snapshot to draw the TARGET DEVICE panel. This covers the backend half
of removing those assumptions:

A. The `hack` package block: validated, strict, and shipped for both panels.
B. `briefing_for`: a pure reshaping of a package, with three honest kinds.
C. The `session` frame (protocol v7) carries the briefing and the target's
   state, for a new session and for a resumed one.
D. A scenario with an activity target states its own TARGET DEVICE `readout`,
   gated by discovery.
E. Panel 2 says it has no activity and shows nothing that suggests one.

No hardware is touched: the device view is always a double or the process-wide
monitor in its reset state.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.commands.registry import default_registry
from app.hack_briefing import BriefingKind, briefing_for, unspecified_briefing
from app.hardware import device_monitor
from app.main import app
from app.models.messages import PROTOCOL_VERSION
from app.panels import (
    PanelPackageInvalidError,
    PanelPackageLoader,
    default_panel_package_loader,
)
from app.panels.models import HackDeclaration, HackGuideSection, HackHint
from app.scenarios import EnvironmentalMonitoringScenario, SmartHomeMQTTScenario
from app.session_panel_guard import resume_panel_matches
from app.sessions import SessionManager, session_manager
from tests.test_panel2_scenario import (
    _FixedIdentification,
    panel_two_identification,
    panel_two_selector,
)
from tests.test_panel_packages import valid_manifest, write_package
from tests.test_session_scenario_wiring import panel_one_identification, selector_over

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_TWO_ID = "environmental-monitoring"

#: Vocabulary of an attack activity. Nothing a Panel 2 student can see in Hack
#: Mode may carry it: that panel defines no cybersecurity activity.
ATTACK_VOCABULARY = re.compile(
    r"mqtt|nmap|mosquitto|esptool|spoof|attack|exploit|broker|authoriz|forged|"
    r"192\.168|cybertrainer|firmware\.bin",
    re.IGNORECASE,
)

#: The inline-code convention hints and guide paragraphs use for a command.
INLINE_CODE = re.compile(r"`([^`]+)`")


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_board():
    device_monitor.reset()
    yield
    device_monitor.reset()


def _text_of(briefing: dict) -> str:
    """Every string a student could read in a briefing, as one blob."""
    return json.dumps(briefing)


# =============================================================================
# A. the `hack` package block
# =============================================================================


def test_both_shipped_packages_declare_a_hack_block() -> None:
    loader = default_panel_package_loader()
    for package_id in (PANEL_ONE_ID, PANEL_TWO_ID):
        package = loader.load(package_id)
        assert isinstance(package.hack, HackDeclaration), package_id
        assert package.hack.guide, package_id


def test_panel_one_hints_are_ordered_and_tied_to_real_objectives() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    objective_ids = {o.objective_id for o in package.evaluation.objectives}
    linked = [h.objective_id for h in package.hack.hints if h.objective_id]

    assert package.hack.hints
    assert set(linked) <= objective_ids
    # Every measurable objective gets at least one hint that helps with it.
    assert set(linked) == objective_ids


def test_every_command_a_hint_names_is_a_real_registered_tool() -> None:
    """A hint can say `mosquitto_pub ...` only if the engine has that tool."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    registered = set(default_registry.names())
    texts = [h.text for h in package.hack.hints] + [
        p for s in package.hack.guide for p in s.paragraphs
    ]
    named = [span.split()[0] for text in texts for span in INLINE_CODE.findall(text)]

    assert named, "expected the hints to show at least one command"
    assert set(named) <= registered, sorted(set(named) - registered)


def test_panel_one_text_states_no_target_fact() -> None:
    """The broker, topics and credentials are for the student to DISCOVER. The
    static briefing must not hand them over."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    criterion = package.remediation.criterion
    blob = _text_of(briefing_for(package))

    for secret in (
        criterion.broker_host,
        criterion.control_topic,
        criterion.state_topic,
        "cybertrainer/smart-home",
        criterion.authorized.username,
        criterion.unauthorized.username,
        "CyberTrainer2026",
        str(criterion.broker_port),
    ):
        assert secret not in blob, secret


def _write(tmp_path: pathlib.Path, name: str, hack: object, *, objectives: bool = True) -> None:
    manifest = valid_manifest(name)
    if objectives:
        manifest["evaluation"]["objectives"] = [
            {"objective_id": "an-objective", "description": "Do it.", "required_events": ["scan"]}
        ]
    manifest["hack"] = hack
    write_package(tmp_path, name, manifest)


def test_a_well_formed_hack_block_loads(tmp_path: pathlib.Path) -> None:
    _write(
        tmp_path,
        "good-hack",
        {
            "guide": [{"heading": "Intro", "paragraphs": ["One.", "Two."]}],
            "hints": [
                {"hint_id": "first", "text": "Try `nmap`.", "objective_id": "an-objective"},
                {"hint_id": "second", "text": "Stuck? Read the guide."},
            ],
        },
    )
    package = PanelPackageLoader(root=tmp_path).load("good-hack")

    assert package.hack.guide == (HackGuideSection("Intro", ("One.", "Two.")),)
    assert package.hack.hints == (
        HackHint("first", "Try `nmap`.", "an-objective"),
        HackHint("second", "Stuck? Read the guide."),
    )


def test_a_package_without_a_hack_block_still_loads(tmp_path: pathlib.Path) -> None:
    write_package(tmp_path, "no-hack", valid_manifest("no-hack"))
    assert PanelPackageLoader(root=tmp_path).load("no-hack").hack is None


@pytest.mark.parametrize(
    "hack, message",
    [
        ({}, "neither a guide nor hints"),
        ("a string", "must be a JSON object"),
        ({"guide": [], "extra": 1}, "unknown field"),
        ({"guide": [{"heading": "H", "paragraphs": ["p"], "x": 1}]}, "unknown field"),
        ({"guide": [{"heading": "H", "paragraphs": []}]}, "no paragraphs"),
        ({"guide": [{"heading": " ", "paragraphs": ["p"]}]}, "non-empty"),
        ({"guide": [{"paragraphs": ["p"]}]}, "missing required field"),
        ({"hints": [{"hint_id": "a", "text": "t", "command": "nmap"}]}, "unknown field"),
        ({"hints": [{"hint_id": "Not Valid", "text": "t"}]}, "identifier"),
        ({"hints": [{"hint_id": "a", "text": ""}]}, "non-empty"),
        (
            {"hints": [{"hint_id": "a", "text": "t"}, {"hint_id": "a", "text": "u"}]},
            "duplicate hack hint id",
        ),
    ],
)
def test_a_malformed_hack_block_is_rejected(
    tmp_path: pathlib.Path, hack: object, message: str
) -> None:
    _write(tmp_path, "bad-hack", hack)
    with pytest.raises(PanelPackageInvalidError, match=message):
        PanelPackageLoader(root=tmp_path).load("bad-hack")


def test_a_hint_naming_an_unknown_objective_is_rejected(tmp_path: pathlib.Path) -> None:
    """A dangling reference would be a hint that can never be marked done."""
    _write(
        tmp_path,
        "dangling",
        {"hints": [{"hint_id": "a", "text": "t", "objective_id": "no-such-objective"}]},
    )
    with pytest.raises(PanelPackageInvalidError, match="unknown objective"):
        PanelPackageLoader(root=tmp_path).load("dangling")


# =============================================================================
# B. `briefing_for`
# =============================================================================


def test_no_package_gives_the_unspecified_briefing() -> None:
    briefing = briefing_for(None)

    assert briefing == unspecified_briefing()
    assert briefing["kind"] == BriefingKind.UNSPECIFIED.value
    assert briefing["title"] is None and briefing["scenario_id"] is None
    assert briefing["objectives"] == briefing["hints"] == briefing["guide"] == []


def test_panel_one_briefing_is_an_activity_read_straight_off_its_package() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    briefing = briefing_for(package)

    assert briefing["kind"] == "activity"
    assert briefing["panel_id"] == PANEL_ONE_ID
    # The package's own value, never a legacy id.
    assert briefing["scenario_id"] == package.scenario_id == PANEL_ONE_ID
    assert briefing["title"] == "Smart Home MQTT Control System"
    assert [o["id"] for o in briefing["objectives"]] == [
        o.objective_id for o in package.evaluation.objectives
    ]
    assert [o["label"] for o in briefing["objectives"]] == [
        o.description for o in package.evaluation.objectives
    ]
    assert [o["required_events"] for o in briefing["objectives"]] == [
        list(o.required_events) for o in package.evaluation.objectives
    ]
    assert briefing["outcomes"] == list(package.learning.objectives)
    assert [h["id"] for h in briefing["hints"]] == [h.hint_id for h in package.hack.hints]
    assert [s["heading"] for s in briefing["guide"]] == [s.heading for s in package.hack.guide]
    json.dumps(briefing)  # plain data: serialisable as is


def test_the_hack_guide_is_not_the_build_remediation_brief() -> None:
    """Hack Mode's guide is its own content, not Build Mode's remediation text."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    briefing = briefing_for(package)
    blob = _text_of(briefing)

    for build_only in (
        package.remediation.vulnerability,
        package.remediation.remediation_goal,
        package.remediation.validation_requirement,
    ):
        assert build_only not in blob
    assert "applyCommand" not in blob  # the Build Mode security region


def test_a_package_declaring_no_objectives_is_a_foundation() -> None:
    package = default_panel_package_loader().load(PANEL_TWO_ID)
    briefing = briefing_for(package)

    assert briefing["kind"] == "foundation"
    assert briefing["panel_id"] == PANEL_TWO_ID
    assert briefing["scenario_id"] == "environmental-sensing"
    assert briefing["objectives"] == []
    assert briefing["hints"] == []
    assert briefing["outcomes"] == []


def test_a_package_without_a_hack_block_briefs_with_an_empty_guide(
    tmp_path: pathlib.Path,
) -> None:
    write_package(tmp_path, "plain", valid_manifest("plain"))
    briefing = briefing_for(PanelPackageLoader(root=tmp_path).load("plain"))

    assert briefing["guide"] == [] and briefing["hints"] == []
    assert briefing["title"] == "Test Panel Scenario"


def test_the_briefing_module_is_pure() -> None:
    """A function of a package: no process, shell, network or file facility."""
    tree = ast.parse((APP_DIR / "hack_briefing.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"os", "subprocess", "socket", "pathlib", "shutil", "asyncio", "json", "app"}
    # `app` appears only under TYPE_CHECKING (the package type); nothing here
    # is imported at run time from another application layer.
    runtime_app = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app")
    ]
    assert not (imported & (forbidden - {"app"})), imported
    assert runtime_app == []


# =============================================================================
# C. the `session` frame
# =============================================================================


def test_the_protocol_version_was_bumped_for_the_session_frame_addition() -> None:
    assert PROTOCOL_VERSION == 7


def test_a_session_with_no_package_says_so_in_its_first_frame(
    client: TestClient, no_board
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        frame = ws.receive_json()

    assert frame["type"] == "session"
    assert frame["protocol_version"] == PROTOCOL_VERSION
    assert frame["scenario"] == unspecified_briefing()
    # The state is still the scenario's own snapshot, present before any command.
    assert frame["state"] == EnvironmentalMonitoringScenario().snapshot()


def test_panel_one_first_frame_carries_its_briefing_and_initial_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.websocket.select_session_scenario", selector_over(panel_one_identification()).select
    )
    package = default_panel_package_loader().load(PANEL_ONE_ID)

    with client.websocket_connect("/ws/hack") as ws:
        frame = ws.receive_json()
        banner = ws.receive_json()
        session = session_manager._sessions[frame["session_id"]]

        assert frame["resumed"] is False
        assert frame["scenario"] == briefing_for(package)
        assert frame["scenario"]["kind"] == "activity"
        # No event has happened and none was needed: the page has it all now.
        assert frame["state"] == session.scenario.snapshot()
        assert session.recorder.events == ()
        assert banner["type"] == "output"
        # Sending the briefing recorded nothing and emitted nothing.
        assert session.recorder.commands == ()


def test_panel_two_first_frame_is_a_foundation_with_a_neutral_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.websocket.select_session_scenario", panel_two_selector().select)

    with client.websocket_connect("/ws/hack") as ws:
        frame = ws.receive_json()

    assert frame["scenario"]["kind"] == "foundation"
    assert frame["scenario"]["objectives"] == []
    assert frame["scenario"]["hints"] == []
    assert frame["state"]["foundation"] is True
    assert "readout" not in frame["state"]


def test_a_resumed_session_is_told_the_same_briefing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.websocket.select_session_scenario", selector_over(panel_one_identification()).select
    )
    monkeypatch.setattr(
        "app.websocket.resume_panel_matches",
        lambda expected: resume_panel_matches(
            expected, _FixedIdentification(panel_one_identification())
        ),
    )

    with client.websocket_connect("/ws/hack") as ws:
        first = ws.receive_json()
        ws.receive_json()  # banner

    with client.websocket_connect(f"/ws/hack?session={first['session_id']}") as ws:
        again = ws.receive_json()

    assert again["resumed"] is True
    assert again["session_id"] == first["session_id"]
    assert again["scenario"] == first["scenario"]
    assert again["state"] == first["state"]


def test_a_new_session_after_an_ended_one_gets_a_fresh_briefing_and_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RESET ends the session and opens a new one: nothing carries over."""
    monkeypatch.setattr(
        "app.websocket.select_session_scenario", selector_over(panel_one_identification()).select
    )
    with client.websocket_connect("/ws/hack") as ws:
        first = ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "input", "data": "esptool.py read_flash 0x10000 0x10000 firmware.bin\r"})
        while ws.receive_json()["type"] != "state":
            pass
    session_manager.end(first["session_id"])

    with client.websocket_connect(f"/ws/hack?session={first['session_id']}") as ws:
        second = ws.receive_json()

    assert second["session_id"] != first["session_id"]
    assert second["resumed"] is False
    assert second["scenario"] == first["scenario"]
    assert all(row["revealed"] is (row["id"] == "status") for row in second["state"]["readout"])


def test_the_manager_keeps_a_sessions_briefing_until_it_ends() -> None:
    import asyncio

    async def run() -> None:
        manager = SessionManager()
        briefing = briefing_for(None)
        session = await manager.create(briefing=briefing)
        assert manager.briefing_of(session.session_id) is briefing
        manager.end(session.session_id)
        assert manager.briefing_of(session.session_id) is None
        bare = await manager.create()
        assert manager.briefing_of(bare.session_id) is None

    asyncio.run(run())


# =============================================================================
# D. the scenario's own TARGET DEVICE readout
# =============================================================================


def _by_id(snapshot: dict) -> dict:
    return {row["id"]: row for row in snapshot["readout"]}


def test_panel_one_readout_reveals_only_what_has_been_discovered() -> None:
    scenario = SmartHomeMQTTScenario()

    rows = _by_id(scenario.snapshot())
    assert list(rows) == ["status", "broker", "command_topic", "motor"]
    assert rows["status"]["revealed"] is True and rows["status"]["value"]
    for hidden in ("broker", "command_topic", "motor"):
        assert rows[hidden] == {"id": hidden, "label": rows[hidden]["label"], "value": None, "revealed": False}

    scenario.extract_firmware()
    scenario.analyze_firmware(None)
    rows = _by_id(scenario.snapshot())
    state = scenario.snapshot()
    assert rows["broker"]["revealed"] is True
    assert rows["broker"]["value"] == (
        f"{state['target']['broker_host']}:{state['target']['broker_port']}"
    )
    assert rows["command_topic"]["revealed"] is True
    assert rows["command_topic"]["value"] == state["target"]["command_topic"]
    assert rows["motor"]["revealed"] is False  # not observed yet


def test_panel_one_readout_names_the_motor_only_once_traffic_is_observed() -> None:
    scenario = SmartHomeMQTTScenario()
    scenario.extract_firmware()
    scenario.analyze_firmware(None)
    snapshot = scenario.snapshot()
    host, port = snapshot["target"]["broker_host"], snapshot["target"]["broker_port"]
    scenario.observe(host, port, snapshot["target"]["command_topic"])

    motor = _by_id(scenario.snapshot())["motor"]
    assert motor["revealed"] is True
    assert motor["value"] in {"RUNNING", "STOPPED"}


def test_the_legacy_target_states_its_own_rows_too() -> None:
    scenario = EnvironmentalMonitoringScenario()
    rows = _by_id(scenario.snapshot())

    assert list(rows) == ["status", "broker", "topic", "telemetry"]
    assert [rows[k]["revealed"] for k in rows] == [True, False, False, False]
    assert all(rows[k]["value"] is None for k in ("broker", "topic", "telemetry"))


def test_a_foundation_scenario_has_no_readout() -> None:
    from app.scenarios import EnvironmentalSensingScenario

    assert "readout" not in EnvironmentalSensingScenario().snapshot()


# =============================================================================
# E. Panel 2 says it has no activity and suggests none
# =============================================================================


def test_nothing_panel_two_shows_in_hack_mode_suggests_an_attack() -> None:
    package = default_panel_package_loader().load(PANEL_TWO_ID)
    briefing = briefing_for(package)
    blob = _text_of(briefing)

    assert not ATTACK_VOCABULARY.search(blob), ATTACK_VOCABULARY.search(blob).group(0)
    # ...and it does say, in words, that nothing is defined.
    assert "No cybersecurity training activity is defined" in blob


def test_panel_two_describes_the_real_system() -> None:
    briefing = briefing_for(default_panel_package_loader().load(PANEL_TWO_ID))
    blob = _text_of(briefing)

    for fact in ("DHT11", "SSD1306", "31.0 C", "L293D", "USB serial", "115200"):
        assert fact in blob, fact
