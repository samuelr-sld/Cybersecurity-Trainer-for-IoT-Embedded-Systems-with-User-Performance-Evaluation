"""The identity gate for panel-specific hardware tests, proven WITHOUT hardware.

`tests/hil_gating.py` decides whether a Panel-1-specific hardware-in-the-loop
test may run against the attached board. The hardware suites can only ever
demonstrate the case for whichever board is on the bench, so the whole decision
table is pinned here against constructed `DeviceState`s instead — runnable on
any machine, in the ordinary (`-m "not hardware"`) run, touching no port.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.hardware.panels import BUILT_IN_PANELS, PanelMatch, PanelRegistry
from app.hardware.state import DeviceState, DeviceStatus
from tests.hil_gating import PANEL_ONE_ID, PANEL_ONE_MAC, panel_one_skip_reason

OTHER_MAC = "20:50:0d:4d:4e:a8"


def attached(mac: str | None, *, panel: str | None = None) -> DeviceState:
    return DeviceState(status=DeviceStatus.CONNECTED, port="COM3", mac=mac, panel=panel)


# --- 1: no board attached -> skip ------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        DeviceStatus.NOT_CHECKED,
        DeviceStatus.DISCONNECTED,
        DeviceStatus.AMBIGUOUS,
        DeviceStatus.ERROR,
    ],
)
def test_no_board_skips(status: DeviceStatus) -> None:
    reason = panel_one_skip_reason(DeviceState(status=status))
    assert reason is not None
    assert "no ESP32" in reason


# --- 2: Panel 1's MAC -> run ------------------------------------------------


def test_panel_one_mac_runs() -> None:
    assert panel_one_skip_reason(attached(PANEL_ONE_MAC)) is None


def test_panel_one_mac_still_runs_while_a_repoll_is_in_flight() -> None:
    """A DETECTING re-check retains the last verdict (port, MAC): the board
    has not gone anywhere, so the gate must not flap to a skip."""
    state = DeviceState(
        status=DeviceStatus.DETECTING,
        port="COM3",
        mac=PANEL_ONE_MAC,
        checked_at=datetime.now(timezone.utc),
    )
    assert panel_one_skip_reason(state) is None


# --- 3 + 4: any other board -> skip -----------------------------------------


def test_an_unregistered_board_skips() -> None:
    reason = panel_one_skip_reason(attached(OTHER_MAC))
    assert reason is not None
    assert OTHER_MAC in reason
    assert PANEL_ONE_MAC in reason


def test_a_different_registered_panel_skips() -> None:
    """The gate is blind to whether the other board is registered: a MAC bound
    to Panel 2 (here, as its human panel name on the state) skips exactly like
    an unknown module, so registering Panel 2 later changes nothing here."""
    state = attached("aa:bb:cc:dd:ee:ff", panel="ENVIRONMENTAL MONITORING SYSTEM")
    assert panel_one_skip_reason(state) is not None


def test_an_unreadable_mac_skips_rather_than_assuming_panel_one() -> None:
    reason = panel_one_skip_reason(attached(None))
    assert reason is not None
    assert "could not be read" in reason


# --- the gate reads the physical MAC, never the registry's answer -----------


def test_a_registry_that_failed_to_name_panel_one_does_not_hide_behind_a_skip() -> None:
    """Panel 1's MAC with NO resolved panel name (a broken binding) must still
    OPEN the gate, so the tests that assert the binding run and FAIL loudly
    instead of being skipped into silence."""
    assert panel_one_skip_reason(attached(PANEL_ONE_MAC, panel=None)) is None


# --- the literal must not drift from production ------------------------------


def test_the_gate_literal_is_still_panel_ones_registered_mac() -> None:
    """`hil_gating` hard-codes Panel 1's MAC on purpose (see its docstring).
    If the registry's binding moves and this literal does not, the gated
    hardware tests would skip forever. This is the tripwire against that."""
    resolution = PanelRegistry(BUILT_IN_PANELS).resolve(PANEL_ONE_MAC)
    assert resolution.match is PanelMatch.IDENTIFIED
    assert resolution.panel is not None
    assert resolution.panel.panel_id == PANEL_ONE_ID
