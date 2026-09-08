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
    """

    lines: tuple[str, ...] = ()
    success: bool = True
    exit_code: int = EXIT_OK
    events: tuple[ScenarioEvent, ...] = ()

    @classmethod
    def ok(cls, *lines: str, events: tuple[ScenarioEvent, ...] = ()) -> "ScenarioOutcome":
        return cls(lines=lines, success=True, exit_code=EXIT_OK, events=events)

    @classmethod
    def failed(
        cls,
        *lines: str,
        exit_code: int = EXIT_FAILURE,
        events: tuple[ScenarioEvent, ...] = (),
    ) -> "ScenarioOutcome":
        return cls(lines=lines, success=False, exit_code=exit_code, events=events)

    @classmethod
    def usage(cls, *lines: str) -> "ScenarioOutcome":
        return cls(lines=lines, success=False, exit_code=EXIT_USAGE)


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
        """Simulated authorized firmware extraction (Stage 1)."""

    @abstractmethod
    def analyze_firmware(self) -> ScenarioOutcome:
        """Analyze extracted firmware, revealing MQTT config (Stage 2)."""

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
    def explore(self, host: str | None, port: int | None) -> ScenarioOutcome:
        """Simulated mqtt-explorer topic browse / visual observation."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """A JSON-serialisable view of the target for Phase 2D to render.

        This is the Phase 2D integration point: the WebSocket layer will
        serialise this into a state frame so the frontend can draw the target
        device panel (temperature, humidity, pressure, status, spoof/attack
        flags). It is data only and contains no terminal text.
        """
