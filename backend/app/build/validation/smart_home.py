"""Panel 1's remediation check: per-command authorization (Phase B8).

    BuildService.validate_workspace          the generic engine: gates, status,
              |                              events, evidence rows
              v
    SmartHomeAuthorizationValidator          THIS module: what "fixed" means
              |                              for one panel's experiment
              v
    AuthorizationEvidence                    mqtt_evidence.py -> Mosquitto -> ESP32
              |
              v
    ValidationResult                         SUCCESS / FAILURE / ERROR / NOT_RUN

WHY A PANEL'S CHECK LIVES HERE AND NOT IN `BuildService`. The engine already
owns the lifecycle — the compile/flash gates, `validation_status`, the
`BuildEvent`s, the `BuildAttemptRecord` every metric reads — and it must keep
owning exactly that and no more. The moment it also knew Panel 1 is MQTT, the
next panel's check would go in beside it and the generic engine would become a
switch over panels. So this module holds one panel's security meaning, it is
reached only through the `scenario_id -> validator` table
(`app/build/validation/registry.py`), and `app/build/service.py` contains no
mention of a broker, a topic, a motor or a panel id — asserted statically by
the test suite.

WHAT IT ASSERTS, AND WHERE THAT COMES FROM. Nothing here decides what a
correct fix is. Every fact it uses — the control topic, the accepted commands,
the authorized and unauthorized lab identities, which probe must be obeyed and
which must be ignored, what state to expect afterwards, how long to wait — is
read off the `AuthorizationCriterion` the panel's own package declares
(`backend/panels/smart-home-mqtt-control/panel.json`). Change the courseware
and this check changes with it; there is no topic string, credential, command
word or timeout literal in this file.

    A COMMAND THAT MUST BE OBEYED   proves the fix did not simply break the
                                    device. Firmware that ignores all MQTT
                                    fails here, which is why "reject
                                    everything" is not a way to pass.

    A COMMAND THAT MUST BE IGNORED  proves the vulnerability is actually
                                    closed — issued by a client that IS
                                    authenticated to the broker, because
                                    authentication was never the missing
                                    control.

    AT LEAST ONE OBSERVED CHANGE    proves acceptance was actually witnessed
                                    rather than inferred from a device that
                                    happened to already be in the expected
                                    state.

FOUR OUTCOMES, AND THREE OF THEM ARE NOT "PASS".

    SUCCESS      every probe behaved as the criterion declares, and at least
                 one acceptance was observed as a real state change.
    FAILURE      a probe did not. An unauthorized command was obeyed (still
                 vulnerable), or an authorized one was not (the fix broke
                 legitimate control). A real, negative verdict about the
                 student's firmware.
    ERROR        the CHECK broke — no broker, a rejected lab credential, a
                 lost link, no state ever observed. The firmware was never
                 judged, so this is emphatically not a failure of it.
    NOT_RUN      reached only through `unavailable_reason`: there is nothing
                 to run here (no criterion, no client library, no provisioned
                 lab fixtures). `BuildService` refuses the request before any
                 status moves and records no attempt.

NOTHING IS SIMULATED AND NOTHING IS ASSUMED. There is no fake device, no
canned verdict and no path from "the flash succeeded" to SUCCESS. If the real
broker and the real panel are not reachable, this says so.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, Callable, Mapping

from app import config
from app.build.validation.criteria import (
    AuthorizationCriterion,
    AuthorizationProbe,
    EvidenceChannel,
    TokenUse,
)
from app.build.validation.models import ValidationContext, ValidationResult
from app.build.validation.mqtt_evidence import (
    AuthorizationEvidence,
    EvidenceChannelError,
    available as mqtt_available,
    open_mqtt_evidence,
)

logger = logging.getLogger(__name__)

#: How a caller supplies a different evidence channel — a test double, or a
#: later phase's serial channel. Takes the criterion and the resolved lab
#: fixtures, and returns an OPEN channel or raises `EvidenceChannelError`.
EvidenceFactory = Callable[
    [AuthorizationCriterion, Mapping[str, str]], AuthorizationEvidence
]

#: The evidence channels this validator knows how to read. A criterion naming
#: any other one is honestly unavailable rather than silently approximated.
SUPPORTED_EVIDENCE = (EvidenceChannel.MQTT_STATE_TOPIC,)


@dataclasses.dataclass(frozen=True)
class _CleanupAttempt:
    """What `_restore` actually managed to establish about the panel.

    `attempted` is always True when one of these exists — `_restore` only
    runs when a run did not itself reach and pass the declared restore
    probe. `confirmed` is true only when the panel was subsequently observed
    on its evidence topic at the restore probe's declared `expect_state`;
    `error is None` says only that the broker accepted the publish, which is
    NOT the same fact — see `_restore`'s docstring for why conflating them
    is exactly the gap this type exists to close.
    """

    attempted: bool
    error: str | None
    confirmed: bool
    observed_state: str | None


class SmartHomeAuthorizationValidator:
    """Checks that a remediated panel authorizes motor commands per command.

    `evidence_factory` and `secret_reader` are injected for the reason every
    adapter in this codebase is: a test drives the full decision table from
    plain values, with no broker, no credentials and no board. Production
    passes neither, and gets the real MQTT channel and the real
    `TRAINER_LAB_*` lookup.

    RESTORATION IS UNCONDITIONAL, NOT PROBE-6-SHAPED. The six declared probes
    end with an authorized STOP by courseware convention (`restore-stop`),
    but a probe earlier in the run can fail — or the evidence channel can
    break — before that probe ever sends. Either way the physical motor may
    still be running, so `_probe_all` always attempts one authorized STOP,
    using that same final probe's identity and valid token, whenever the run
    did not itself already reach and pass it. A run that passes all six never
    sends a second one (see `_run_probes`'s `reached_restore`).

    A PUBLISH THE BROKER ACCEPTED IS NOT A CONFIRMED STOP. The committed
    vulnerable baseline only recognises the bare command word "STOP" and
    silently ignores the tokenized payload the restore probe sends, so
    `evidence.publish` can raise nothing while the motor keeps running. For
    that reason `_restore` also reads back the same evidence topic every
    probe already reads (`evidence.await_state`) and reports whether the
    panel was actually observed at the restore probe's declared
    `expect_state` afterwards. The cleanup attempt — and its confirmation,
    or lack of one — can itself fail without ever touching the verdict; it
    is only ever added to a result as extra detail
    (`cleanup_stop_attempted`/`cleanup_stop_error`/`cleanup_confirmed`/
    `cleanup_observed_state`/`cleanup_outcome`) — never as a replacement for
    the verdict the probes already reached.
    """

    def __init__(
        self,
        evidence_factory: EvidenceFactory | None = None,
        secret_reader: Callable[[str], str] | None = None,
    ) -> None:
        self._open_evidence = (
            evidence_factory if evidence_factory is not None else open_mqtt_evidence
        )
        self._secret = secret_reader if secret_reader is not None else config.lab_secret

    # --- availability -------------------------------------------------------

    def unavailable_reason(self, context: ValidationContext) -> str | None:
        """Why this check cannot run here, or None when it can.

        Asked BEFORE anything starts, so every answer here costs the student
        nothing: no `RUNNING` status, no event, and no `BuildAttemptRecord`
        that AID would count. All three reasons are deployment facts, not
        facts about the firmware.
        """
        criterion = self._criterion(context)
        if criterion is None:
            return (
                "this panel declares no machine-checkable remediation criterion, so there "
                "is nothing to check"
            )
        if criterion.evidence not in SUPPORTED_EVIDENCE:
            return (
                f"this panel's remediation is observed through the "
                f"{criterion.evidence.value!r} channel, which this validator cannot read"
            )
        if self._open_evidence is open_mqtt_evidence and not mqtt_available():
            return (
                "the MQTT client library (paho-mqtt) is not installed in this backend, so "
                "the device's state topic cannot be observed"
            )
        missing = self._missing_fixtures(criterion)
        if missing:
            return (
                "this deployment has not provisioned the training-lab credentials this "
                "check needs; set " + ", ".join(missing)
            )
        return None

    def _criterion(self, context: ValidationContext) -> AuthorizationCriterion | None:
        remediation = context.remediation
        if remediation is None:
            return None
        return remediation.criterion

    def _fixtures(self, criterion: AuthorizationCriterion) -> dict[str, str]:
        """Every provisioned lab value this criterion names, by variable name."""
        return {name: self._secret(name) for name in criterion.secret_env_names}

    def _missing_fixtures(self, criterion: AuthorizationCriterion) -> tuple[str, ...]:
        return tuple(
            sorted(name for name, value in self._fixtures(criterion).items() if not value)
        )

    # --- the check ----------------------------------------------------------

    async def validate(self, context: ValidationContext) -> ValidationResult:
        """Run the declared probes against the real panel and report what happened.

        The MQTT work is blocking, so it runs on a worker thread — the same
        reason `app/hardware/serial_transport.py` parks pyserial off the event
        loop. A `BuildService` handling other sessions is never stalled by a
        settle window.
        """
        criterion = self._criterion(context)
        if criterion is None:
            # Unreachable through `BuildService`, which asks
            # `unavailable_reason` first. Answered rather than raised so a
            # direct caller gets the honest outcome instead of a traceback.
            return ValidationResult.not_run(
                "this panel declares no machine-checkable remediation criterion"
            )
        return await asyncio.to_thread(self._run, criterion, context)

    def _run(
        self, criterion: AuthorizationCriterion, context: ValidationContext
    ) -> ValidationResult:
        """The whole check, synchronously. Returns a verdict; never raises."""
        # Read once, so a check cannot observe a fixture changing mid-run and
        # so an injected reader is called a predictable number of times.
        fixtures = self._fixtures(criterion)
        try:
            evidence = self._open_evidence(criterion, fixtures)
        except EvidenceChannelError as error:
            return ValidationResult.error(
                f"the panel's evidence channel is not reachable: {error}",
                criterion=criterion.criterion_id,
                evidence=criterion.evidence.value,
                stage="open",
            )
        try:
            return self._probe_all(criterion, evidence, context, fixtures)
        except EvidenceChannelError as error:
            return ValidationResult.error(
                f"the check lost the panel's evidence channel: {error}",
                criterion=criterion.criterion_id,
                evidence=criterion.evidence.value,
                stage="probe",
            )
        finally:
            evidence.close()

    def _probe_all(
        self,
        criterion: AuthorizationCriterion,
        evidence: AuthorizationEvidence,
        context: ValidationContext,
        fixtures: Mapping[str, str],
    ) -> ValidationResult:
        initial = evidence.await_any_state(criterion.response_timeout_seconds)
        if initial is None:
            # The device never reported a state at all. It may not be running
            # the flashed firmware, may not have joined the training network,
            # or may not be powered — none of which is a judgement about the
            # student's authorization logic. No probe was sent, so there is
            # nothing for a cleanup command to restore.
            return ValidationResult.error(
                "the panel never reported a state on its evidence topic, so the check "
                "could not observe whether any command was obeyed",
                criterion=criterion.criterion_id,
                state_topic=criterion.state_topic,
                stage="baseline",
            )

        try:
            result, reached_restore = self._run_probes(criterion, evidence, context, fixtures)
        except EvidenceChannelError as error:
            # The channel broke mid-run. The panel may be mid-command, so it
            # is cleaned up — and that cleanup's own outcome (confirmed or
            # not) is attached to the ERROR verdict this produces, exactly as
            # it would be for a probe FAILURE below.
            cleanup = self._restore(criterion, evidence, fixtures)
            return self._with_cleanup_outcome(
                ValidationResult.error(
                    f"the check lost the panel's evidence channel: {error}",
                    criterion=criterion.criterion_id,
                    evidence=criterion.evidence.value,
                    stage="probe",
                ),
                cleanup,
            )
        except Exception:
            # Some other exception broke the check instead of producing a
            # verdict. Restoration must not depend on which exception this
            # is, so the panel is cleaned up here exactly as above, then the
            # original exception still propagates unchanged: a broken check
            # must never be reported as a verdict.
            self._restore(criterion, evidence, fixtures)
            raise

        if reached_restore:
            # The run itself reached and passed the final (restore) probe, so
            # the panel is already at rest — sending a second STOP would be
            # pure noise on the wire.
            return result
        cleanup = self._restore(criterion, evidence, fixtures)
        return self._with_cleanup_outcome(result, cleanup)

    def _run_probes(
        self,
        criterion: AuthorizationCriterion,
        evidence: AuthorizationEvidence,
        context: ValidationContext,
        fixtures: Mapping[str, str],
    ) -> tuple[ValidationResult, bool]:
        """Send every declared probe in order and return the verdict.

        The bool is whether the loop ran to completion — which, since the
        declared `restore-stop` probe is always `criterion.probes[-1]`, is
        true only when that final authorized STOP was itself sent and
        obeyed. `_probe_all` uses it to decide whether a cleanup restoration
        is still needed.
        """
        records: list[dict[str, Any]] = []
        observed_acceptances = 0
        token = fixtures.get(criterion.token_env, "")

        for probe in criterion.probes:
            before = evidence.state()
            payload = probe.payload(
                valid_token=token,
                invalid_token=criterion.invalid_token,
                separator=criterion.token_separator,
            )
            evidence.publish(
                criterion.identity_for(probe).identity_id, criterion.control_topic, payload
            )

            if probe.expect_accepted:
                after = evidence.await_state(
                    probe.expect_state, criterion.response_timeout_seconds
                )
            else:
                # Nothing should happen, so there is no event to wait FOR:
                # the only honest check is to let the window in which an
                # obeyed command would have acted pass, then look.
                evidence.settle(criterion.settle_seconds)
                after = evidence.state()

            passed = after == probe.expect_state
            witnessed = passed and probe.expect_accepted and before != after
            if witnessed:
                observed_acceptances += 1
            records.append(
                self._record(probe, before, after, passed, witnessed)
            )
            if not passed:
                return (
                    ValidationResult.failure(
                        self._failure_message(probe, before, after),
                        criterion=criterion.criterion_id,
                        failed_probe=probe.probe_id,
                        probes=records,
                        firmware_fingerprint=context.firmware_fingerprint,
                        evidence=criterion.evidence.value,
                    ),
                    False,
                )

        if observed_acceptances == 0:
            # Every probe matched its expected state, but no authorized
            # command was ever seen to CHANGE anything — the device may
            # simply be ignoring MQTT entirely, which passes every rejection
            # probe for the wrong reason. Reported as a failure of the
            # remediation, because a device that obeys nothing has not
            # implemented per-command authorization.
            return (
                ValidationResult.failure(
                    "no authorized command was observed to change the panel's state, so "
                    "the firmware never demonstrated that it still obeys a properly "
                    "authorized command",
                    criterion=criterion.criterion_id,
                    probes=records,
                    firmware_fingerprint=context.firmware_fingerprint,
                    evidence=criterion.evidence.value,
                ),
                True,
            )

        return (
            ValidationResult.success(
                "the panel obeyed every properly authorized command and ignored every "
                "unauthorized one",
                criterion=criterion.criterion_id,
                probes=records,
                observed_acceptances=observed_acceptances,
                firmware_fingerprint=context.firmware_fingerprint,
                evidence=criterion.evidence.value,
            ),
            True,
        )

    def _restore(
        self,
        criterion: AuthorizationCriterion,
        evidence: AuthorizationEvidence,
        fixtures: Mapping[str, str],
    ) -> "_CleanupAttempt":
        """Best-effort authorized STOP, sent regardless of how the run ended,
        then a read of the same evidence topic every probe already reads to
        find out whether the panel actually obeyed it.

        Reuses the declared `restore-stop` probe's OWN identity, token and
        declared `expect_state` (`criterion.probes[-1]`) — this is not a
        second command the validator invents, it is that probe's payload,
        sent and then CONFIRMED independently of whether the run ever
        reached it. A publish the broker accepted is not proof of anything:
        the committed vulnerable baseline only recognises the bare command
        word "STOP" and silently drops a tokenized payload, so the motor can
        stay RUNNING even though `evidence.publish` raised nothing. Returns a
        `_CleanupAttempt`; never raises, because a cleanup failure — or an
        unconfirmed one — must never replace the verdict the probes already
        reached.
        """
        restore_probe = criterion.probes[-1]
        token = fixtures.get(criterion.token_env, "")
        payload = restore_probe.payload(
            valid_token=token,
            invalid_token=criterion.invalid_token,
            separator=criterion.token_separator,
        )
        try:
            evidence.publish(
                criterion.identity_for(restore_probe).identity_id,
                criterion.control_topic,
                payload,
            )
        except Exception as error:  # noqa: BLE001 - cleanup must never raise
            logger.warning(
                "cleanup restoration STOP failed for criterion %s: %s",
                criterion.criterion_id,
                error,
            )
            return _CleanupAttempt(
                attempted=True, error=str(error), confirmed=False, observed_state=None
            )

        observed = evidence.await_state(
            restore_probe.expect_state, criterion.response_timeout_seconds
        )
        confirmed = observed == restore_probe.expect_state
        if not confirmed:
            # The publish did not raise, so the broker accepted it — but
            # accepted-by-the-broker and obeyed-by-the-firmware are exactly
            # the two facts this whole validator exists to tell apart. A
            # baseline that ignores the tokenized STOP leaves this False.
            logger.warning(
                "cleanup restoration STOP for criterion %s was not confirmed: the "
                "panel reported %s, expected %s",
                criterion.criterion_id,
                observed,
                restore_probe.expect_state,
            )
        return _CleanupAttempt(
            attempted=True, error=None, confirmed=confirmed, observed_state=observed
        )

    @staticmethod
    def _with_cleanup_outcome(
        result: ValidationResult, cleanup: "_CleanupAttempt"
    ) -> ValidationResult:
        """Attach what the cleanup restoration did, without touching the verdict.

        Only ever adds detail keys — `outcome` and `message` are exactly what
        the probes (or the lost-channel error) already decided, so a cleanup
        failure, or a cleanup the panel never confirmed, can be seen by an
        evaluator but can never masquerade as, or override, the original
        security result. `cleanup_confirmed`/`cleanup_outcome` are the
        explicit tri-state answer: a run that never needed cleanup carries
        none of these keys at all (see `_probe_all`'s `reached_restore`
        short-circuit); one that did carries `cleanup_outcome` of either
        `"confirmed"` or `"attempted_unconfirmed"` — never a silent
        `"succeeded"` inferred from the publish alone.
        """
        details = dict(result.details)
        details["cleanup_stop_attempted"] = cleanup.attempted
        details["cleanup_stop_error"] = cleanup.error
        details["cleanup_confirmed"] = cleanup.confirmed
        details["cleanup_observed_state"] = cleanup.observed_state
        details["cleanup_outcome"] = (
            "confirmed" if cleanup.confirmed else "attempted_unconfirmed"
        )
        return dataclasses.replace(result, details=details)

    @staticmethod
    def _record(
        probe: AuthorizationProbe,
        before: str | None,
        after: str | None,
        passed: bool,
        witnessed: bool,
    ) -> dict[str, Any]:
        """One probe's evidence row — what was sent, and what the device did.

        Diagnostic evidence for an evaluator, exactly as
        `ValidationResult.details` documents. It names the probe, the
        identity class and the token class, and deliberately carries NO
        credential, token value or payload: a stored evidence row must be
        safe to show an instructor.
        """
        return {
            "probe_id": probe.probe_id,
            "authorization": probe.authorization.value,
            "command": probe.command,
            "token": probe.token.value,
            "expected_accepted": probe.expect_accepted,
            "expected_state": probe.expect_state,
            "state_before": before,
            "state_after": after,
            "passed": passed,
            "state_change_observed": witnessed,
        }

    @staticmethod
    def _failure_message(
        probe: AuthorizationProbe, before: str | None, after: str | None
    ) -> str:
        """Why this probe failed, in the student's terms.

        The two failures mean opposite things and must never read alike: one
        says the vulnerability is still there, the other says the fix went
        too far.
        """
        if probe.expect_accepted:
            return (
                f"the panel did not obey a properly authorized {probe.command} "
                f"({probe.probe_id}): it reported {after or 'nothing'} rather than "
                f"{probe.expect_state}, so the remediation blocks legitimate control"
            )
        detail = "" if probe.token is TokenUse.NONE else " carrying an invalid token"
        return (
            f"the panel obeyed an unauthorized {probe.command}{detail} ({probe.probe_id}): "
            f"its state moved from {before or 'unknown'} to {after or 'unknown'}, so an "
            "authenticated client can still actuate the motor without per-command "
            "authorization"
        )
