"""Build Mode backend — the defender/developer side of the trainer.

Position in the pipeline:

    Build WebSocket -> Build Service -> Build Workspace -> BuildProject
    (app/build_websocket.py) (service.py)  (workspace.py)   (models.py)
                                  |
                                  +-> CompilerAdapter -> arduino-cli compile
                                  |   (compiler.py)
                                  |
                                  +-> FlasherAdapter  -> arduino-cli upload -> ESP32
                                      (flasher.py)

Responsibility split:

    models.py        passive dataclasses: `BuildProject`, `FirmwareFile`,
                      `FileSegment`, region/status enums.
    events.py         `BuildEvent` domain events — the evaluation seam.
    workspace.py      `BuildWorkspace` — the only thing allowed to mutate a
                      project's files, and the enforcement point for locked
                      vs. editable regions. Also the only thing that reads a
                      project's files onto disk (`materialize`), for compilation.
    environmental.py  the Environmental Monitoring reference project.
    discovery/        `analyze_source` — passive C++ structural discovery
                      (B1). Produces a `BuildDocument`/`CodeSection[]` and
                      imports nothing else in this package.
    document_project.py  the one conversion `CodeSection[] -> FileSegment[]`
                      (B2). Pure; reads no file and knows no panel.
    sketch_source.py  `load_sketch_project` — reads a real sketch directory
                      through the two above into a `BuildProject` (B2). The
                      only step here that opens a file; still knows no panel
                      (`app/build_project_selection.py` supplies its
                      arguments off a resolved `PanelPackage`).
    process.py        `run_capture` — the only module in this package (and in
                      the whole backend) allowed to spawn a subprocess.
    compiler.py       `CompilerAdapter` / `ArduinoCliCompiler` — builds the
                      `arduino-cli compile` argument array and interprets
                      its result; spawns nothing itself.
    flasher.py        `FlasherAdapter` / `ArduinoCliFlasher` — the same for
                      serial-device discovery and real firmware upload.
    service.py        `BuildService` — session-level orchestration called by
                      the WebSocket layer; transport/UI independent.
    provisioning.py   the generic compile-time credential-provisioning seam
                      (Option A) — `ProvisioningStrategy`/`ProvisioningPlan`/
                      `ProvisioningStrategyRegistry`. Panel-agnostic; not
                      re-exported here (see its own docstring). No panel
                      registers a strategy today — Panel 1's committed
                      firmware carries its real lab credentials as literals
                      instead (see `app/build_provisioning_selection.py`).

PHASE 3A SCOPE. A student can load the Environmental Monitoring project,
view its full firmware, and edit only its one editable security region.

PHASE 1 (BUILD MODE POC) SCOPE. The default project a fresh session loads
is now the LED Blink pipeline-proof project (`blink.py`), not Environmental
Monitoring — the latter predates the finalized five-panel scope and is kept
in the codebase, unused by default, for when that scope resumes. Nothing
about the pipeline below changed: `create_default_workspace` just wraps a
different `BuildProject`.

PHASE 3B SCOPE. A student can also compile the current workspace with the
real `arduino-cli` toolchain (`app/build/compiler.py`) — a materialized,
throwaway copy of the workspace, never the live one. `CompileStatus` moves
between `NOT_STARTED`/`RUNNING`/`SUCCEEDED`/`FAILED` for real.

PHASE 3C SCOPE. A student can also flash a *successfully compiled*
workspace onto a physically connected ESP32 (`app/build/flasher.py`): the
backend discovers the attached serial device itself, refuses to guess
between several, and runs a real `arduino-cli upload` against the build
output that compile retained. `FlashStatus` is now driven for real, with
`NO_DEVICE` kept distinct from `FAILED` so "nothing is plugged in" is never
dressed up as a build or toolchain problem.

A successful flash means one thing: the upload process exited 0. It does not
mean the firmware runs, works, or is secure. `ValidationStatus` remains
always `NOT_STARTED` — validation, security testing, Hack Mode re-testing,
scoring, and Blockly are still not implemented; see `app/build/events.py`'s
still-reserved event vocabulary for those.

SECURITY BOUNDARY — inherited from Hack Mode's, with one explicit, narrow
exception. Nothing in this package parses the firmware text it holds or
accepts a whole-file replacement — edits are always addressed by
`(path, region_id)` through `BuildWorkspace`, never by submitting raw file
content (see `app/build/workspace.py`). Nothing in this package uses
`os.system`/`os.popen`, `shell=True`, `eval`/`exec`, or a CMD/PowerShell/sh
invocation, and nothing but `app/build/process.py` may invoke a subprocess
at all — `compiler.py` and `flasher.py` decide *what* to run and hand it a
backend-built argument array, but spawn nothing themselves. Those three
modules' own docstrings document why the one exception exists and how the
whole path stays safe (argument list only, no shell, backend-only arguments
including the serial port, bounded timeouts).
"""

