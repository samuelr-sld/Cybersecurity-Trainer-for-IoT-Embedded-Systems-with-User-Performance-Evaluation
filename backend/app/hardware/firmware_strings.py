"""Extracting printable strings from real firmware bytes — pure, no I/O.

The real-hardware counterpart to a scenario's canned string table (see
`app/scenarios/smart_home.py::_firmware_strings`): once
`app/commands/handlers/esptool_py.py` has captured a real
`FirmwareArtifact` (`app/hardware/flash_reader.py`) for a session, `strings`
and `grep` read it back through this module instead of the simulated list —
see `app/commands/handlers/strings.py` / `grep.py`.

GENERIC AND CONTENT-FREE. This module knows nothing about MQTT, Panel 1, or
any expected finding. It performs exactly what real `strings(1)` performs:
find runs of printable ASCII bytes at least `min_length` long. Whether those
runs happen to contain anything a student is looking for is a fact about
the ACTUAL BYTES on the chip, not about this function.
"""

from __future__ import annotations

#: Real `strings(1)`'s own default minimum run length.
DEFAULT_MIN_LENGTH = 4

#: Printable 7-bit ASCII, the same range real `strings` matches by default
#: (0x20 space through 0x7e tilde).
_PRINTABLE_LOW = 0x20
_PRINTABLE_HIGH = 0x7E


def extract_printable_strings(data: bytes, min_length: int = DEFAULT_MIN_LENGTH) -> tuple[str, ...]:
    """Every run of `min_length`+ printable ASCII bytes in `data`, in order.

    Runs are not deduplicated or sorted — real `strings` prints each
    occurrence where it finds it, and so does this.
    """
    if min_length < 1:
        raise ValueError(f"min_length must be at least 1, got {min_length}")

    found: list[str] = []
    run = bytearray()
    for byte in data:
        if _PRINTABLE_LOW <= byte <= _PRINTABLE_HIGH:
            run.append(byte)
            continue
        if len(run) >= min_length:
            found.append(run.decode("ascii"))
        run.clear()
    if len(run) >= min_length:
        found.append(run.decode("ascii"))
    return tuple(found)
