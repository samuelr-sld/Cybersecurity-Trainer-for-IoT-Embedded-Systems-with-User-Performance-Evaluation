"""HTTP endpoint for the attached panel's status — what the Main Menu shows.

    GET /api/hardware/status    which panel is attached, and on which USB port?

The Main Menu has no WebSocket (a mode's socket is where a board's state has
always reached the page), so this is the one HTTP window onto the SHARED device
layer. It is a window and nothing more:

    Main Menu --GET--> this module --+--> device_monitor.refresh()   (the one monitor
                                     |                               both modes read)
                                     +--> identify_panel(...)        (MAC -> registry)

READ-ONLY, AND REUSING THE EXISTING SERVICE. There is no second detector, no
background loop, and no state of its own here. The answer comes from the same
`device_monitor` the Hack and Build header polls use, so the menu, a mode's
header and mode preparation cannot describe one board differently. The panel
verdict is `identify_panel` — MAC -> `PanelRegistry` -> `PanelDefinition` —
and is NEVER inferred from the USB port: a port names a bridge chip, not a
panel (every generic CP210x devkit reports the same descriptors).

LIGHTWEIGHT BY THE MONITOR'S OWN RULES. `refresh()` is the call a mode's
10-second `hardware_status` poll already makes, so this inherits what makes
that affordable: concurrent callers collapse into one `arduino-cli board list`
and a result younger than `HARDWARE_CACHE_SECONDS` is reused outright. Reading
a board's MAC resets it, so the monitor does that once per plug-in and caches
it per port — this endpoint never forces a re-read (`reverify_identity` is
reserved for mode preparation, which is about to act on the identity), never
opens a port, never compiles or flashes, and records nothing: it is not a
student action and must not look like one in any session log.

WHO POLLS, AND HOW OFTEN, IS THE PAGE'S CONCERN, not this module's: the
frontend asks on mount and then on the same slow interval the modes use, only
while the menu is on screen (`src/hooks/useHardwareStatus.js`).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.hardware import device_monitor, identify_panel

router = APIRouter(prefix="/api/hardware")


@router.get("/status")
async def hardware_status() -> dict:
    """The attached panel and USB port, in the words the Main Menu needs.

    `panel.status` is `PanelIdentificationStatus` verbatim, so the page can tell
    apart the four things a board can be:

        identified     MAC is bound to a registered panel      -> `name` is set
        unregistered   MAC was read but no panel is bound to it -> `mac` is set
        unidentified   a board is attached, its MAC is not read -> neither is set
        not_connected  no board                                 -> neither is set

    plus `not_checked` ("nobody has looked yet", the first moments after the
    backend starts) which is deliberately not "no board". `usb.port` is the
    REAL detected port (never the canonical training alias) and is None unless
    a board is present; the page renders that as Disconnected.
    """
    state = await device_monitor.refresh()
    identification = identify_panel(state, device_monitor.panel_registry)
    present = state.board_present
    return {
        "usb": {
            "connected": present,
            "port": state.port if present else None,
        },
        "panel": {
            "status": identification.status.value,
            "name": identification.panel.display_name if identification.panel else None,
            "mac": identification.mac,
        },
    }
