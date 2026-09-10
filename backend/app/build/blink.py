"""The LED Blink Build Mode project — Phase 1 pipeline-proof firmware.

This is the current *default* Build Mode project. Its only job is to prove
that the existing write -> compile -> flash -> physical-execution pipeline
(`app/build/workspace.py`, `compiler.py`, `flasher.py`, `process.py`,
`service.py`) works end to end against real hardware. It carries no
vulnerability, no remediation objective, and no locked system logic of its
own — the Environmental Monitoring / Weak MQTT project (`environmental.py`)
that previously held this role predates the finalized five-panel scope and
is no longer loaded by default; it remains in the codebase, unused for now,
for when that scope resumes.

TEMPORARY PHASE 1 POC CONFIGURATION — FULLY EDITABLE, NO LOCKED REGION.
While Phase 1 proves the physical pipeline and Blockly is not yet built,
region locking serves no purpose here: there is no remediation objective to
protect a student from editing around, and the team wants to hand-edit or
paste generated C++ anywhere in `main.ino`, not just inside one carved-out
box. `main.ino` is therefore represented as a *single* EDITABLE segment
covering the whole file (former locked header comment included) rather than
a locked-then-editable split. This is a project-representation choice, not
an architecture change: `BuildWorkspace`, `FileSegment`/`RegionKind`,
`update_region()`, and the backend's general locked-region enforcement
(`workspace.py::RegionNotEditableError`) are all completely unchanged and
still apply to any project that *does* declare a LOCKED segment — see
`environmental.py`, which keeps its real `locked_pre`/`locked_post` split.
`src/screens/BuildMode.jsx` renders a file with no LOCKED segments as a
plain, unbordered code editor (no orange "security region" box, no region
map) purely because the file *has* no locked segments to show — nothing in
the frontend was special-cased to this project id either.

When the real remediation scope resumes, restore a `locked_pre` (and,
if needed, `locked_post`) `FileSegment` around a narrower `BLINK_REGION_ID`
segment here — the two-segment shape `environmental.py` already
demonstrates — and both the backend enforcement and the frontend's locked
styling come back automatically, with no other code to change.
"""

from __future__ import annotations

from app.build.models import BoardInfo, BuildProject, FileSegment, FirmwareFile, RegionKind

#: The one editable region in this project. Named for what it is here
#: (a plain blink program), not "security_logic" — this project has no
#: security framing. A future Blockly workspace targeting this project would
#: address the same id. For this Phase 1 POC it covers the *entire* file
#: (see the module docstring) rather than just the program body.
BLINK_REGION_ID = "blink_program"

_MAIN_INO_HEADER_COMMENT = """/* LED Blink -- Build Mode Phase 1 pipeline-proof firmware.
 * Edit the program below, then Save, Compile, and Flash to see it run on
 * the physical ESP32. This project exists to prove the pipeline works end
 * to end; it has no vulnerability and no remediation objective.
 *
 * TEMPORARY POC NOTE: the whole file is editable right now (no locked
 * region) so the pipeline can be exercised with any C++ program, not just
 * edits inside a fixed box. Region locking returns with the real scenarios.
 */

"""

_MAIN_INO_PROGRAM_DEFAULT = """void setup() {
  pinMode(2, OUTPUT);
}

void loop() {
  digitalWrite(2, HIGH);
  delay(1000);
  digitalWrite(2, LOW);
  delay(1000);
}
"""

#: The full starting source of `main.ino` — header comment plus program,
#: concatenated into the one EDITABLE segment this POC project uses. Kept as
#: two constants (rather than one literal) only so the comment and the
#: default program stay independently readable/diffable in this file.
_MAIN_INO_EDITABLE_DEFAULT = _MAIN_INO_HEADER_COMMENT + _MAIN_INO_PROGRAM_DEFAULT


def create_blink_project() -> BuildProject:
    """Build a fresh LED Blink project.

    One call, one independent `BuildProject` — same isolation guarantee
    `create_environmental_monitoring_project` gives: no two Build Mode
    sessions can share or corrupt each other's firmware state.
    """
    main_ino = FirmwareFile(
        path="main.ino",
        segments=(
            FileSegment(RegionKind.EDITABLE, BLINK_REGION_ID, _MAIN_INO_EDITABLE_DEFAULT),
        ),
    )
    return BuildProject(
        project_id="led-blink-poc",
        scenario_id="led-blink-poc",
        module_id="build_pipeline_proof",
        firmware_name="LED Blink (Build Pipeline Proof)",
        board=BoardInfo(name="ESP32 Dev Module", mcu="ESP32", fqbn="esp32:esp32:esp32"),
        files=(main_ino,),
        security_region_id=BLINK_REGION_ID,
    )
