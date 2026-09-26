"""Which validator a declared scenario resolves to (Phase B7).

    package.scenario_id  ->  ValidationStrategyRegistry  ->  ValidationStrategy

THE SAME TABLE SHAPE THE REST OF THE PROJECT USES. `PanelRegistry` maps a MAC
to a panel and `ScenarioRegistry` (`app/scenarios/registry.py`) maps a
`scenario_id` to a `Scenario`; this maps the same `scenario_id` to the
validator for that experiment's remediation. One explicit dict lookup, never
an `if scenario_id == ...` chain, never a dynamic import, never `eval`/`exec`,
and never a module path built from an id — a registered factory is a callable
someone wrote in Python and registered on purpose.

A FRESH INSTANCE PER LOOKUP, like `ScenarioRegistry.create`: a validator may
accumulate per-attempt state, and two Build Mode sessions on the same panel
must not be able to observe each other's.

AN UNREGISTERED ID IS NOT AN ERROR HERE, WHICH IS WHERE THIS DELIBERATELY
DIFFERS FROM `ScenarioRegistry`. An unknown `scenario_id` there raises,
because a Hack session must run *some* experiment and silently falling back
to another panel's would be wrong. Here the fallback is not another panel's
check — it is `DeclaredRequirementValidator`, which refuses to run anything
at all and says why. "No validator is registered for this experiment" and
"this experiment's validator declines to run" lead to the same honest
refusal, so raising would only turn a truthful message into a stack trace.

`build_default_validation_registry()` REGISTERED NOTHING UNTIL PHASE B8, AND
THAT WAS THE TRUTH AT THE TIME: no panel declared a machine-checkable
remediation criterion, so registering one against a validator that could not
validate would have put a panel's name in the engine while adding no
behaviour. B8 is the phase that defines Panel 1's criterion
(`backend/panels/smart-home-mqtt-control/panel.json`), so this table now has
exactly ONE row — `smart-home-mqtt-control ->
SmartHomeAuthorizationValidator` — and the other four panels still resolve to
the default, because they still declare prose only.

THAT ROW IS NOT A PANEL BRANCH. It is a registration, in the same table shape
`PanelRegistry` and `ScenarioRegistry` use: the package names its experiment,
this maps that id to a validator, and both are data. Nothing on the request
path asks which panel is attached — `app/build/service.py` has no mention of
MQTT, a broker or a panel id, and the test suite asserts that statically.
Registering a second panel is adding a row, not changing a mechanism.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from app.build.validation.smart_home import SmartHomeAuthorizationValidator
from app.build.validation.strategy import (
    DeclaredRequirementValidator,
    ValidationStrategy,
)

#: The experiment Panel 1 runs, as its package declares it. Named once here
#: so the registration and the tests refer to the same constant rather than
#: repeating a string literal; it is a KEY into this table, never a branch.
SMART_HOME_MQTT_CONTROL = "smart-home-mqtt-control"

#: How a registered experiment builds its validator. A zero-argument callable
#: returning a fresh strategy — deliberately not a class, so a registration
#: may bind configuration without the registry knowing what configuration is.
ValidationStrategyFactory = Callable[[], ValidationStrategy]


class ValidationStrategyRegistry:
    """The `scenario_id -> ValidationStrategy` table. Construction does no I/O."""

    def __init__(
        self, entries: Mapping[str, ValidationStrategyFactory] | None = None
    ) -> None:
        self._factories: dict[str, ValidationStrategyFactory] = {}
        for scenario_id, factory in (entries or {}).items():
            self.register(scenario_id, factory)

    def register(self, scenario_id: str, factory: ValidationStrategyFactory) -> None:
        """Bind one experiment to its validator. Refuses to overwrite.

        A duplicate id is a registration mistake — two validators for one
        experiment, with whichever ran last silently winning — so it is
        rejected at wiring time rather than discovered from a wrong verdict.
        """
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError(f"scenario id must be a non-empty string, got {scenario_id!r}")
        if not callable(factory):
            raise ValueError(f"validator factory for {scenario_id!r} must be callable")
        if scenario_id in self._factories:
            raise ValueError(f"a validator is already registered for {scenario_id!r}")
        self._factories[scenario_id] = factory

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        """Every registered id, sorted — diagnostics and tests."""
        return tuple(sorted(self._factories))

    def registered(self, scenario_id: str) -> bool:
        return scenario_id in self._factories

    def create(self, scenario_id: str | None) -> ValidationStrategy:
        """A fresh validator for this experiment, or the honest default.

        `None` (a session with no resolved package) takes the same path as
        an unregistered id: there is no experiment to look up, so there is
        no validator but the one that declines.
        """
        factory = self._factories.get(scenario_id) if scenario_id else None
        if factory is None:
            return DeclaredRequirementValidator()
        strategy = factory()
        if not isinstance(strategy, ValidationStrategy):
            raise ValueError(
                f"the validator registered for {scenario_id!r} did not build a "
                f"ValidationStrategy, got {type(strategy).__name__}"
            )
        return strategy


#: The experiments that have a real validator today. One row (see the module
#: docstring). Each value is a factory, so `create` builds a FRESH validator
#: per session and no two sessions share one.
BUILT_IN_VALIDATORS: tuple[tuple[str, ValidationStrategyFactory], ...] = (
    (SMART_HOME_MQTT_CONTROL, SmartHomeAuthorizationValidator),
)


def build_default_validation_registry(
    entries: Iterable[tuple[str, ValidationStrategyFactory]] | None = None,
) -> ValidationStrategyRegistry:
    """The process-wide table — see the module docstring.

    `entries` REPLACES the built-in rows rather than adding to them, which is
    what lets a test build a registry containing exactly the validators it
    wants (including an empty one) without reaching into a global. Omitting
    the argument gives the shipped table; passing `()` gives an empty one.
    """
    rows = BUILT_IN_VALIDATORS if entries is None else tuple(entries)
    return ValidationStrategyRegistry(dict(rows))


#: The registry the Build Mode connection lifecycle reads.
default_validation_registry = build_default_validation_registry()
