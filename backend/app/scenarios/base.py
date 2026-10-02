"""The scenario interface — the seam between commands and the target.

This is the boundary the whole phase is built around:

    Command Router -> Command Handler -> Scenario -> (simulated) Target

A command handler holds no knowledge of the Environmental Monitoring target.
It parses the student's arguments, calls one method on this interface, and
renders the returned `ScenarioOutcome` as terminal output. That indirection is
what lets a future PHYSICAL ESP32 be dropped in behind the same interface:

    Command Router -> Command Handler -> Scenario -> Physical Target

without touching the router, the handlers, the protocol, or the terminal.

TRANSPORT INDEPENDENCE. A `Scenario` returns plain data: text lines (no line
terminators, no ANSI, no framing) and structured events. It never imports
FastAPI, xterm, or the WebSocket layer, and it never formats a wire frame.
The transport decides how lines become bytes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.scenarios.events import ScenarioEvent
from app.scenarios.state import ScenarioState

# Exit statuses match the shell convention the router already uses, so the
# evaluation phase can read success/usage/failure off a command uniformly.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class ScenarioOutcome:
    """The result of one scenario operation.

    `lines` is terminal-ready text without line terminators — the transport
    owns CRLF. `success` and `exit_code` classify the operation for the
    handler and, later, the evaluator. `events` are the domain events this
    single call emitted; the engine also retains them in its own ordered log.

    `fields_correct` is the Phase 2E.2 Reconnaissance Efficiency (RE) seam.
    RE needs to know whether a RECOGNIZED command's required target-fact
    fields (host, port, topic, ...) were correct — a genuinely different
    question from `success`/`exit_code`, which a scenario reserves for
    tool-realistic behaviour. The two can legitimately disagree: a real
    `nmap` against a reachable host with the WRONG port is a successful scan
    that correctly reports the port closed (`success=True`), even though the
    port argument itself was wrong. Leaving `fields_correct` at its default
    (`None`) means "not applicable / not assessed" — the right answer for a
    tool with no target-fact fields to get wrong (`esptool.py`, `strings`,
    `grep`), and the metric layer falls back to `exit_code == 0` for it. A
    scenario sets it explicitly only where it already knows the ground truth
    and `success` alone cannot distinguish "reached the right target" from
    "reached A target with a wrong required field". This field carries no
    metric arithmetic — it is one more fact the scenario reports, exactly
    like `events`; see `app/metrics/re.py` for the computation that reads it.
    """

    lines: tuple[str, ...] = ()
    success: bool = True
    exit_code: int = EXIT_OK
    events: tuple[ScenarioEvent, ...] = ()
    fields_correct: bool | None = None

    @classmethod
    def ok(
        cls,
        *lines: str,
        events: tuple[ScenarioEvent, ...] = (),
        fields_correct: bool | None = None,
    ) -> "ScenarioOutcome":
        return cls(
            lines=lines,
            success=True,
            exit_code=EXIT_OK,
            events=events,
            fields_correct=fields_correct,
        )

    @classmethod
    def failed(
        cls,
        *lines: str,
        exit_code: int = EXIT_FAILURE,
        events: tuple[ScenarioEvent, ...] = (),
        fields_correct: bool | None = None,
    ) -> "ScenarioOutcome":
        return cls(
            lines=lines,
            success=False,
            exit_code=exit_code,
            events=events,
            fields_correct=fields_correct,
        )

    @classmethod
    def usage(cls, *lines: str) -> "ScenarioOutcome":
        return cls(lines=lines, success=False, exit_code=EXIT_USAGE, fields_correct=False)


def readout_row(
    row_id: str, label: str, value: str | None, *, revealed: bool
) -> dict[str, Any]:
    """One line of a scenario's own TARGET DEVICE readout.

    A scenario that has an activity target describes what the student may
    currently know about it as a list of these under `snapshot()["readout"]`,
    so the page renders rows without knowing what kind of device it is (a
    motor, a sensor, anything). `revealed` is the scenario's own gating: a
    fact the student has not discovered yet is sent as `value=None` and the page
    shows it as not yet discovered, rather than the page holding a per-device
    list of which facts to hide.
    """
    return {
        "id": row_id,
        "label": label,
        "value": value if revealed else None,
        "revealed": bool(revealed),
    }


class Scenario(ABC):
    """What a training target must be able to do, simulated or physical.

    Each method corresponds to one student action. Arguments are already
    parsed by the handler into `host` / `port` / `topic` / `message`; the
    scenario decides what that action does against the current target state.
    """

    #: Stable identifier for logs and for a future scenario selector.
    scenario_id: str = "scenario"

    @property
    @abstractmethod
    def state(self) -> ScenarioState:
        """The live per-session state (read-only for callers by convention)."""

    @property
    @abstractmethod
    def events(self) -> tuple[ScenarioEvent, ...]:
        """Every domain event emitted so far, in order — for Phase 2E."""

    @abstractmethod
    def extract_firmware(self) -> ScenarioOutcome:
        """Simulated authorized firmware read (Stage 1) — `esptool.py read_flash`."""

    @abstractmethod
    def analyze_firmware(self, search: str | None = None) -> ScenarioOutcome:
        """Analyze extracted firmware, revealing its configuration (Stage 2).

        Backs two real-tool command surfaces (Phase 2C): `strings` calls
        this with `search=None` for an unfiltered dump of every printable
        string; `grep <pattern>` calls it with the student's pattern for a
        filtered one. Neither handler interprets the strings themselves —
        WHICH strings exist, and whether any of them are meaningful, is
        entirely scenario data (see `EnvironmentalMonitoringScenario`'s own
        string table); this interface only guarantees every scenario can
        answer the same generic question. `None` performs the same
        unconditional configuration-recovery analysis this method has always
        performed, unchanged.
        """

    @abstractmethod
    def scan(self, host: str | None, port: int | None) -> ScenarioOutcome:
        """Simulated nmap against the network (Stage 3)."""

    @abstractmethod
    def observe(
        self, host: str | None, port: int | None, topic: str | None
    ) -> ScenarioOutcome:
        """Simulated mosquitto_sub against the broker (Stage 4)."""

    @abstractmethod
    def publish(
        self,
        host: str | None,
        port: int | None,
        topic: str | None,
        message: str | None,
    ) -> ScenarioOutcome:
        """Simulated mosquitto_pub — the spoofing attack (Stages 5-6)."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """A JSON-serialisable view of the target for Phase 2D to render.

        This is the Phase 2D integration point: the WebSocket layer will
        serialise this into a state frame so the frontend can draw the target
        device panel (temperature, humidity, pressure, status, spoof/attack
        flags). It is data only and contains no terminal text.

        A scenario with an activity target also puts a `readout` list in it
        (see `readout_row`): the rows its TARGET DEVICE panel shows, already
        gated by what the student has discovered. A scenario with no activity
        (a foundation panel) sends `foundation: true` and no `readout`.
        """