from __future__ import annotations

from app.build.blink import BLINK_REGION_ID, create_blink_project
from app.build.compiler import (
    ArduinoCliCompiler,
    CompiledArtifact,
    CompileFailureCategory,
    CompileOutcome,
    CompileRequest,
    CompilerAdapter,
    default_compiler,
)
from app.build.document_project import (
    DocumentProjectError,
    board_info_from_fqbn,
    build_project_from_document,
    firmware_file_from_document,
    policy_for_document,
)
from app.build.environmental import (
    SECURITY_REGION_ID,
    create_environmental_monitoring_project,
)
from app.build.events import BuildEvent, BuildEventType
from app.build.flasher import (
    ArduinoCliFlasher,
    DeviceDetectOutcome,
    DeviceDetectRequest,
    FlasherAdapter,
    FlashFailureCategory,
    FlashOutcome,
    FlashRequest,
    SerialDevice,
    default_flasher,
)
from app.build.models import (
    BoardInfo,
    BuildProject,
    CompileStatus,
    FileSegment,
    FirmwareFile,
    FlashStatus,
    HardwareStatus,
    RegionKind,
    ValidationStatus,
)
from app.build.no_device import NO_DEVICE_PROJECT_ID, create_no_device_project
from app.build.policy import (
    DEFAULT_POLICY,
    InteractionPolicy,
    ProjectPolicy,
    ProjectPolicyError,
    build_project_policy,
)
from app.build.sketch_source import SketchSourceError, load_sketch_project
from app.build.workspace import (
    BuildWorkspace,
    BuildWorkspaceError,
    ProjectFileNotFoundError,
    RegionNotEditableError,
    RegionNotFoundError,
)


def create_default_workspace() -> BuildWorkspace:
    """Build the workspace a bare `BuildSession()` starts in.

    One call, one independent workspace wrapping its own `BuildProject` — the
    dataclass default for a session nobody's connection lifecycle supplied a
    workspace to (direct construction, most existing tests). It is NOT what a
    real `/ws/build` connection loads any more: `app/build_project_selection.py`
    always passes an explicit `workspace`, the attached panel's real firmware
    when one resolves and `create_no_device_workspace()` otherwise — see that
    module for why LED Blink stopped being the connection-path fallback.

    Currently the LED Blink pipeline-proof project (Phase 1) — see
    `blink.py`. `create_environmental_monitoring_project` remains available
    and independently constructible for the later five-panel phase; it is
    simply not what a fresh session loads right now.
    """
    return BuildWorkspace(create_blink_project())


def create_no_device_workspace() -> BuildWorkspace:
    """The workspace a session with no resolved panel gets. See `no_device.py`."""
    return BuildWorkspace(create_no_device_project())


__all__ = [
    "BLINK_REGION_ID",
    "DEFAULT_POLICY",
    "SECURITY_REGION_ID",
    "ArduinoCliCompiler",
    "ArduinoCliFlasher",
    "BoardInfo",
    "BuildEvent",
    "BuildEventType",
    "BuildProject",
    "BuildWorkspace",
    "BuildWorkspaceError",
    "CompileFailureCategory",
    "CompiledArtifact",
    "CompileOutcome",
    "CompileRequest",
    "CompileStatus",
    "CompilerAdapter",
    "DeviceDetectOutcome",
    "DeviceDetectRequest",
    "DocumentProjectError",
    "FileSegment",
    "FirmwareFile",
    "FlashFailureCategory",
    "FlashOutcome",
    "FlashRequest",
    "FlashStatus",
    "FlasherAdapter",
    "HardwareStatus",
    "InteractionPolicy",
    "NO_DEVICE_PROJECT_ID",
    "ProjectFileNotFoundError",
    "ProjectPolicy",
    "ProjectPolicyError",
    "RegionKind",
    "RegionNotEditableError",
    "RegionNotFoundError",
    "SerialDevice",
    "SketchSourceError",
    "ValidationStatus",
    "board_info_from_fqbn",
    "build_project_from_document",
    "build_project_policy",
    "create_blink_project",
    "create_default_workspace",
    "create_environmental_monitoring_project",
    "create_no_device_project",
    "create_no_device_workspace",
    "default_compiler",
    "default_flasher",
    "firmware_file_from_document",
    "load_sketch_project",
    "policy_for_document",
]
