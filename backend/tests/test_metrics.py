"""Phase 2E.2 — generic Hack Mode performance metric computation.

Covers the four Hack Mode metrics from the final capstone manuscript
(Section 3.10.1): Attack Completion Rate (ACR), Reconnaissance Efficiency
(RE), Exploitation Attempt Count (EAC), and Time-to-Exploitation (TTE).

Two kinds of coverage:

1. UNIT tests against small, synthetic `PanelPackage`/record fixtures, which
   pin the exact formula semantics (0/4..4/4, N/A vs 0%, cross-session
   isolation, phase boundaries) precisely and independently of any one
   panel's real content.
2. An INTEGRATION test that drives the real command router against a real
   `HackSession` running the real, shipped `smart-home-mqtt-control`
   package, to prove the metric layer actually works end to end against
   what Phase 2E.1 already built — not just against fixtures shaped to fit.

Every metric function is exercised only through its public signature
(`compute_acr`, `compute_re`, `compute_eac`, `compute_tte`); nothing here
reaches into scenario internals or panel-specific behaviour, matching the
"generic metric layer" architecture rule this phase was built under.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.commands import CommandContext, default_router
from app.events.records import HackCommandRecord, HackEventRecord, HackSessionRecord
from app.metrics import MetricStatus, compute_acr, compute_eac, compute_re, compute_tte
from app.panels import default_panel_package_loader
from app.panels.models import (
    EvaluationDeclaration,
    ObjectiveDeclaration,
    PanelPackage,
    ScenarioDefinition,
    WorkflowPhase,
    WorkflowStep,
)
from app.sessions import HackSession

METRICS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "metrics"

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def _event(session_id: str, sequence: int, event_type: str, at: datetime = T0) -> HackEventRecord:
    return HackEventRecord.create(
        session_id=session_id, sequence=sequence, event_type=event_type, message="", occurred_at=at
    )


def _command(
    session_id: str,
    sequence: int,
    name: str | None,
    *,
    exit_code: int = 0,
    handled: bool = True,
    fields_correct: bool | None = None,
    at: datetime = T0,
) -> HackCommandRecord:
    return HackCommandRecord(
        session_id=session_id,
        sequence=sequence,
        name=name,
        argv=(),
        exit_code=exit_code,
        handled=handled,
        occurred_at=at,
        fields_correct=fields_correct,
    )


def _session(
    session_id: str,
    *,
    scenario_id: str = "test-scenario",
    participant_id: str | None = None,
    started_at: datetime = T0,
    ended_at: datetime | None = None,
) -> HackSessionRecord:
    return HackSessionRecord(
        session_id=session_id,
        scenario_id=scenario_id,
        started_at=started_at,
        ended_at=ended_at,
        participant_id=participant_id,
    )


# =============================================================================
# ACR — Attack Completion Rate
# =============================================================================


def _package_with_objectives(count: int, *, panel_id: str = "acr-test-panel") -> PanelPackage:
    """A synthetic package declaring `count` objectives, each its own event."""
    objectives = tuple(
        ObjectiveDeclaration(
            objective_id=f"objective-{i}",
            description=f"Objective {i}.",
            required_events=(f"event_{i}",),
        )
        for i in range(count)
    )
    return PanelPackage(
        schema_version=1,
        panel_id=panel_id,
        scenario=ScenarioDefinition(scenario_id="acr-test-scenario", title="ACR Test"),
        evaluation=EvaluationDeclaration(objectives=objectives),
    )


@pytest.mark.parametrize(
    "completed, total, expected_pct",
    [(0, 4, 0.0), (1, 4, 25.0), (2, 4, 50.0), (3, 4, 75.0), (4, 4, 100.0)],
)
def test_acr_fraction_matches_worked_examples(completed, total, expected_pct) -> None:
    package = _package_with_objectives(total)
    events = [
        _event("s1", i, f"event_{i}") for i in range(completed)
    ]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(expected_pct)


def test_acr_zero_completed_is_zero_percent_not_na() -> None:
    """An unsuccessful session still gets a real 0%, never N/A."""
    package = _package_with_objectives(4)
    result = compute_acr(package, "s1", events=[])
    assert result.status is MetricStatus.COMPUTED
    assert result.value == 0.0


def test_acr_quit_after_partial_completion() -> None:
    package = _package_with_objectives(4)
    events = [_event("s1", 1, "event_0"), _event("s1", 2, "event_1")]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(50.0)


def test_acr_successful_session_is_full_completion() -> None:
    package = _package_with_objectives(4)
    events = [_event("s1", i + 1, f"event_{i}") for i in range(4)]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(100.0)


def test_acr_duplicate_objective_completion_does_not_inflate_count() -> None:
    package = _package_with_objectives(2)
    events = [
        _event("s1", 1, "event_0"),
        _event("s1", 2, "event_0"),  # objective 0 "completed" again
        _event("s1", 3, "event_1"),
    ]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(100.0)  # 2/2, not counted as 3


def test_acr_objective_completion_from_another_session_does_not_count() -> None:
    package = _package_with_objectives(2)
    events = [
        _event("s1", 1, "event_0"),
        _event("OTHER-SESSION", 2, "event_1"),  # belongs to a different session
    ]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(50.0)


def test_acr_objective_count_comes_from_the_package_definition() -> None:
    small = _package_with_objectives(2)
    large = _package_with_objectives(6)
    events: list[HackEventRecord] = []
    assert compute_acr(small, "s1", events).value == 0.0
    assert compute_acr(large, "s1", events).value == 0.0
    # Same (empty) session activity, different totals -> same 0%, but the
    # denominators genuinely differ, which the detail message names.
    assert "2" in compute_acr(small, "s1", events).detail
    assert "6" in compute_acr(large, "s1", events).detail


def test_acr_objective_with_no_required_events_never_auto_completes() -> None:
    """A conceptual objective (no technical signal) never marks itself done,
    but still counts in the denominator — it caps the ceiling honestly."""
    package = PanelPackage(
        schema_version=1,
        panel_id="acr-conceptual",
        scenario=ScenarioDefinition(scenario_id="acr-test-scenario", title="ACR Test"),
        evaluation=EvaluationDeclaration(
            objectives=(
                ObjectiveDeclaration(objective_id="technical", description="x", required_events=("event_a",)),
                ObjectiveDeclaration(objective_id="conceptual", description="y", required_events=()),
            )
        ),
    )
    events = [_event("s1", 1, "event_a")]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(50.0)  # capped at 1/2, never 100%


def test_acr_is_not_applicable_when_module_declares_no_objectives() -> None:
    package = _package_with_objectives(0)
    result = compute_acr(package, "s1", events=[])
    assert result.status is MetricStatus.NOT_APPLICABLE
    assert result.value is None


def _panel_one_package() -> PanelPackage:
    return default_panel_package_loader().load("smart-home-mqtt-control")


def test_acr_against_the_real_panel_one_package_reaches_full_completion() -> None:
    """All five Smart Home ACTIVITY objectives (`evaluation.objectives`) are
    measurable milestones of the guided workflow, so a session that produces
    every canonical scenario event reaches 5/5 = 100% — there is no
    artificial ceiling from unobservable objectives."""
    package = _panel_one_package()
    assert len(package.evaluation.objectives) == 5
    all_events = [
        "firmware_extracted",
        "firmware_analyzed",
        "broker_discovered",
        "topic_discovered",
        "mqtt_observed",
        "spoof_attempted",
        "spoof_succeeded",
        "target_impacted",
        "attack_completed",
    ]
    events = [_event("s1", i + 1, name) for i, name in enumerate(all_events)]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(100.0)


def test_acr_panel_one_only_firmware_extraction_is_one_fifth() -> None:
    package = _panel_one_package()
    events = [_event("s1", 1, "firmware_extracted")]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(20.0)


def test_acr_panel_one_extraction_and_analysis_objectives_in_isolation() -> None:
    """Formula-level check, independent of how the live scenario happens to
    bundle its events: objectives 1 and 2 alone (without the discovery
    objective's evidence) score 2/5 = 40%."""
    package = _panel_one_package()
    events = [_event("s1", 1, "firmware_extracted"), _event("s1", 2, "firmware_analyzed")]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(40.0)


def test_acr_panel_one_extraction_then_analysis_through_the_real_scenario() -> None:
    """The real `SmartHomeMQTTScenario.analyze_firmware()` emits
    `firmware_analyzed`, `broker_discovered` and `topic_discovered` together
    on first analysis (Phase 2E.1, unchanged by this correction), so driving
    the actual scenario through extraction + analysis satisfies THREE
    objectives at once (extract, analyze, discover), not two — this is the
    live system's real, bundled behaviour, distinct from the isolated
    formula check above."""
    from app.scenarios import SmartHomeMQTTScenario

    scenario = SmartHomeMQTTScenario()
    scenario.extract_firmware()
    scenario.analyze_firmware(None)

    package = _panel_one_package()
    result = compute_acr(package, "s1", [
        _event("s1", i + 1, e.type.value) for i, e in enumerate(scenario.events)
    ])
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(60.0)


def test_acr_panel_one_discovery_objective_requires_both_broker_and_topic() -> None:
    package = _panel_one_package()

    broker_only = [_event("s1", 1, "broker_discovered")]
    assert compute_acr(package, "s1", broker_only).value == pytest.approx(0.0)

    both = [_event("s1", 1, "broker_discovered"), _event("s1", 2, "topic_discovered")]
    result = compute_acr(package, "s1", both)
    assert result.value == pytest.approx(20.0)  # exactly one objective (#3)


def test_acr_panel_one_observation_adds_the_fourth_objective() -> None:
    package = _panel_one_package()
    events = [
        _event("s1", 1, "firmware_extracted"),
        _event("s1", 2, "firmware_analyzed"),
        _event("s1", 3, "broker_discovered"),
        _event("s1", 4, "topic_discovered"),
        _event("s1", 5, "mqtt_observed"),
    ]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(80.0)  # objectives 1-4 of 5


def test_acr_panel_one_successful_attack_adds_the_fifth_objective() -> None:
    package = _panel_one_package()
    events = [
        _event("s1", 1, "firmware_extracted"),
        _event("s1", 2, "firmware_analyzed"),
        _event("s1", 3, "broker_discovered"),
        _event("s1", 4, "topic_discovered"),
        _event("s1", 5, "mqtt_observed"),
        _event("s1", 6, "attack_completed"),
    ]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(100.0)


def test_acr_panel_one_duplicate_events_do_not_inflate_completion() -> None:
    package = _panel_one_package()
    events = [
        _event("s1", 1, "firmware_extracted"),
        _event("s1", 2, "firmware_extracted"),  # repeated
        _event("s1", 3, "firmware_extracted"),  # repeated again
    ]
    result = compute_acr(package, "s1", events)
    assert result.value == pytest.approx(20.0)  # still exactly one objective


def test_acr_panel_one_conceptual_learning_objectives_do_not_affect_the_denominator() -> None:
    """`learning.objectives` (the broader academic list — includes two purely
    conceptual entries, "explain why..."/"identify the fix...") is a
    completely separate concept from `evaluation.objectives` (the five
    measurable activity goals ACR is defined over). The learning list's
    content and count must have no bearing on ACR's denominator."""
    package = _panel_one_package()
    assert len(package.learning.objectives) == 5
    assert any("Explain why" in text for text in package.learning.objectives)
    assert any("Identify the firmware" in text for text in package.learning.objectives)
    # The ACR denominator is the ACTIVITY objective count, unrelated in
    # content to the (also 5, coincidentally) learning objectives above.
    assert len(package.evaluation.objectives) == 5
    result = compute_acr(package, "s1", events=[])
    assert result.status is MetricStatus.COMPUTED
    assert result.value == 0.0  # a real 0/5, not skewed by the learning list


def test_acr_panel_one_incomplete_session_still_produces_a_partial_number() -> None:
    package = _panel_one_package()
    events = [_event("s1", 1, "firmware_extracted"), _event("s1", 2, "firmware_analyzed")]
    result = compute_acr(package, "s1", events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(40.0)


# =============================================================================
# RE — Reconnaissance Efficiency
# =============================================================================


def _re_package() -> PanelPackage:
    return PanelPackage(
        schema_version=1,
        panel_id="re-test-panel",
        scenario=ScenarioDefinition(scenario_id="re-test-scenario", title="RE Test"),
        workflow=(
            WorkflowStep(step_id="a", title="A", command="tool-a", phase=WorkflowPhase.RECONNAISSANCE),
            WorkflowStep(step_id="b", title="B", command="tool-b", phase=WorkflowPhase.RECONNAISSANCE),
            WorkflowStep(step_id="x", title="X", command="tool-x", phase=WorkflowPhase.EXPLOITATION),
        ),
    )


def test_re_all_recognized_commands_correct() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-b", exit_code=0, fields_correct=True),
        _command("s1", 3, "tool-x", exit_code=0),  # concludes reconnaissance
    ]
    result = compute_re(package, "s1", commands)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(100.0)


def test_re_one_recognized_command_incorrect() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-a", exit_code=0, fields_correct=False),
        _command("s1", 3, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(50.0)


def test_re_multiple_incorrect_recognized_commands() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-a", exit_code=0, fields_correct=False),
        _command("s1", 3, "tool-b", exit_code=1, fields_correct=False),
        _command("s1", 4, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(1 / 3 * 100)


def test_re_invalid_command_is_excluded_entirely() -> None:
    """An invalid/unrecognized command changes neither numerator nor
    denominator — 2 recognized (1 correct) + 1 unrecognized stays 50%."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-a", exit_code=0, fields_correct=False),
        _command("s1", 3, "not-a-real-tool", exit_code=127, handled=False),
        _command("s1", 4, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(50.0)


def test_re_misspelled_command_is_excluded() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "toool-a", exit_code=127, handled=False),  # typo
        _command("s1", 3, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(100.0)


def test_re_incorrect_required_parameter() -> None:
    """Recognized, ran successfully (exit 0), but the scenario says a
    required field was wrong — must not count as correct."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=False),
        _command("s1", 2, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(0.0)


def test_re_correct_tool_but_incorrect_field() -> None:
    """The manuscript's own example: correct tool, correct host, incorrect
    port -> recognized=yes, fully correct=no."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=False),
        _command("s1", 2, "tool-b", exit_code=0, fields_correct=True),
        _command("s1", 3, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(50.0)


def test_re_reconnaissance_incomplete_is_na() -> None:
    """No exploitation-phase command yet issued -> the phase has not
    concluded, so RE is N/A rather than a number that could still change."""
    package = _re_package()
    commands = [_command("s1", 1, "tool-a", exit_code=0, fields_correct=True)]
    result = compute_re(package, "s1", commands)
    assert result.status is MetricStatus.NOT_APPLICABLE
    assert result.value is None


def test_re_commands_outside_reconnaissance_are_excluded() -> None:
    """The exploitation-phase command itself is never counted in RE's own
    numerator/denominator, only used to detect the phase boundary."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-x", exit_code=1, fields_correct=False),  # failed exploit attempt
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(100.0)  # tool-x never enters the fraction


def test_re_phase_boundary_is_declared_by_the_package_not_inferred() -> None:
    """Swap which tool is EXPLOITATION; the same commands now score
    differently, proving the boundary is read from package data, not
    hardcoded as "everything before the last tool"."""
    reordered = PanelPackage(
        schema_version=1,
        panel_id="re-test-panel-2",
        scenario=ScenarioDefinition(scenario_id="re-test-scenario", title="RE Test"),
        workflow=(
            WorkflowStep(step_id="a", title="A", command="tool-a", phase=WorkflowPhase.EXPLOITATION),
            WorkflowStep(step_id="b", title="B", command="tool-b", phase=WorkflowPhase.RECONNAISSANCE),
        ),
    )
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-b", exit_code=0, fields_correct=True),
    ]
    result = compute_re(reordered, "s1", commands)
    # tool-a concludes recon immediately; tool-b (the only recon tool) is
    # 100% correct, and tool-a is excluded from the fraction entirely.
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(100.0)


def test_re_all_required_fields_must_be_correct_not_just_success() -> None:
    """Success alone (exit_code == 0) is not sufficient when the scenario
    explicitly flags a required field as wrong."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=False),  # "succeeded" but wrong field
        _command("s1", 2, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(0.0)


def test_re_manuscript_worked_example_two_recognized_one_correct() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-b", exit_code=0, fields_correct=False),
        _command("s1", 3, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(50.0)


def test_re_unrecognized_command_does_not_change_the_result() -> None:
    """Same as the prior example, plus one unrecognized command: RE stays 50%."""
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-b", exit_code=0, fields_correct=False),
        _command("s1", 3, "gibberish", exit_code=127, handled=False),
        _command("s1", 4, "tool-x", exit_code=0),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(50.0)


def test_re_commands_from_another_session_are_excluded() -> None:
    package = _re_package()
    commands = [
        _command("s1", 1, "tool-a", exit_code=0, fields_correct=True),
        _command("s1", 2, "tool-x", exit_code=0),
        _command("OTHER", 3, "tool-a", exit_code=0, fields_correct=False),
    ]
    result = compute_re(package, "s1", commands)
    assert result.value == pytest.approx(100.0)


# =============================================================================
# EAC — Exploitation Attempt Count
# =============================================================================

ATTACK_COMPLETED = "attack_completed"


def test_eac_success_on_the_first_session() -> None:
    sessions = [_session("s1", participant_id="alice", started_at=T0)]
    events = {"s1": [_event("s1", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.status is MetricStatus.COMPUTED
    assert result.value == 1


def test_eac_quit_then_succeed_is_two() -> None:
    sessions = [
        _session("s1", participant_id="alice", started_at=T0),
        _session("s2", participant_id="alice", started_at=T0 + timedelta(minutes=10)),
    ]
    events = {"s1": [], "s2": [_event("s2", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.value == 2


def test_eac_two_quits_then_succeed_is_three() -> None:
    sessions = [
        _session("s1", participant_id="alice", started_at=T0),
        _session("s2", participant_id="alice", started_at=T0 + timedelta(minutes=10)),
        _session("s3", participant_id="alice", started_at=T0 + timedelta(minutes=20)),
    ]
    events = {"s1": [], "s2": [], "s3": [_event("s3", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.value == 3


def test_eac_multiple_commands_inside_one_session_do_not_inflate_it() -> None:
    """Five publish attempts inside session 1, which then succeeds: EAC=1."""
    sessions = [_session("s1", participant_id="alice", started_at=T0)]
    events = {
        "s1": [
            _event("s1", 1, "spoof_attempted"),
            _event("s1", 2, "spoof_rejected"),
            _event("s1", 3, "spoof_attempted"),
            _event("s1", 4, "spoof_rejected"),
            _event("s1", 5, "spoof_attempted"),
            _event("s1", 6, "spoof_rejected"),
            _event("s1", 7, "spoof_attempted"),
            _event("s1", 8, "spoof_rejected"),
            _event("s1", 9, "spoof_attempted"),
            _event("s1", 10, "spoof_succeeded"),
            _event("s1", 11, ATTACK_COMPLETED),
        ]
    }
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.value == 1


def test_eac_another_participant_is_excluded() -> None:
    sessions = [
        _session("s1", participant_id="alice", started_at=T0),
        _session("s2", participant_id="bob", started_at=T0 + timedelta(minutes=1)),
    ]
    events = {"s1": [], "s2": [_event("s2", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    # Alice has one ongoing, unsuccessful session; Bob's success must not
    # be attributed to her.
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE


def test_eac_another_activity_is_excluded() -> None:
    sessions = [
        _session("s1", participant_id="alice", scenario_id="panel-one", started_at=T0),
        _session(
            "s2", participant_id="alice", scenario_id="panel-two", started_at=T0 + timedelta(minutes=1)
        ),
    ]
    events = {"s1": [], "s2": [_event("s2", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="panel-one")
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE


def test_eac_ongoing_unsuccessful_sessions_are_not_yet_computable() -> None:
    sessions = [_session("s1", participant_id="alice", started_at=T0)]
    result = compute_eac(sessions, {}, participant_id="alice", scenario_id="test-scenario")
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE
    assert result.value is None


def test_eac_orders_sessions_chronologically_regardless_of_input_order() -> None:
    later = _session("s2", participant_id="alice", started_at=T0 + timedelta(minutes=10))
    earlier = _session("s1", participant_id="alice", started_at=T0)
    # Passed in reverse chronological order on purpose.
    sessions = [later, earlier]
    events = {"s1": [], "s2": [_event("s2", 1, ATTACK_COMPLETED)]}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.value == 2  # s1 is still attempt #1 despite input order


def test_eac_successful_session_terminates_the_sequence() -> None:
    """A session recorded AFTER the successful one must not extend the count."""
    sessions = [
        _session("s1", participant_id="alice", started_at=T0),
        _session("s2", participant_id="alice", started_at=T0 + timedelta(minutes=10)),
        _session("s3", participant_id="alice", started_at=T0 + timedelta(minutes=20)),
    ]
    events = {"s1": [], "s2": [_event("s2", 1, ATTACK_COMPLETED)], "s3": []}
    result = compute_eac(sessions, events, participant_id="alice", scenario_id="test-scenario")
    assert result.value == 2


def test_eac_with_no_participant_identity_is_not_applicable() -> None:
    """The honest gap: no session in this backend has a real participant id
    today (no authentication exists on /ws/hack), and this must never be
    silently treated as a group of one."""
    sessions = [_session("s1", participant_id=None, started_at=T0)]
    result = compute_eac(sessions, {}, participant_id=None, scenario_id="test-scenario")
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_eac_no_sessions_for_the_participant_is_not_applicable() -> None:
    result = compute_eac([], {}, participant_id="alice", scenario_id="test-scenario")
    assert result.status is MetricStatus.NOT_APPLICABLE


# =============================================================================
# TTE — Time-to-Exploitation
# =============================================================================


def test_tte_manuscript_worked_example() -> None:
    session = _session("s1", started_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc))
    events = [
        _event(
            "s1", 1, ATTACK_COMPLETED, at=datetime(2026, 1, 1, 10, 3, 25, tzinfo=timezone.utc)
        )
    ]
    result = compute_tte(session, events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(205.0)


def test_tte_successful_session() -> None:
    session = _session("s1", started_at=T0)
    events = [_event("s1", 1, ATTACK_COMPLETED, at=T0 + timedelta(seconds=42))]
    result = compute_tte(session, events)
    assert result.value == pytest.approx(42.0)


def test_tte_failed_ended_session_has_no_tte() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=5))
    result = compute_tte(session, events=[])
    assert result.status is MetricStatus.NOT_APPLICABLE
    assert result.value is None


def test_tte_abandoned_still_open_session_is_not_yet_computable() -> None:
    session = _session("s1", started_at=T0, ended_at=None)
    result = compute_tte(session, events=[])
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE
    assert result.value is None


def test_tte_uses_timezone_aware_server_timestamps() -> None:
    session = _session("s1", started_at=T0)
    success_at = T0 + timedelta(seconds=10)
    assert success_at.tzinfo is not None
    events = [_event("s1", 1, ATTACK_COMPLETED, at=success_at)]
    result = compute_tte(session, events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(10.0)


def test_tte_success_event_from_another_session_is_ignored() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=1))
    events = [_event("OTHER-SESSION", 1, ATTACK_COMPLETED, at=T0 + timedelta(seconds=5))]
    result = compute_tte(session, events)
    assert result.status is MetricStatus.NOT_APPLICABLE  # session ended, no OWN success


def test_tte_success_before_session_start_is_rejected() -> None:
    session = _session("s1", started_at=T0)
    events = [_event("s1", 1, ATTACK_COMPLETED, at=T0 - timedelta(seconds=5))]
    result = compute_tte(session, events)
    assert result.status is MetricStatus.NOT_APPLICABLE
    assert result.value is None


def test_tte_duplicate_success_events_use_the_first() -> None:
    session = _session("s1", started_at=T0)
    events = [
        _event("s1", 1, ATTACK_COMPLETED, at=T0 + timedelta(seconds=30)),
        _event("s1", 2, ATTACK_COMPLETED, at=T0 + timedelta(seconds=90)),
    ]
    result = compute_tte(session, events)
    assert result.value == pytest.approx(30.0)


def test_tte_exact_boundary_zero_seconds() -> None:
    session = _session("s1", started_at=T0)
    events = [_event("s1", 1, ATTACK_COMPLETED, at=T0)]
    result = compute_tte(session, events)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(0.0)


# =============================================================================
# MetricValue model
# =============================================================================


def test_metric_value_computed_requires_a_value() -> None:
    with pytest.raises(ValueError):
        from app.metrics.results import MetricValue

        MetricValue(status=MetricStatus.COMPUTED, value=None)


def test_metric_value_not_applicable_carries_no_value() -> None:
    with pytest.raises(ValueError):
        from app.metrics.results import MetricValue

        MetricValue(status=MetricStatus.NOT_APPLICABLE, value=1.0)


def test_metric_value_is_available_property() -> None:
    from app.metrics.results import MetricValue

    assert MetricValue.computed(50.0).is_available is True
    assert MetricValue.not_applicable("x").is_available is False
    assert MetricValue.not_yet_computable("x").is_available is False


# =============================================================================
# Integration: the real router, the real Panel 1 package, end to end
# =============================================================================


def _dispatch(session: HackSession, line: str):
    return asyncio.run(default_router.dispatch(line, CommandContext(session=session)))


def test_metrics_computed_from_a_real_panel_one_session(isolated_event_store) -> None:
    """Drive the real command router through a real Smart Home session and
    compute all four metrics from what actually got recorded — proving the
    metric layer reads Phase 2E.1's real recorded activity, not a fixture
    shaped to fit."""
    from app.scenarios import SmartHomeMQTTScenario

    session = HackSession(
        session_id="integration-panel-one", scenario=SmartHomeMQTTScenario(), participant_id="alice"
    )
    session.recorder.start()

    _dispatch(session, "esptool.py read_flash 0x0 0x400000 firmware.bin")
    _dispatch(session, "strings firmware.bin")
    _dispatch(session, "nmap 192.168.50.1")
    _dispatch(session, "mosquitto_sub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control")
    _dispatch(session, "mosquitto_pub -h 192.168.50.1 -t cybertrainer/smart-home/motor/control -m START")

    session.recorder.finish()

    package = default_panel_package_loader().load("smart-home-mqtt-control")
    commands = list(session.recorder.commands)
    events = list(session.recorder.events)

    acr = compute_acr(package, session.session_id, events)
    assert acr.status is MetricStatus.COMPUTED
    assert acr.value == pytest.approx(100.0)  # the full guided flow completes all 5

    re_result = compute_re(package, session.session_id, commands)
    assert re_result.status is MetricStatus.COMPUTED
    assert re_result.value == pytest.approx(100.0)  # all 4 recon commands correct

    session_record = isolated_event_store.session(session.session_id)
    assert session_record is not None
    tte = compute_tte(session_record, events)
    assert tte.status is MetricStatus.COMPUTED
    assert tte.value >= 0.0

    eac = compute_eac(
        [session_record],
        {session.session_id: events},
        participant_id="alice",
        scenario_id=session.scenario.scenario_id,
    )
    assert eac.status is MetricStatus.COMPUTED
    assert eac.value == 1


def test_re_reconnaissance_incomplete_on_a_real_session_that_never_attacked(
    isolated_event_store,
) -> None:
    from app.scenarios import SmartHomeMQTTScenario

    session = HackSession(session_id="integration-recon-only", scenario=SmartHomeMQTTScenario())
    session.recorder.start()
    _dispatch(session, "nmap 192.168.50.1")
    session.recorder.finish()

    package = default_panel_package_loader().load("smart-home-mqtt-control")
    result = compute_re(package, session.session_id, list(session.recorder.commands))
    assert result.status is MetricStatus.NOT_APPLICABLE


# =============================================================================
# Genericity and security
# =============================================================================


def test_metrics_package_imports_nothing_panel_specific() -> None:
    """No module under app/metrics may import a specific scenario
    implementation — only the generic base/events/panels layers."""
    banned = ("app.scenarios.smart_home", "app.scenarios.environmental")
    for path in sorted(METRICS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(name for name in imported for bad in banned if name == bad)
        assert offenders == [], f"{path.name} imports {offenders}"


def test_metrics_package_contains_no_panel_specific_literal() -> None:
    """No hardcoded Panel 1 MAC, panel id, or topic string anywhere in the
    generic metric layer's actual code constants."""
    banned_literals = {
        "smart-home-mqtt-control",
        "20:9b:a9:88:0b:e4",
        "cybertrainer/smart-home/motor/control",
        "cybertrainer/smart-home/motor/state",
        "192.168.50.1",
    }
    for path in sorted(METRICS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in banned_literals
        )
        assert offenders == [], f"{path.name} names panel-specific literals: {offenders}"


@pytest.mark.parametrize(
    "filename",
    ["__init__.py", "results.py", "acr.py", "reconnaissance_efficiency.py", "eac.py", "tte.py"],
)
def test_metrics_module_uses_no_dynamic_execution(filename: str) -> None:
    tree = ast.parse((METRICS_DIR / filename).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__"}, filename
        if isinstance(func, ast.Attribute):
            assert func.attr not in {
                "system",
                "popen",
                "spawn",
                "spawnv",
                "import_module",
                "Popen",
                "run",
            }, filename
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                ), filename


@pytest.mark.parametrize(
    "filename",
    ["__init__.py", "results.py", "acr.py", "reconnaissance_efficiency.py", "eac.py", "tte.py"],
)
def test_metrics_module_imports_nothing_that_executes_or_connects(filename: str) -> None:
    tree = ast.parse((METRICS_DIR / filename).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = ("subprocess", "socket", "serial", "paho", "importlib", "os", "app.build", "app.hardware")
    offenders = sorted(
        name for name in imported for bad in banned if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"{filename} imports {offenders}"
