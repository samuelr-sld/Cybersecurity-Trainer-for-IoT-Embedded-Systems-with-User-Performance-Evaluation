"""Reconnaissance Efficiency (RE) — final manuscript, Section 3.10.1.

    RE = (Recognized Commands with All Fields Correct
          / Total Recognized Commands Issued During the Reconnaissance Phase) x 100%

GENERIC BY CONSTRUCTION. This module reads a `PanelPackage`'s declared
`workflow` (`WorkflowStep.phase`, see `app/panels/models.py::WorkflowPhase`)
and one session's recorded `HackCommandRecord`s. It never names a tool,
host, port or topic — those are per-package/per-command facts already
carried on the records themselves.

RECOGNIZED. A command is "recognized" exactly when a registered handler ran
for it — `HackCommandRecord.handled` (Phase 2B), the same flag that already
distinguishes "no such tool" from "the tool ran". An unrecognized or
misspelled command (`handled=False`) is excluded from the denominator
entirely, per the manuscript.

ALL REQUIRED FIELDS CORRECT. `exit_code == 0` alone is not sufficient — a
real `nmap` against a reachable host on the WRONG port is a successful scan
that correctly reports the port closed, yet the port argument was still
wrong. `HackCommandRecord.fields_correct` (Phase 2E.2, forwarded from
`ScenarioOutcome.fields_correct`) is the scenario's own, ground-truth-backed
answer to exactly that question; `None` means "not applicable" (a tool with
no target-fact fields to get wrong, e.g. `esptool.py`/`strings`/`grep`), so
a command counts as fully correct when `exit_code == 0` AND
`fields_correct is not False`.

THE RECONNAISSANCE PHASE BOUNDARY IS DECLARED, NOT INFERRED. Nothing in the
pre-existing architecture separated "reconnaissance" tools from
"exploitation" tools — `CommandCategory` groups `mosquitto_sub` and
`mosquitto_pub` into the same MQTT bucket, and workflow-step ORDER is a
hint, not a declared fact a metric should read a phase off of. So the
package declares it explicitly per step (`WorkflowStep.phase`), and this
function takes the reconnaissance-phase tool names as *data* from the
package — never a hardcoded assumption like "everything before
mosquitto_pub".

RECONNAISSANCE "INCOMPLETE" = THE SESSION HAS NOT YET LEFT IT. RE reports a
final efficiency figure for a phase, so it is only meaningful once that
phase has concluded — a still-in-progress reconnaissance attempt might still
improve or worsen. The generic, package-driven signal for "reconnaissance
has concluded" is exactly the same phase split: the session has issued at
least one recognized command belonging to a workflow step declared
EXPLOITATION. Until then, RE is N/A rather than a number that could still
change on the very next command.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.metrics.results import MetricValue
from app.panels.models import WorkflowPhase

if TYPE_CHECKING:  # pragma: no cover - type-only
    from app.events.records import HackCommandRecord
    from app.panels.models import PanelPackage


def _tool_names(package: "PanelPackage", phase: WorkflowPhase) -> frozenset[str]:
    """The distinct real-tool command names the package declares for `phase`."""
    return frozenset(
        step.command
        for step in package.workflow
        if step.phase is phase and step.command is not None
    )


def _is_fully_correct(command: "HackCommandRecord") -> bool:
    return command.exit_code == 0 and command.fields_correct is not False


def compute_re(
    package: "PanelPackage", session_id: str, commands: Sequence["HackCommandRecord"]
) -> MetricValue:
    """RE for one session, given its package and its recorded command log.

    `commands` may be any collection of recorded commands — this function
    filters to `session_id` itself (defence in depth, same reasoning as
    `app/metrics/acr.py::compute_acr`) and then to sequence order, so a
    caller may pass an unsorted or unscoped collection safely. Typical usage
    passes `SqliteEventStore.commands_for_session(session_id)`, which is
    already scoped and ordered, making both steps a no-op in practice.
    """
    scoped = sorted(
        (command for command in commands if command.session_id == session_id),
        key=lambda command: command.sequence,
    )
    recon_tools = _tool_names(package, WorkflowPhase.RECONNAISSANCE)
    exploit_tools = _tool_names(package, WorkflowPhase.EXPLOITATION)

    reconnaissance_concluded = any(
        command.handled and command.name in exploit_tools for command in scoped
    )
    if not reconnaissance_concluded:
        return MetricValue.not_applicable(
            "reconnaissance phase has not concluded for this session "
            "(no exploitation-phase command recorded yet)"
        )

    recognized = [
        command
        for command in scoped
        if command.handled and command.name in recon_tools
    ]
    total = len(recognized)
    if total == 0:
        return MetricValue.not_applicable(
            "no recognized reconnaissance-phase commands were issued"
        )

    correct = sum(1 for command in recognized if _is_fully_correct(command))
    percentage = (correct / total) * 100
    return MetricValue.computed(
        percentage,
        detail=f"{correct}/{total} recognized reconnaissance commands fully correct",
    )
