"""Panel/scenario packages — the experiment a connected panel provides.

    THE ENGINE PROVIDES THE TOOLS. THE CONNECTED PANEL PROVIDES THE EXPERIMENT.

That sentence is the architecture. `app/commands/` keeps offering one global
toolbox of real tools to every panel; this package is how the panel that is
plugged in says what the student is meant to DO with them.

One data flow, no panel branches anywhere:

    ESP32 MAC
      |
    PanelRegistry              app/hardware/panels.py   (pure lookup)
      |
    PanelDefinition            app/hardware/panels.py   (identity + package_id)
      |
    PanelPackage               this package             (<root>/<id>/panel.json)
      |-- ScenarioDefinition        which experiment
      |-- LearningContent           objectives, instructions, expected findings
      |-- WorkflowStep[]            the expected student workflow
      |-- EvaluationDeclaration     success conditions + relevant metrics
      +-- FirmwareConfiguration     app/hardware/firmware.py (REUSED, not re-declared)
      |
    Generic Hack Engine        app/commands/, app/scenarios/  (UNCHANGED)

Module map:

    models.py   the validated, frozen package shape. Passive data.
    loader.py   the only module here that touches the filesystem: locate,
                validate, load, reject. No cache, no execution, no paths
                from a client.
    service.py  `PanelResourceService` — composes the existing panel
                identification service with the loader, so one call answers
                "what experiment is the attached panel running?".

SCOPE OF PHASE 2D.1 / 2D.2 — RESOURCES ONLY. What exists is package loading
and Panel 1's resource integration. What does NOT exist, here or anywhere:
the Panel 1 attack activity, its vulnerability state machine, MQTT
discovery/observation/spoofing behaviour, physical attack impact,
remediation, secured firmware, automatic firmware provisioning, any Panel 2
activity, and any metric computation. A package DECLARES which metrics are
relevant; no formula for ACR, RE, EAC, TTE, TTR, AID or DEI exists in this
backend.

NOTHING IN THIS PACKAGE IS WIRED INTO A REQUEST PATH YET. No WebSocket
endpoint, session, command handler or frontend frame reads a package. Panel
identification stays silent, the Hack Mode `hardware` frame is unchanged, and
loading a package triggers no compile, flash, scenario start or event.
"""

from app.panels.loader import (
    MANIFEST_NAME,
    FirmwareResourceMissingError,
    PanelPackageError,
    PanelPackageInvalidError,
    PanelPackageLoader,
    PanelPackageNotFoundError,
    default_panel_package_loader,
)
from app.panels.models import (
    SCHEMA_VERSION,
    EvaluationDeclaration,
    EvaluationMetric,
    ExpectedFinding,
    LearningContent,
    PanelPackage,
    ScenarioDefinition,
    SuccessCondition,
    WorkflowStep,
)
from app.panels.service import (
    PanelResources,
    PanelResourceService,
    PanelResourceStatus,
    default_panel_resource_service,
)

__all__ = [
    "MANIFEST_NAME",
    "SCHEMA_VERSION",
    "EvaluationDeclaration",
    "EvaluationMetric",
    "ExpectedFinding",
    "FirmwareResourceMissingError",
    "LearningContent",
    "PanelPackage",
    "PanelPackageError",
    "PanelPackageInvalidError",
    "PanelPackageLoader",
    "PanelPackageNotFoundError",
    "PanelResourceService",
    "PanelResourceStatus",
    "PanelResources",
    "ScenarioDefinition",
    "SuccessCondition",
    "WorkflowStep",
    "default_panel_package_loader",
    "default_panel_resource_service",
]
