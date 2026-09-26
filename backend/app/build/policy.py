"""Which discovered section a student may read, explore, or edit (Phase B8).

    BuildDocument / CodeSection[]        what the firmware IS        (B1)
              |
              v
    ProjectPolicy                        what a student may DO      (this module)
              |
              v
    FileSegment.kind  (LOCKED | EDITABLE)   what the workspace ENFORCES  (B2)

THREE POLICIES, TWO REGION KINDS, AND THAT IS DELIBERATE. `RegionKind`
(`app/build/models.py`) answers one question and has answered it since Phase
3A: may `BuildWorkspace.update_region` write here? It is the ENFORCEMENT
vocabulary, it is binary, and B8 does not widen it — adding a third member
would mean every existing consumer of the region model (the workspace's own
rejection path, `app/build/program_source.py`'s locked-region integrity check,
the `state` frame, the frontend) had to learn a value it has no rule for, and
the one that escaped `_locked_text` would be a span nothing protected.

`InteractionPolicy` answers a different question — what is this section FOR,
in this panel's remediation activity? — and EXPLORE is the member that only
makes sense there:

    LOCKED     infrastructure the activity does not concern. Shown, never
               editable, and not part of what the student is asked to reason
               about.
    EXPLORE    read-only, but deliberately relevant: the code the student must
               READ to understand the vulnerability (the callback that accepts
               a command, the helpers it actuates through, the configuration
               it trusts). Investigable, not writable.
    EDITABLE   the remediation region. The only policy that maps to
               `RegionKind.EDITABLE`.

So EXPLORE and LOCKED are both `RegionKind.LOCKED` segments — an EXPLORE
section is exactly as protected as a LOCKED one, by exactly the same code, and
`update_region` refuses it without knowing this module exists. The difference
is what the UI is told, not what the backend permits, which is why widening
the permission vocabulary would have been the wrong place to express it.

THE DEFAULT IS LOCKED, AND THAT IS THE ABSENCE OF A DECISION. A section nobody
classified is locked, the same conservative default
`app/build/document_project.py` already documents for `editable_section_ids`.
A policy is something a panel's remediation activity states on purpose.

POLICY IS ADDRESSED BY STABLE SECTION ID, NEVER BY FUNCTION NAME. The ids are
B1's own (`helper_applyCommand`, `callback_onMessage`, `global_3`), which B2
reuses verbatim as region ids — so one construct is named identically by the
discovery model, the workspace, the semantic IR and this policy, and no
consumer (least of all the frontend) has to re-derive editability by matching
C++ identifiers.

NO PANEL KNOWLEDGE, NO BLOCK KNOWLEDGE, NO I/O. This module is a leaf: it
imports nothing from `app` at all. It does not know which panel declared a
policy, what MQTT is, or that Blockly exists — a `BlockDefinition`
(`app/blockly/`) stays generic and gains no permission field, exactly as B4's
bridge is forbidden editability vocabulary. What a panel declares is read one
level up (`app/build_project_selection.py`, off the panel package's
remediation declaration) and arrives here as plain ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class InteractionPolicy(str, Enum):
    """What a student may do with one discovered section. See the module docstring."""

    LOCKED = "locked"
    EXPLORE = "explore"
    EDITABLE = "editable"

    @property
    def writable(self) -> bool:
        """Whether this policy permits an edit. Only EDITABLE does."""
        return self is InteractionPolicy.EDITABLE


#: What an unclassified section gets — see the module docstring.
DEFAULT_POLICY = InteractionPolicy.LOCKED


@dataclass(frozen=True)
class ProjectPolicy:
    """One project's complete section-interaction decision, validated.

    Frozen, mapping-backed, and deterministic: the same declaration always
    produces the same answer for the same section id, and `policy_for` is a
    dict lookup rather than a chain of conditionals — the same discipline
    `PanelRegistry`, `ScenarioRegistry` and `ValidationStrategyRegistry` hold.

    It carries no security-region field of its own. `BuildProject`
    (`app/build/models.py`) has named the remediation region since Phase 3A
    and stays the single source of truth for it; this policy only has to be
    CONSISTENT with it, which `BuildProject.__post_init__` checks.
    """

    policies: Mapping[str, InteractionPolicy] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        entries: dict[str, InteractionPolicy] = {}
        for section_id, policy in dict(self.policies).items():
            if not isinstance(section_id, str) or not section_id.strip():
                raise ValueError(f"policy section id must be a non-empty string: {section_id!r}")
            if not isinstance(policy, InteractionPolicy):
                raise ValueError(f"section {section_id!r}: not an InteractionPolicy: {policy!r}")
            entries[section_id] = policy
        object.__setattr__(self, "policies", MappingProxyType(entries))

    def policy_for(self, section_id: str) -> InteractionPolicy:
        """This section's policy, or the conservative default for an unnamed one."""
        return self.policies.get(section_id, DEFAULT_POLICY)

    def _ids_with(self, policy: InteractionPolicy) -> tuple[str, ...]:
        return tuple(
            sorted(
                section_id
                for section_id, declared in self.policies.items()
                if declared is policy
            )
        )

    @property
    def editable_section_ids(self) -> tuple[str, ...]:
        """Every explicitly EDITABLE section id, sorted — deterministic."""
        return self._ids_with(InteractionPolicy.EDITABLE)

    @property
    def explore_section_ids(self) -> tuple[str, ...]:
        """Every explicitly EXPLORE section id, sorted — deterministic."""
        return self._ids_with(InteractionPolicy.EXPLORE)

    @property
    def locked_section_ids(self) -> tuple[str, ...]:
        """Every EXPLICITLY locked section id, sorted.

        Not "every section that is locked": a section this policy never
        mentions is locked too (see `DEFAULT_POLICY`) and is not listed here,
        because this object does not know what sections exist. Ask
        `policy_for` for a definitive answer about one id, or read the
        project's own segments for the full set.
        """
        return self._ids_with(InteractionPolicy.LOCKED)

    @property
    def declared(self) -> bool:
        """Whether anything was actually classified."""
        return bool(self.policies)

    def snapshot(self) -> dict[str, object]:
        """A JSON-serialisable view for the Build Mode `state` frame."""
        return {
            "sections": {
                section_id: policy.value for section_id, policy in sorted(self.policies.items())
            },
            "editable_section_ids": list(self.editable_section_ids),
            "explore_section_ids": list(self.explore_section_ids),
        }


