"""The Build Mode placeholder for "no panel resolved" (no-device correction).

This is NOT a Build Mode activity. `app/build_project_selection.py` used to
fall back to the LED Blink pipeline-proof project (`blink.py`) for every panel
resolution failure, including the ordinary case of no ESP32 being connected at
all — which meant a student saw a real, editable firmware activity with no
device attached and no panel identified. The physical ESP32 is supposed to be
what selects a Build Mode activity, so there must be a distinct, honest "there
is nothing to load" answer instead of quietly substituting one.

This project is that answer. It carries a single LOCKED, non-empty segment (a
`BuildProject` cannot render nothing) explaining that a panel must be attached,
declares no security region and no policy (there is nothing to remediate), and
its `project_id` (`NO_DEVICE_PROJECT_ID`) is a stable marker the frontend reads
to render its empty/no-device state instead of an IDE. It is never editable,
never compiled, and never flashed — see `BuildSession.has_active_project` in
`app/build_sessions.py`, which `app/build_project_selection.py` sets to False
whenever this project is what a selection produced, and which
`app/build/service.py` checks before honouring any edit/compile/flash/validate
request for the session.

It still declares a real ESP32 board FQBN (the same one `blink.py` and every
shipped panel use) so hardware presence detection keeps working — both modes
poll for PANEL/USB/CONNECTED on the same 10s interval regardless of whether an
activity is loaded, and that must not stop just because no panel resolved yet.
"""

from __future__ import annotations

from app.build.models import BoardInfo, BuildProject, FileSegment, FirmwareFile, RegionKind

#: A stable, non-panel, non-firmware marker. `src/screens/BuildMode.jsx` reads
#: `state.project.project_id` (via `has_active_project`, not this string) to
#: decide whether to render the IDE at all — this id exists for diagnostics
#: and so this module never has to be confused with a real panel's firmware.
NO_DEVICE_PROJECT_ID = "no-device"

_NO_DEVICE_REGION_ID = "no_device_notice"

#: The generic ESP32 Dev Module FQBN every shipped panel and the Blink POC
#: build against. Naming it here (rather than leaving hardware detection
#: without a target) is what keeps the header's PANEL/USB/CONNECTED status
#: truthful before any panel has been identified.
_NO_DEVICE_BOARD = BoardInfo(name="ESP32 Dev Module", mcu="ESP32", fqbn="esp32:esp32:esp32")

_NOTICE_TEXT = """/* No ESP32 detected.
 *
 * Build Mode loads a panel's activity from the physically attached ESP32.
 * Connect a panel over USB and wait for it to be identified to load its
 * firmware here.
 */
"""


def create_no_device_project() -> BuildProject:
    """A fresh, independent placeholder project for a session with no panel.

    One call, one independent `BuildProject`, the same isolation guarantee
    `create_blink_project` gives — not that isolation matters here, since
    nothing may ever edit it, but so this function needs no special case
    wherever a `BuildProject` is expected.
    """
    main_ino = FirmwareFile(
        path="main.ino",
        segments=(FileSegment(RegionKind.LOCKED, _NO_DEVICE_REGION_ID, _NOTICE_TEXT),),
    )
    return BuildProject(
        project_id=NO_DEVICE_PROJECT_ID,
        scenario_id=NO_DEVICE_PROJECT_ID,
        module_id="no_device",
        firmware_name="No device connected",
        board=_NO_DEVICE_BOARD,
        files=(main_ino,),
        security_region_id=None,
    )
