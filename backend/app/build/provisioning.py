"""Compile-time firmware provisioning — the generic contract (Option A).

    BuildService.compile_workspace       orchestrates: gates, status, events
        |
        v
    ProvisioningStrategy.provision       performs: writes into the THROWAWAY
        |                                 compile-only sketch directory
        v
    (nothing — success just means the copy now compiles against real
     credentials; a failure is reported as an ordinary failed compile)

Mirrors `app/build/validation/`'s split, for the same reason. If
`BuildService` also owned "how do I get a real Wi-Fi/MQTT credential into
this panel's firmware", the first panel that needed one would put
panel-specific knowledge inside the generic engine, and the next one would
too. So the engine here knows only "ask this session's strategy to provision
the sketch directory it just materialized"; what that means for a given
panel lives in a strategy a `scenario_id` resolves to, exactly as
`ValidationStrategyRegistry` resolves a validator.

WHY THIS NEVER TOUCHES `BuildProject`/`FirmwareFile`. Every file in
`BuildProject.files` is rendered verbatim into `BuildWorkspace.snapshot()`'s
`state` frame (sent to the frontend) and into every materialized sketch
directory — there is no "locked but not sent to the client" segment kind. A
provisioned credential placed there would reach the WebSocket wire the
moment a session loaded, whether or not the student ever compiled anything.
A `ProvisioningStrategy` instead receives the PATH to a throwaway, per-compile
filesystem copy `BuildWorkspace.materialize` already wrote, strictly between
that call and the compiler invocation, and may only write into it — never
into the session's live workspace, never onto the wire.

NO PANEL BRANCH IN THE ENGINE. `BuildService` (`app/build/service.py`) never
asks "is this Panel 1" — it calls whatever strategy this session's
`ProvisioningPlan` carries, and a static scan asserts `service.py` names no
panel (see `tests/test_build_pipeline_b7.py`/`b8.py`). `ProvisioningStrategyRegistry`
is the one table mapping a `scenario_id` to a strategy; registering a panel
is adding a row, never a new branch. No panel registers one today — see
`app/build_provisioning_selection.py` for the (currently empty) table and
how a session picks a strategy up.

DEFAULT IS A NO-OP, NOT A REFUSAL. Unlike validation (which defaults to a
strategy that explains why it cannot run), an unprovisioned panel's compile
proceeds exactly as it always has — most panels ship no provisioning-worthy
secret at all, and their committed source is already everything
`arduino-cli` needs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class ProvisioningError(Exception):
    """A strategy could not provision the materialized sketch it was given.

    Caught exactly once, by `BuildService.compile_workspace`, and reported as
    a truthful failed compile — never propagated as an unhandled exception,
    and never silently swallowed in favour of compiling whatever placeholder
    the committed source already contains.
    """


@runtime_checkable
class ProvisioningStrategy(Protocol):
    """What `BuildService` needs from a provisioner — real or a test double."""

    def provision(self, sketch_dir: Path) -> None:
        """Write or rewrite files inside `sketch_dir` before it is compiled.

        `sketch_dir` is the throwaway, per-compile directory
        `BuildWorkspace.materialize` just wrote — never the session's live
        workspace, never anything a WebSocket frame carries. Implementations
        raise `ProvisioningError` for anything that stops them; they must not
        raise anything else.
        """
        ...


class NullProvisioningStrategy:
    """Every panel's default: the materialized sketch is compiled as-is."""

    def provision(self, sketch_dir: Path) -> None:
        return None


#: One shared instance — it holds no state, so every session reusing it is
#: indistinguishable from getting its own.
NULL_PROVISIONING_STRATEGY = NullProvisioningStrategy()


@dataclass(frozen=True)
class ProvisioningPlan:
    """The static half of a session's compile-time provisioning.

    Injected into a `BuildSession` at connect exactly as its `ValidationPlan`
    is, and chosen by `app/build_provisioning_selection.py` from the same
    panel resolution the connection already performed. The session never
    resolves one itself, and `BuildService` never reaches for a global
    registry mid-action.
    """

    strategy: ProvisioningStrategy
    #: Where this plan came from, for logging only. Never sent to a client.
    source: str = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ProvisioningStrategy):
            raise ValueError(f"not a ProvisioningStrategy: {self.strategy!r}")

    def describe(self) -> str:
        return f"provisioning={type(self.strategy).__name__} source={self.source}"


def default_provisioning_plan() -> ProvisioningPlan:
    """The plan a session gets when nothing resolved one for it."""
    return ProvisioningPlan(strategy=NULL_PROVISIONING_STRATEGY)


#: How a registered scenario builds its strategy. A zero-argument callable
#: returning a fresh strategy — deliberately not a bare class requirement, so
#: a registration may bind configuration without the registry knowing what
#: configuration is.
ProvisioningStrategyFactory = Callable[[], ProvisioningStrategy]


class ProvisioningStrategyRegistry:
    """The `scenario_id -> ProvisioningStrategy` table. Construction does no I/O.

    The same table shape the rest of the project uses (`PanelRegistry`,
    `ScenarioRegistry`, `ValidationStrategyRegistry`): one explicit dict
    lookup, never an `if scenario_id == ...` chain and never a dynamic
    import.
    """

    def __init__(
        self, entries: Mapping[str, ProvisioningStrategyFactory] | None = None
    ) -> None:
        self._factories: dict[str, ProvisioningStrategyFactory] = {}
        for scenario_id, factory in (entries or {}).items():
            self.register(scenario_id, factory)

    def register(self, scenario_id: str, factory: ProvisioningStrategyFactory) -> None:
        """Bind one experiment to its provisioner. Refuses to overwrite."""
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError(f"scenario id must be a non-empty string, got {scenario_id!r}")
        if not callable(factory):
            raise ValueError(f"provisioning factory for {scenario_id!r} must be callable")
        if scenario_id in self._factories:
            raise ValueError(f"a provisioning strategy is already registered for {scenario_id!r}")
        self._factories[scenario_id] = factory

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        """Every registered id, sorted — diagnostics and tests."""
        return tuple(sorted(self._factories))

    def registered(self, scenario_id: str) -> bool:
        return scenario_id in self._factories

    def create(self, scenario_id: str | None) -> ProvisioningStrategy:
        """A fresh strategy for this experiment, or the no-op default.

        `None` (a session with no resolved package) and an unregistered id
        take the same path: there is nothing declared to provision, so the
        materialized sketch compiles exactly as it is.
        """
        factory = self._factories.get(scenario_id) if scenario_id else None
        if factory is None:
            return NULL_PROVISIONING_STRATEGY
        strategy = factory()
        if not isinstance(strategy, ProvisioningStrategy):
            raise ValueError(
                f"the provisioning factory registered for {scenario_id!r} did not build a "
                f"ProvisioningStrategy, got {type(strategy).__name__}"
            )
        return strategy


def build_provisioning_registry(
    entries: Iterable[tuple[str, ProvisioningStrategyFactory]],
) -> ProvisioningStrategyRegistry:
    """A registry containing exactly the rows given — a test's own table,
    with no dependency on which panels this deployment happens to ship."""
    return ProvisioningStrategyRegistry(dict(entries))
