"""Phase 2E.3 — generic Build Mode performance metric computation.

Covers the three Build Mode metrics from the final capstone manuscript
(Section 3.10.1): Time-to-Resolution (TTR), Attempt and Iteration Density
(AID), and Debugging Efficiency Index (DEI).

Unit tests against synthetic `BuildSessionRecord`/`BuildAttemptRecord`
fixtures, which pin the exact formula/segmentation semantics independently
of any one panel — mirroring `tests/test_metrics.py`'s approach for the Hack
Mode metrics. An integration test at the end drives the real `BuildService`
(against fake compiler/flasher adapters, no real subprocess) to prove the
metrics read what `app/build/service.py` actually records.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.build.records import BuildAttemptRecord, BuildAttemptType, BuildSessionRecord
from app.metrics import MetricStatus, compute_aid, compute_dei, compute_ttr

METRICS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "metrics"

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def _session(
    session_id: str,
    *,
    started_at: datetime = T0,
    ended_at: datetime | None = None,
    panel_id: str | None = None,
) -> BuildSessionRecord:
    return BuildSessionRecord(
        session_id=session_id, started_at=started_at, ended_at=ended_at, panel_id=panel_id
    )


def _attempt(
    session_id: str,
    sequence: int,
    attempt_type: BuildAttemptType,
    success: bool,
    at: datetime = T0,
    detail: str = "",
) -> BuildAttemptRecord:
    return BuildAttemptRecord(
        session_id=session_id,
        sequence=sequence,
        attempt_type=attempt_type,
        success=success,
        occurred_at=at,
        detail=detail,
    )


COMPILE = BuildAttemptType.COMPILE
FLASH = BuildAttemptType.FLASH
VALIDATION = BuildAttemptType.VALIDATION


# =============================================================================
# TTR — Time-to-Resolution
# =============================================================================


def test_ttr_worked_example() -> None:
    session = _session("s1", started_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc))
    attempts = [
        _attempt(
            "s1", 1, VALIDATION, True, at=datetime(2026, 1, 1, 10, 3, 25, tzinfo=timezone.utc)
        )
    ]
    result = compute_ttr(session, attempts)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(205.0)


def test_ttr_successful_session() -> None:
    session = _session("s1", started_at=T0)
    attempts = [_attempt("s1", 1, VALIDATION, True, at=T0 + timedelta(seconds=90))]
    result = compute_ttr(session, attempts)
    assert result.value == pytest.approx(90.0)


def test_ttr_ignores_compile_and_flash_success_as_the_fix() -> None:
    """A compile succeeding, and a flash succeeding, are NOT successful
    remediation evidence — only a validation success is."""
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=10))
    attempts = [
        _attempt("s1", 1, COMPILE, True, at=T0 + timedelta(seconds=10)),
        _attempt("s1", 2, FLASH, True, at=T0 + timedelta(seconds=20)),
    ]
    result = compute_ttr(session, attempts)
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_ttr_failed_ended_session_has_no_ttr() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=5))
    result = compute_ttr(session, attempts=[])
    assert result.status is MetricStatus.NOT_APPLICABLE
    assert result.value is None


def test_ttr_abandoned_still_open_session_is_not_yet_computable() -> None:
    session = _session("s1", started_at=T0, ended_at=None)
    attempts = [_attempt("s1", 1, COMPILE, False, at=T0 + timedelta(seconds=5))]
    result = compute_ttr(session, attempts)
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE


def test_ttr_uses_build_mode_session_start_not_hack_mode() -> None:
    """A distinctly Build-Mode-shaped start (no scenario_id/participant_id
    exists on `BuildSessionRecord` at all) proves this is a different clock
    from `app/metrics/tte.py`'s Hack Mode session."""
    build_start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    session = _session("s1", started_at=build_start)
    attempts = [_attempt("s1", 1, VALIDATION, True, at=build_start + timedelta(seconds=15))]
    result = compute_ttr(session, attempts)
    assert result.value == pytest.approx(15.0)


def test_ttr_success_from_another_session_is_ignored() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=1))
    attempts = [_attempt("OTHER", 1, VALIDATION, True, at=T0 + timedelta(seconds=5))]
    result = compute_ttr(session, attempts)
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_ttr_validation_success_before_session_start_is_rejected() -> None:
    session = _session("s1", started_at=T0)
    attempts = [_attempt("s1", 1, VALIDATION, True, at=T0 - timedelta(seconds=5))]
    result = compute_ttr(session, attempts)
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_ttr_uses_the_first_validation_success() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, VALIDATION, False, at=T0 + timedelta(seconds=10)),
        _attempt("s1", 2, VALIDATION, True, at=T0 + timedelta(seconds=30)),
        _attempt("s1", 3, VALIDATION, True, at=T0 + timedelta(seconds=90)),
    ]
    result = compute_ttr(session, attempts)
    assert result.value == pytest.approx(30.0)