class ProjectPolicyError(ValueError):
    """A section policy could not be built for this document."""


def build_project_policy(
    section_ids: Iterable[str],
    *,
    editable_section_ids: Iterable[str] = (),
    explore_section_ids: Iterable[str] = (),
) -> ProjectPolicy:
    """Classify a document's sections. Every id must be one the document has.

    Raises `ProjectPolicyError` for an id no section carries, and for an id
    declared both EDITABLE and EXPLORE — a caller that named a region that
    does not exist, or asked for it to be two things at once, made a mistake
    worth failing on rather than one to resolve by precedence. This mirrors
    `firmware_file_from_document`'s existing rejection of an unknown editable
    id, for the same reason.

    Sections named by neither argument are recorded as LOCKED explicitly, so
    a policy that classifies anything states a decision for every section the
    document has rather than relying on the lookup default — the default
    exists for a section nobody has *seen*, not for one nobody classified.

    Classifying NOTHING returns an EMPTY policy rather than an all-LOCKED
    one. The two answer every `policy_for` question identically, but only the
    empty one is honest about the difference between "this activity decided
    every section is locked" and "no remediation activity has said anything",
    which is exactly what `declared` reports and what a B2 project (which
    decides nothing) is.
    """
    known = list(dict.fromkeys(section_ids))
    known_set = set(known)
    editable = list(dict.fromkeys(editable_section_ids))
    explore = list(dict.fromkeys(explore_section_ids))

    unknown = sorted((set(editable) | set(explore)) - known_set)
    if unknown:
        raise ProjectPolicyError(
            f"no such section(s) to give a policy: {', '.join(unknown)}"
        )
    both = sorted(set(editable) & set(explore))
    if both:
        raise ProjectPolicyError(
            f"section(s) declared both editable and explore: {', '.join(both)}"
        )

    if not editable and not explore:
        return ProjectPolicy()

    policies: dict[str, InteractionPolicy] = {
        section_id: InteractionPolicy.LOCKED for section_id in known
    }
    for section_id in explore:
        policies[section_id] = InteractionPolicy.EXPLORE
    for section_id in editable:
        policies[section_id] = InteractionPolicy.EDITABLE
    return ProjectPolicy(policies=policies)
