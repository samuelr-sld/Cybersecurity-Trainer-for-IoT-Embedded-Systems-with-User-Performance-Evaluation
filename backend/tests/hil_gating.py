"""Identity gate for the PANEL-SPECIFIC hardware-in-the-loop tests.

`@pytest.mark.hardware` only says "some ESP32 is attached". That is the right
gate for a test about USB detection, MAC reading, esptool or the serial
transport — those behave the same on every panel — and the wrong one for a
test that asserts "the attached board is Panel 1": with a different panel on
the bench (or an unregistered module) it fails, and not because anything is
broken. Worse, a test that FLASHES Panel 1's firmware would overwrite whatever
a different panel was running.

So a panel-specific test additionally asks `panel_one_skip_reason`:

    no board attached                 -> skip   (the suite's existing gate)
    board attached, MAC unreadable    -> skip   (cannot confirm which panel)
    board attached, any OTHER MAC     -> skip   (another panel, or unregistered)
    board attached, Panel 1's MAC     -> run

WHAT IS COMPARED IS THE CHIP'S MAC, NOT THE REGISTRY'S ANSWER. The MAC is read
by the production `DeviceMonitor` (the same esptool probe a header poll uses)
and is a physical fact. If this gate instead asked `PanelRegistry` "is this
Panel 1?", a regression that stopped Panel 1's own MAC resolving would turn
into a SKIP, silently hiding exactly the failure
`test_panel_registry_resolves_the_identified_board` exists to report. Gating
on the MAC and asserting on the registry keeps that failure loud: the right
board attached but the chain broken still FAILS.

It is also why this is blind to whether another MAC is registered: Panel 2's
MAC, once bound, skips Panel 1's tests exactly as an unknown module does, with
no change here.

This module reads and decides; it opens no port and writes nothing. It is not
collected as a test (no `test_` prefix) and imports nothing from `app` beyond
the passive `DeviceState`.
"""

from __future__ import annotations

from app.hardware.state import DeviceState

#: Panel 1's board (`app/hardware/panels.py::BUILT_IN_PANELS`), the Smart Home
#: MQTT Control System. Deliberately a literal rather than a lookup in that
#: table — see the module docstring: the gate must not depend on the thing the
#: gated tests verify.
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"


def panel_one_skip_reason(state: DeviceState) -> str | None:
    """None when the attached board is Panel 1; otherwise why to skip.

    `state` is a completed detection (a `DeviceMonitor` snapshot after
    `refresh()`); `state.mac` is already in the canonical lower-case colon
    form `PANEL_ONE_MAC` is written in.
    """
    if not state.board_present:
        return (
            "no ESP32 detected on USB; Panel-1-specific hardware tests need "
            "Panel 1 attached"
        )
    if state.mac is None:
        return (
            "the attached board's MAC could not be read, so it cannot be "
            f"confirmed as Panel 1 ({PANEL_ONE_MAC}); check discover_esptool()"
        )
    if state.mac != PANEL_ONE_MAC:
        return (
            f"the attached board (MAC {state.mac}) is not Panel 1 "
            f"({PANEL_ONE_MAC}); Panel-1-specific hardware tests skip for any "
            "other panel or an unregistered module"
        )
    return None