# =============================================================================
# AID — Attempt and Iteration Density
# =============================================================================


def test_aid_counts_compile_flash_and_validation_attempts() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=2))
    attempts = [
        _attempt("s1", 1, COMPILE, False),
        _attempt("s1", 2, COMPILE, True),
        _attempt("s1", 3, FLASH, True),
        _attempt("s1", 4, VALIDATION, False),
    ]
    result = compute_aid(session, attempts)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(4 / 2)  # 4 attempts / 2 minutes


def test_aid_zero_attempts_is_a_real_zero() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=5))
    result = compute_aid(session, attempts=[])
    assert result.status is MetricStatus.COMPUTED
    assert result.value == 0.0


def test_aid_open_session_is_not_yet_computable() -> None:
    session = _session("s1", started_at=T0, ended_at=None)
    attempts = [_attempt("s1", 1, COMPILE, True)]
    result = compute_aid(session, attempts)
    assert result.status is MetricStatus.NOT_YET_COMPUTABLE


def test_aid_zero_duration_session_is_not_applicable() -> None:
    session = _session("s1", started_at=T0, ended_at=T0)
    result = compute_aid(session, attempts=[_attempt("s1", 1, COMPILE, True)])
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_aid_failed_attempts_count_the_same_as_successes() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=1))
    all_failed = [
        _attempt("s1", 1, COMPILE, False),
        _attempt("s1", 2, COMPILE, False),
    ]
    result = compute_aid(session, all_failed)
    assert result.value == pytest.approx(2.0)


def test_aid_excludes_attempts_from_another_session() -> None:
    session = _session("s1", started_at=T0, ended_at=T0 + timedelta(minutes=1))
    attempts = [
        _attempt("s1", 1, COMPILE, True),
        _attempt("OTHER", 2, COMPILE, True),
    ]
    result = compute_aid(session, attempts)
    assert result.value == pytest.approx(1.0)


# =============================================================================
# DEI — Debugging Efficiency Index
# =============================================================================


def test_dei_single_resolved_segment() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, True, at=T0 + timedelta(seconds=60)),
    ]
    result = compute_dei(session, attempts)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(60.0)


def test_dei_multiple_failures_before_success_stay_one_segment() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, False, at=T0 + timedelta(seconds=20)),
        _attempt("s1", 3, COMPILE, False, at=T0 + timedelta(seconds=40)),
        _attempt("s1", 4, COMPILE, True, at=T0 + timedelta(seconds=90)),
    ]
    result = compute_dei(session, attempts)
    # ONE segment, from the FIRST failure to the success: 90s, not 3 segments.
    assert result.value == pytest.approx(90.0)
    assert "1 resolved debugging segment" in result.detail


def test_dei_unresolved_segment_is_excluded() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, False, at=T0 + timedelta(seconds=30)),
        # no later success — this segment never resolves
    ]
    result = compute_dei(session, attempts)
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_dei_no_failures_at_all_is_not_applicable() -> None:
    session = _session("s1", started_at=T0)
    attempts = [_attempt("s1", 1, COMPILE, True, at=T0)]
    result = compute_dei(session, attempts)
    assert result.status is MetricStatus.NOT_APPLICABLE


def test_dei_averages_multiple_resolved_segments_of_the_same_type() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, True, at=T0 + timedelta(seconds=10)),  # segment: 10s
        _attempt("s1", 3, COMPILE, False, at=T0 + timedelta(seconds=100)),
        _attempt("s1", 4, COMPILE, True, at=T0 + timedelta(seconds=130)),  # segment: 30s
    ]
    result = compute_dei(session, attempts)
    assert result.value == pytest.approx((10.0 + 30.0) / 2)


def test_dei_pools_segments_across_different_attempt_types() -> None:
    """A failed COMPILE closes on the next COMPILE success; a failed FLASH
    closes on the next FLASH success — never on each other — but both
    resolved segments contribute to the one session-wide average."""
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, FLASH, False, at=T0 + timedelta(seconds=5)),
        _attempt("s1", 3, COMPILE, True, at=T0 + timedelta(seconds=20)),  # compile segment: 20s
        _attempt("s1", 4, FLASH, True, at=T0 + timedelta(seconds=45)),  # flash segment: 40s
    ]
    result = compute_dei(session, attempts)
    assert result.value == pytest.approx((20.0 + 40.0) / 2)


