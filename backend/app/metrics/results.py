"""The result shape every Hack Mode metric returns — never a bare number.

Phase 2E.2. The final capstone manuscript (Section 3.10.1) draws a sharp
line the task brief restates explicitly: a metric that legitimately computes
to zero (ACR with no completed objectives) is NOT the same fact as a metric
that cannot be computed at all (RE with reconnaissance still in progress,
TTE for a session that never succeeded). Collapsing either into a silent
`0` would be a wrong number wearing the clothes of a real one, so every
metric function in this package returns a `MetricValue`, never a bare
`float | None`.

Two "unavailable" shades, not one, because they mean different things to a
caller deciding what to show a professor:

    NOT_APPLICABLE     The metric is not a meaningful question for this
                        input, and asking again with the same session will
                        not change that (reconnaissance never happened;
                        the module declares no objectives; a session ended
                        without ever succeeding).

    NOT_YET_COMPUTABLE The metric may still become answerable — the
                        relevant activity (a still-open session, an
                        unresolved attempt sequence) has not finished
                        happening yet.

Both are absent-value states; neither is `0`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MetricStatus(str, Enum):
    """What a `MetricValue` represents: a number, or one honest reason it isn't."""

    COMPUTED = "computed"
    NOT_APPLICABLE = "not_applicable"
    NOT_YET_COMPUTABLE = "not_yet_computable"


@dataclass(frozen=True)
class MetricValue:
    """One metric's result: a status, and a value only when that status is COMPUTED.

    `value` is deliberately untyped beyond `float | int` — ACR/RE report a
    percentage (`float`), EAC reports a session count (`int`), TTE reports a
    duration in seconds (`float`). `detail` is a short, human-readable reason
    for the status; it is diagnostic text for a log or a professor's screen,
    never parsed by anything downstream.
    """

    status: MetricStatus
    value: float | int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status is MetricStatus.COMPUTED and self.value is None:
            raise ValueError("a COMPUTED MetricValue must carry a value")
        if self.status is not MetricStatus.COMPUTED and self.value is not None:
            raise ValueError(f"a {self.status.value} MetricValue must not carry a value")

    @classmethod
    def computed(cls, value: float | int, detail: str = "") -> "MetricValue":
        return cls(status=MetricStatus.COMPUTED, value=value, detail=detail)

    @classmethod
    def not_applicable(cls, detail: str) -> "MetricValue":
        return cls(status=MetricStatus.NOT_APPLICABLE, value=None, detail=detail)

    @classmethod
    def not_yet_computable(cls, detail: str) -> "MetricValue":
        return cls(status=MetricStatus.NOT_YET_COMPUTABLE, value=None, detail=detail)

    @property
    def is_available(self) -> bool:
        return self.status is MetricStatus.COMPUTED
