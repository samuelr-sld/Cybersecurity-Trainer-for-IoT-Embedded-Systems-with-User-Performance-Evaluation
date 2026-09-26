"""A remediation requirement a machine can check (Phase B8).

    panel.json remediation.criterion          courseware, authored
              |
              v
    app/panels/models.py::AuthorizationCriterion      the DECLARATION
              |
              v
    app/build_validation_selection.py                 the one converter
              |
              v
    this module's AuthorizationCriterion              the engine's VIEW
              |
              v
    ValidationStrategy.validate(context)              what actually runs

WHY THE SAME SHAPE EXISTS TWICE, AND WHY THAT IS NOT DUPLICATION BY ACCIDENT.
`app/build/` may not import `app.panels` and `app/panels/` may not import
`app.build` — both directions are asserted statically
(`tests/test_build_pipeline_b7.py`, `tests/test_panel_packages.py`), because
the generic engine must stay panel-agnostic and the courseware layer must stay
unable to execute anything. A type both sides name therefore cannot live on
either side, and this codebase already answers that the same way for the prose
half of the very same declaration: `RemediationDeclaration` becomes
`RemediationSpec` through one mechanical conversion in the one module allowed
to see both. B8 extends that established projection rather than inventing a
second mechanism, a shared package, or an import exception.

THE ENGINE'S VIEW IS DELIBERATELY NARROWER. The courseware declaration is
where a value is VALIDATED — topic shapes, port ranges, env-name patterns,
the rule that an unauthorized command may never be declared acceptable, the
requirement that a criterion distinguish an accepted command from a rejected
one. None of that is repeated here, because a criterion only reaches this
module after `app/panels/models.py` built one, and re-deriving the rules would
create exactly the second source of truth the projection exists to avoid. What
this layer re-checks is only what it must not assume: that it was handed the
right types.

STILL DATA, STILL NOT CODE. No field here is evaluated, imported, formatted
into a command, or turned into a path — a payload is assembled from a command
word, a separator and a provisioned token, all plain strings. And there is no
secret: an identity carries the NAME of the environment variable a deployment
provisions its password into, never a password.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceChannel(str, Enum):
    """Where a validator observes what the remediated device actually did."""

    MQTT_STATE_TOPIC = "mqtt_state_topic"
    DEVICE_SERIAL = "device_serial"


class CommandAuthorization(str, Enum):
    """Which declared identity issues a probe's command.

    Both are authenticated to the broker; the difference is entitlement.
    """

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"


class TokenUse(str, Enum):
    """What per-command authorization evidence a probe attaches, if any."""

    NONE = "none"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class LabIdentity:
    """One synthetic lab client, addressed by account name and secret LOCATION.

    `password_env` names an environment variable; it is never a password.
    Resolving it is `app/config.py::lab_secret`'s job, and a variable that is
    not provisioned makes a validator UNAVAILABLE rather than letting it
    connect with a blank credential.
    """

    identity_id: str
    username: str
    password_env: str

    def __post_init__(self) -> None:
        for name in ("identity_id", "username", "password_env"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"lab identity {name} must be a non-empty string")


@dataclass(frozen=True)
class AuthorizationProbe:
    """One command a validator sends, and what a FIXED device must do with it."""

    probe_id: str
    description: str
    authorization: CommandAuthorization
    command: str
    token: TokenUse
    expect_accepted: bool
    expect_state: str

    def __post_init__(self) -> None:
        if not isinstance(self.probe_id, str) or not self.probe_id.strip():
            raise ValueError("probe id must be a non-empty string")
        if not isinstance(self.authorization, CommandAuthorization):
            raise ValueError(f"probe {self.probe_id}: authorization must be a CommandAuthorization")
        if not isinstance(self.token, TokenUse):
            raise ValueError(f"probe {self.probe_id}: token must be a TokenUse")
        for name in ("command", "expect_state"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"probe {self.probe_id}: {name} must be a non-empty string")
        if not isinstance(self.expect_accepted, bool):
            raise ValueError(f"probe {self.probe_id}: expect_accepted must be a boolean")

    def payload(self, *, valid_token: str, invalid_token: str, separator: str) -> str:
        """The exact bytes this probe publishes, assembled from plain values.

        Three strings joined — no template, no format specification from the
        courseware, and nothing evaluated. A probe that attaches no token
        publishes the bare command, which is precisely what the VULNERABLE
        firmware obeys and what a remediated one must ignore.
        """
        if self.token is TokenUse.NONE:
            return self.command
        token = valid_token if self.token is TokenUse.VALID else invalid_token
        return f"{self.command}{separator}{token}"


@dataclass(frozen=True)
class AuthorizationCriterion:
    """The engine's view of a per-command authorization remediation.

    See the module docstring for why this mirrors
    `app/panels/models.py::AuthorizationCriterion` rather than importing it.
    """

    criterion_id: str
    broker_host: str
    broker_port: int
    control_topic: str
    state_topic: str
    accepted_commands: tuple[str, ...]
    observed_states: tuple[str, ...]
    authorized: LabIdentity
    unauthorized: LabIdentity
    token_env: str
    token_separator: str
    invalid_token: str
    evidence: EvidenceChannel
    probes: tuple[AuthorizationProbe, ...]
    settle_seconds: float = 2.0
    response_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.criterion_id, str) or not self.criterion_id.strip():
            raise ValueError("criterion id must be a non-empty string")
        if not isinstance(self.evidence, EvidenceChannel):
            raise ValueError(f"criterion {self.criterion_id}: evidence must be an EvidenceChannel")
        for name in ("authorized", "unauthorized"):
            if not isinstance(getattr(self, name), LabIdentity):
                raise ValueError(f"criterion {self.criterion_id}: {name} must be a LabIdentity")
        for probe in self.probes:
            if not isinstance(probe, AuthorizationProbe):
                raise ValueError(f"criterion {self.criterion_id}: not an AuthorizationProbe")
        if not self.probes:
            raise ValueError(f"criterion {self.criterion_id}: no probes to run")

    def identity_for(self, probe: AuthorizationProbe) -> LabIdentity:
        """Which declared identity publishes this probe's command."""
        return (
            self.authorized
            if probe.authorization is CommandAuthorization.AUTHORIZED
            else self.unauthorized
        )

    @property
    def secret_env_names(self) -> tuple[str, ...]:
        """Every environment variable a deployment must provision, sorted.

        What `unavailable_reason` reports by NAME when something is missing,
        so an instructor is told which variable to set rather than being told
        the check is simply unavailable.
        """
        return tuple(
            sorted(
                {
                    self.token_env,
                    self.authorized.password_env,
                    self.unauthorized.password_env,
                }
            )
        )

    def describe(self) -> str:
        """One log/evidence line. Names no secret and no secret's value."""
        return (
            f"criterion={self.criterion_id} broker={self.broker_host}:{self.broker_port} "
            f"control={self.control_topic} evidence={self.evidence.value} "
            f"probes={len(self.probes)}"
        )
