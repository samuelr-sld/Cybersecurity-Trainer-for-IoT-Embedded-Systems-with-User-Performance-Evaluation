"""Generic performance metric computation — Phase 2E.2 (Hack) + 2E.3 (Build).

    Student action -> Command Router -> Scenario -> Command/Event Recording
        -> SQLite / Session Records -> GENERIC METRIC COMPUTATION (this
        package) -> MetricValue

Scope: all seven metrics named in the final capstone manuscript, Section
3.10.1. Hack Mode (Phase 2E.2) — Attack Completion Rate (ACR),
Reconnaissance Efficiency (RE), Exploitation Attempt Count (EAC — see
`app/metrics/eac.py` for why that is the manuscript's name, not "Exploit
Action Completeness"), and Time-to-Exploitation (TTE). Build Mode (Phase
2E.3) — Time-to-Resolution (TTR), Attempt and Iteration Density (AID — see
`app/metrics/aid.py` for the same kind of name correction), and Debugging
Efficiency Index (DEI).

GENERIC, NOT PANEL-SPECIFIC. No module in this package imports
`app.scenarios.smart_home`/`environmental` or any Build project module,
mentions MQTT, a motor, a topic string, firmware source, or any panel id.
Every function takes plain, already-recorded data — a `PanelPackage` (data
a panel declared) and `HackSessionRecord`/`HackCommandRecord`/
`HackEventRecord`/`BuildSessionRecord`/`BuildAttemptRecord` rows (data the
Phase 2B/2E.3 recorders already wrote) — and returns a `MetricValue`
(`app/metrics/results.py`). Panel-specific meaning lives entirely in a
package's declared data (`evaluation.objectives`, `workflow[].phase`); the
three Build metrics need no package at all, since — like TTE/EAC — their
success/attempt/duration signals are already generic, cross-panel facts
(a validated fix, a compile/flash/validation attempt), never a
panel-declared one.

NO EXECUTION, NO EVALUATION OF ANYTHING. This package contains no
`eval`/`exec`/`compile`, no subprocess, and no dynamic dispatch off a
package's declared data — an `EvaluationMetric` name selects a plain
function by an explicit `if`/lookup in a caller, never by constructing an
attribute or module name from a string. It computes a NUMBER (or an honest
non-numeric `MetricValue` status) from data that already exists; it never
triggers a command, a compile, a flash, a scenario transition, or a new
recorded event.
"""

from __future__ import annotations

from app.metrics.acr import compute_acr
from app.metrics.aid import compute_aid
from app.metrics.dei import compute_dei
from app.metrics.eac import compute_eac
from app.metrics.reconnaissance_efficiency import compute_re
from app.metrics.results import MetricStatus, MetricValue
from app.metrics.tte import compute_tte
from app.metrics.ttr import compute_ttr

__all__ = [
    "MetricStatus",
    "MetricValue",
    "compute_acr",
    "compute_aid",
    "compute_dei",
    "compute_eac",
    "compute_re",
    "compute_tte",
    "compute_ttr",
]