def test_dei_a_success_with_no_preceding_failure_starts_no_segment() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, True, at=T0),  # first try succeeds
        _attempt("s1", 2, COMPILE, False, at=T0 + timedelta(seconds=10)),
        _attempt("s1", 3, COMPILE, True, at=T0 + timedelta(seconds=50)),  # segment: 40s
    ]
    result = compute_dei(session, attempts)
    assert result.value == pytest.approx(40.0)


def test_dei_can_be_computed_from_a_still_open_session() -> None:
    """Resolved segments are fixed the moment the success lands; DEI does
    not require the whole session to have ended."""
    session = _session("s1", started_at=T0, ended_at=None)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, True, at=T0 + timedelta(seconds=15)),
    ]
    result = compute_dei(session, attempts)
    assert result.status is MetricStatus.COMPUTED
    assert result.value == pytest.approx(15.0)


def test_dei_excludes_attempts_from_another_session() -> None:
    session = _session("s1", started_at=T0)
    attempts = [
        _attempt("s1", 1, COMPILE, False, at=T0),
        _attempt("s1", 2, COMPILE, True, at=T0 + timedelta(seconds=10)),
        _attempt("OTHER", 3, COMPILE, False, at=T0),
        _attempt("OTHER", 4, COMPILE, True, at=T0 + timedelta(seconds=1000)),
    ]
    result = compute_dei(session, attempts)
    assert result.value == pytest.approx(10.0)  # not skewed by the other session's 1000s


# =============================================================================
# Integration: the real BuildService, fake adapters, real recorder
# =============================================================================


def test_build_metrics_computed_from_a_real_build_session(isolated_event_store) -> None:
    from app.build.compiler import CompileFailureCategory, CompileOutcome
    from app.build.service import BuildService
    from app.build_sessions import BuildSession

    class _FakeCompiler:
        def __init__(self) -> None:
            self._calls = 0

        async def run_compile(self, request):
            self._calls += 1
            await asyncio.sleep(0)
            if self._calls == 1:
                return CompileOutcome.failed(
                    category=CompileFailureCategory.COMPILER_ERROR,
                    exit_code=1,
                    stdout="",
                    stderr="error",
                    duration_seconds=0.1,
                )
            return CompileOutcome.ok(exit_code=0, stdout="ok", stderr="", duration_seconds=0.1)

    async def run() -> None:
        service = BuildService(compiler=_FakeCompiler())
        session = BuildSession(session_id="integration-build")
        session.recorder.start()

        await service.compile_workspace(session)  # fails
        await service.compile_workspace(session)  # succeeds
        await service.record_validation_attempt(session, success=True, detail="fixed")

        session.recorder.finish()

        session_record = isolated_event_store.build_session(session.session_id)
        assert session_record is not None
        attempts = isolated_event_store.build_attempts_for_session(session.session_id)
        assert len(attempts) == 3

        ttr = compute_ttr(session_record, attempts)
        assert ttr.status is MetricStatus.COMPUTED
        assert ttr.value >= 0.0

        aid = compute_aid(session_record, attempts)
        assert aid.status is MetricStatus.COMPUTED
        assert aid.value > 0.0

        dei = compute_dei(session_record, attempts)
        assert dei.status is MetricStatus.COMPUTED
        assert dei.value >= 0.0

    asyncio.run(run())


# =============================================================================
# Genericity and security
# =============================================================================


def test_build_metrics_import_nothing_panel_specific() -> None:
    banned = ("app.scenarios.smart_home", "app.scenarios.environmental", "app.build.environmental")
    for filename in ("ttr.py", "aid.py", "dei.py"):
        tree = ast.parse((METRICS_DIR / filename).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(name for name in imported for bad in banned if name == bad)
        assert offenders == [], f"{filename} imports {offenders}"


@pytest.mark.parametrize("filename", ["ttr.py", "aid.py", "dei.py"])
def test_build_metrics_use_no_dynamic_execution(filename: str) -> None:
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


@pytest.mark.parametrize("filename", ["ttr.py", "aid.py", "dei.py"])
def test_build_metrics_import_no_subprocess_or_os(filename: str) -> None:
    tree = ast.parse((METRICS_DIR / filename).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = ("subprocess", "os", "socket", "serial")
    offenders = sorted(
        name for name in imported for bad in banned if name == bad or name.startswith(bad + ".")
    )
    assert offenders == [], f"{filename} imports {offenders}"
