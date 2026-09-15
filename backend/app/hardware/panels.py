"""Which physical training panel a given ESP32 is.

    ESP32 MAC (app/hardware/identity.py) -> panel name (this module)

A "panel" is one of the trainer's physical activity stations. The board's
MAC is its durable identity; this maps that identity to the human-readable
name a student sees. Keeping the mapping here — rather than in either
mode's UI — is what lets Hack Mode and Build Mode name the same board
identically without either one owning the knowledge.

ONE PANEL IS MAPPED TODAY. Only the ESP32 physically connected to this
project's development machine is configured; the other four panels of the
finalized five-panel scope get entries here as their boards are actually
connected and their MACs read. The shape is already right for them, which
is the whole point of a table:

    MAC A -> SMART HOME MQTT CONTROL SYSTEM      (mapped)
    MAC B -> ENVIRONMENTAL MONITORING SYSTEM     (awaiting hardware)
    MAC C -> EMERGENCY EXIT LIGHTING SYSTEM      (awaiting hardware)
    MAC D -> IOT IDENTITY AND ACCESS CONTROL SYSTEM
    MAC E -> SMART IOT DEVICE CONTROL SYSTEM

UNMAPPED IS NOT AN ERROR, AND IS NEVER GUESSED. A board whose MAC has no
entry here resolves to None, and the UI shows the MAC itself. Inventing a
plausible panel name for an unknown board would make the header lie about
which station a student is sitting at — the one thing this table exists to
get right.
"""

from __future__ import annotations

from app import config
from app.hardware.identity import normalize_mac

#: MAC -> panel name. Keys are canonical lower-case colon form
#: (`normalize_mac`), so a lookup cannot miss on spelling.
#:
#: 20:9b:a9:88:0b:e4 is the ESP32-D0WD-V3 on this project's development
#: machine, read from the chip's OTP ROM with `esptool read_mac` — not a
#: placeholder and not an example value.
BUILT_IN_PANEL_NAMES: dict[str, str] = {
    "20:9b:a9:88:0b:e4": "SMART HOME MQTT CONTROL SYSTEM",
}


def _parse_overrides(raw: str) -> dict[str, str]:
    """Read `TRAINER_PANEL_NAMES` — `mac=NAME` pairs, comma separated.

    Lets a second machine (or a lab with the other four panels wired up)
    add entries without editing this file. Malformed or non-MAC entries are
    skipped rather than raising: a typo in an environment variable must not
    prevent the backend from starting, and the affected board simply shows
    its MAC, which is the same honest fallback an unmapped board gets.
    """
    mapping: dict[str, str] = {}
    for chunk in raw.split(","):
        key, separator, value = chunk.partition("=")
        if not separator:
            continue
        mac = normalize_mac(key)
        name = value.strip()
        if mac and name:
            mapping[mac] = name
    return mapping


def panel_names() -> dict[str, str]:
    """The active mapping: built-ins, with configured overrides applied."""
    mapping = dict(BUILT_IN_PANEL_NAMES)
    mapping.update(_parse_overrides(config.PANEL_NAMES_RAW))
    return mapping


def panel_for(mac: str | None) -> str | None:
    """The panel name for this MAC, or None when it is unknown.

    None is the honest answer for an unmapped board and is what makes the
    UI fall back to displaying the MAC — see the module docstring.
    """
    if not mac:
        return None
    canonical = normalize_mac(mac)
    return panel_names().get(canonical) if canonical else None
