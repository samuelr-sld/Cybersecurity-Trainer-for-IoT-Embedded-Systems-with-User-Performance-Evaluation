"""The canonical trainer serial path, and how it resolves to a real port.

    student types / reads:   /dev/ttyUSB0      (canonical trainer alias)
                                   |
                           resolve_serial_target
                                   |
                                   v
    actual hardware:         DeviceState.port  ("COM3" on this dev box,
                                                 "/dev/ttyACM0" on a Pi, ...)
                                   |
                                   v
                          serial transport / arduino-cli

WHY AN ALIAS EXISTS AT ALL. The trainer's deployment target is a Raspberry
Pi, so the instructional material, the Hack Mode command examples, and the
muscle memory students build are all Linux-shaped: `/dev/ttyUSB0`. During
development the very same board enumerates as `COM3` on Windows. Teaching
one path and showing another would make the worked examples untypeable, so
the student-facing layer speaks one stable, canonical name everywhere.

WHAT IT IS NOT. `/dev/ttyUSB0` here is a TRAINER/COMMAND ALIAS — a label the
courseware uses — never a claim that this host has such a device node, and
never something handed to the serial layer. Nothing opens it, nothing
flashes to it, and `DeviceState.port` is never set from it. On a Pi where
the real port genuinely *is* `/dev/ttyUSB0` the alias and the actual port
coincide and collapse to a single representation, which is exactly right.

THE ONE DIRECTION THAT MATTERS. Display goes port -> representations
(`serial_representations`); commands go alias -> real port
(`resolve_serial_target`). Every physical operation is performed against the
value that came back from `resolve_serial_target`, i.e. against
`DeviceState.port` — which is why a student can type the canonical path in a
Hack Mode command while the backend still talks to COM3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:  # pragma: no cover
    from app.hardware.state import DeviceState


def canonical_alias() -> str:
    """The Linux/training serial path the courseware speaks.

    Read through `config` rather than captured at import time so a lab that
    standardises on a different node (`/dev/ttyACM0`, say) can set
    TRAINER_CANONICAL_SERIAL_ALIAS without a code change, and so tests can
    override it.
    """
    return config.CANONICAL_SERIAL_ALIAS


def serial_representations(port: str | None, label: str | None = None) -> tuple[str, ...]:
    """Every name the student-facing USB field may show, in display order.

    Order is deliberate: the ACTUAL detected port first, because that is the
    truth about this machine and what a student staring at Device Manager or
    `dmesg` will see; then any genuinely different spelling detection itself
    reported (`label` — a `/dev/serial/by-id/...` path, for instance); then
    the canonical trainer alias, which is what the Hack Mode command
    examples use.

    Deduplicated, so on a Raspberry Pi whose real port already *is* the
    canonical path the field holds a single value and simply stays on it
    rather than "toggling" between two identical strings.

    Empty for a port of None — a disconnected device has no representations,
    and the header shows a dash rather than a stale or invented path.
    """
    if not port:
        return ()
    return tuple(dict.fromkeys(name for name in (port, label, canonical_alias()) if name))


def resolve_serial_target(target: str | None, state: "DeviceState") -> str | None:
    """Map a student-supplied serial target onto the real port to open.

    THE SEAM the later Hack Mode serial engine plugs into. A command like

        serial-monitor /dev/ttyUSB0

    passes its argument through here and receives `DeviceState.port` back —
    "COM3" on this development machine — and opens *that*. The canonical
    alias therefore never reaches a serial library, an `arduino-cli`
    invocation, or an `open()`; it is resolved away first, which is the
    whole point of having a resolution step rather than letting the UI
    string flow downstream.

    Accepts either name for the attached board: the canonical trainer alias,
    or the actual detected port (a student reading the header's other
    representation, or a Pi user typing what `dmesg` showed). Comparison
    ignores case and surrounding whitespace, because Windows port names are
    case-insensitive and a typed command line routinely carries stray
    spaces; the alias is our own label, so being lenient about its case
    costs nothing.

    Returns None — never a guess, and never a fallback to "the only board" —
    when nothing is connected or the target names neither. A caller that
    gets None must report an unknown target rather than open something the
    student did not ask for.
    """
    if target is None or not state.connected or not state.port:
        return None
    candidate = target.strip().casefold()
    if not candidate:
        return None
    known = {state.port.casefold(), canonical_alias().casefold()}
    known.update(alias.casefold() for alias in state.port_aliases)
    return state.port if candidate in known else None
