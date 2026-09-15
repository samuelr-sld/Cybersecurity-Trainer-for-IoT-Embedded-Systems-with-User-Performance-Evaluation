"""Shared plumbing for the serial commands.

    student's words -> resolve_target() -> REAL DeviceState.port -> transport

THE ONE RESOLUTION PATH. Every serial command routes its target through
`resolve_target` here, so there is exactly one place that decides which
physical port a student's words refer to — and exactly one place that can be
audited for the rule that matters: the canonical `/dev/ttyUSB0` training
alias is never what gets opened. It is translated to the real address the
shared `DeviceMonitor` detected (`app/hardware/serial_alias.py`), and only
that address reaches `SerialTransport`.

These handlers move bytes and read state. They start no process, interpret
no payload, and touch no filesystem — see `app/hardware/serial_transport.py`
for the rest of that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.commands.base import CommandContext, CommandResult
from app.hardware import canonical_alias, device_monitor, resolve_serial_target

#: Exit statuses, matching `app/commands/router.py`'s shell convention.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class Target:
    """A resolved serial target, or the reason it could not be resolved.

    `address` is always a REAL port this machine reported — never the
    training alias, and never a value a student supplied verbatim.
    """

    address: str | None = None
    error: CommandResult | None = None

    @property
    def ok(self) -> bool:
        return self.address is not None


def resolve_target(args: tuple[str, ...]) -> tuple[Target, tuple[str, ...]]:
    """Resolve an optional leading target argument to a real port.

    Returns the resolved target plus the arguments that remain once a
    leading target has been consumed, so `serial-send /dev/ttyUSB0 HELLO`
    and `serial-send HELLO` both work:

    - If the first argument names the attached board — its canonical alias,
      its real port, or any representation the header offers — it is
      consumed as the target.
    - Otherwise nothing is consumed and the currently attached board is
      used, so the common case needs no target at all.

    That ordering is why a message is never mistaken for a target: only a
    word that *actually resolves to the connected device* is treated as one.

    A disconnected board is a failure with a specific message rather than a
    silent fallback, because there is no board to fall back to.
    """
    state = device_monitor.snapshot()

    if args:
        resolved = resolve_serial_target(args[0], state)
        if resolved is not None:
            return Target(address=resolved), args[1:]

    if not state.connected or not state.port:
        return Target(error=no_device_result()), args

    return Target(address=state.port), args


def no_device_result() -> CommandResult:
    """The standard "nothing is plugged in" reply.

    Phrased as a plain fact, never an error: an unplugged board is a normal
    state of a training rig, not a fault in the trainer.
    """
    return CommandResult.text(
        "no ESP32 is connected",
        "Plug the board in over USB; the header reports it once detected.",
        exit_code=EXIT_FAILURE,
    )


def unknown_target_result(target: str) -> CommandResult:
    """A target that names neither the alias nor the attached board.

    `target` is safe to echo: the parser has already rejected control
    characters, so it cannot carry an escape sequence, and it is capped.
    """
    state = device_monitor.snapshot()
    known = ", ".join(state.port_aliases) if state.port_aliases else canonical_alias()
    return CommandResult.text(
        f"unknown serial target '{_shorten(target)}'",
        f"This board answers to: {known}",
        exit_code=EXIT_USAGE,
    )


def _shorten(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def describe_port(address: str) -> str:
    """How a real port is named back to the student.

    Shows the canonical training path alongside the real address whenever
    they differ, so a student on a Windows development box sees both the
    path the courseware uses and the port actually being opened, and never
    has to wonder which one is real.
    """
    alias = canonical_alias()
    return address if address == alias else f"{address} (as {alias})"
