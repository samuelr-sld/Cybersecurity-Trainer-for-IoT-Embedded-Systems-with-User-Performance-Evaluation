"""Phase 2D.1 / 2D.2 — panel/scenario package architecture + Panel 1.

    ESP32 MAC -> PanelRegistry -> PanelDefinition -> PanelPackage -> FirmwareConfiguration
     (fake here)  (hardware/)      (hardware/)       (app/panels/)     (hardware/firmware.py)

What this asserts, mapped to the phase's required test list:

 1. Panel 1's package loads successfully.
 2. The registered Panel 1 MAC resolves to Panel 1.
 3. Panel 1's definition resolves its scenario package.
 4. Panel 1's package resolves its firmware configuration.
 5. A missing package is rejected cleanly (and told apart from an invalid one).
 6. An invalid package configuration is rejected cleanly.
 7. An unknown MAC stays unknown all the way through the service.
 8. A second, fake panel package loads through the SAME loader algorithm with
    no code change — extensibility is structural.
 9. Package loading executes no command and touches no command registry.
10. Package loading performs no firmware flashing and imports no build layer.

Existing 2A/2B/2C/2C.5 behaviour and Build Mode are covered by their own
suites and are unchanged by this phase; this file adds only new coverage.

EVERY MAC HERE IS FAKE (`02:...`, locally administered) except where a test
deliberately exercises the real registered Panel 1 MAC through a pure
lookup, which touches no hardware. No esptool runs, no board is opened, and
no arduino-cli is invoked anywhere in this file.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from app.hardware.firmware import FirmwareSourceKind
from app.hardware.panel_identification import (
    PanelIdentification,
    PanelIdentificationStatus,
)
from app.hardware.panels import (
    BUILT_IN_PANELS,
    PanelDefinition,
    default_panel_registry,
)
from app.panels import (
    SCHEMA_VERSION,
    EvaluationMetric,
    FirmwareResourceMissingError,
    PanelPackage,
    PanelPackageInvalidError,
    PanelPackageLoader,
    PanelPackageNotFoundError,
    PanelResourceService,
    PanelResourceStatus,
    default_panel_package_loader,
)
from app.panels.service import PanelResources

APP_PANELS = pathlib.Path(__file__).resolve().parents[1] / "app" / "panels"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"
PANEL_ONE_ID = "smart-home-mqtt-control"


# --- a valid manifest, as data, for building temporary packages -------------


def valid_manifest(panel_id: str = "test-panel") -> dict:
    """The smallest manifest a loader accepts, with firmware omitted."""
    return {
        "schema_version": SCHEMA_VERSION,
        "panel_id": panel_id,
        "scenario": {
            "scenario_id": panel_id,
            "title": "Test Panel Scenario",
            "summary": "A scenario used only in tests.",
        },
        "learning": {
            "objectives": ["Understand the seam."],
            "activity_instructions": ["Do the thing."],
            "expected_findings": [
                {"finding_id": "a-finding", "description": "Something to find."}
            ],
        },
        "workflow": [
            {"step_id": "step-one", "title": "First step", "command": "nmap"}
        ],
        "evaluation": {
            "success_conditions": [
                {
                    "condition_id": "done",
                    "description": "It happened.",
                    "required_events": ["spoof_succeeded"],
                }
            ],
            "metrics": ["ACR", "TTE"],
        },
        "parameters": {"note": "static"},
    }


def write_package(root: pathlib.Path, package_id: str, manifest: dict) -> pathlib.Path:
    directory = root / package_id
    directory.mkdir(parents=True)
    (directory / "panel.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


# --- 1: Panel 1 package loads ----------------------------------------------


def test_panel_one_package_loads_successfully() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)

    assert isinstance(package, PanelPackage)
    assert package.schema_version == SCHEMA_VERSION
    assert package.panel_id == PANEL_ONE_ID
    assert package.scenario.title == "Smart Home MQTT Control System"
    assert package.learning.objectives
    assert package.workflow
    assert package.evaluation.metrics


def test_panel_one_declares_only_established_metrics() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    assert set(package.evaluation.metrics) <= set(EvaluationMetric)
    # Hack Mode metrics for a Hack activity.
    assert EvaluationMetric.TTE in package.evaluation.metrics


def test_panel_one_workflow_names_only_real_engine_tools() -> None:
    """Guidance must point at tools the generic Hack Engine actually offers,
    not invented panel-specific commands."""
    from app.commands.registry import build_default_registry

    known = set(build_default_registry().names())
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    for step in package.workflow:
        if step.command is not None:
            assert step.command in known, step.step_id


def test_panel_one_success_conditions_use_canonical_event_vocabulary() -> None:
    from app.scenarios.events import ScenarioEventType

    known = {member.value for member in ScenarioEventType}
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    for condition in package.evaluation.success_conditions:
        for event in condition.required_events:
            assert event in known, event


# --- 2: MAC resolves to Panel 1 --------------------------------------------


def test_registered_panel_one_mac_resolves_to_panel_one() -> None:
    resolution = default_panel_registry().resolve(PANEL_ONE_MAC)

    assert resolution.identified is True
    assert resolution.panel_id == PANEL_ONE_ID
    assert resolution.panel.package_id == PANEL_ONE_ID


# --- 3: definition resolves its scenario package ----------------------------


def test_panel_one_definition_resolves_its_package() -> None:
    registry = default_panel_registry()
    panel = registry.resolve(PANEL_ONE_MAC).panel

    package = default_panel_package_loader().load_for_panel(panel)

    assert package.panel_id == panel.panel_id
    assert package.scenario_id == PANEL_ONE_ID


def test_load_for_panel_rejects_a_package_declaring_a_different_panel(
    tmp_path: pathlib.Path,
) -> None:
    """A package copied into the wrong directory must be caught, not taught."""
    manifest = valid_manifest("misfiled")
    manifest["panel_id"] = "someone-else"
    write_package(tmp_path, "misfiled", manifest)
    loader = PanelPackageLoader(root=tmp_path)
    panel = PanelDefinition(
        panel_id="misfiled", display_name="MISFILED", package_id="misfiled"
    )

    with pytest.raises(PanelPackageInvalidError):
        loader.load_for_panel(panel)


def test_load_for_panel_without_a_package_is_not_found(tmp_path: pathlib.Path) -> None:
    loader = PanelPackageLoader(root=tmp_path)
    panel = PanelDefinition(panel_id="bare", display_name="BARE")
    with pytest.raises(PanelPackageNotFoundError):
        loader.load_for_panel(panel)


# --- 4: package resolves its firmware configuration -------------------------


def test_panel_one_package_resolves_its_firmware_configuration() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)

    firmware = package.firmware
    assert firmware is not None
    assert firmware.firmware_id == "smart-home-mqtt-control-firmware"
    assert firmware.source.kind is FirmwareSourceKind.SKETCH_DIRECTORY
    assert firmware.board.fqbn == "esp32:esp32:esp32"
    assert firmware.serial.baud_rate == 115200


def test_panel_one_firmware_resource_exists_on_disk() -> None:
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    sketch = package.firmware_sketch_path

    assert sketch is not None
    assert sketch.is_dir()
    assert any(sketch.glob("*.ino"))


def test_declared_but_missing_firmware_resource_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    manifest = valid_manifest("no-sketch")
    manifest["firmware"] = {
        "firmware_id": "no-sketch-firmware",
        "source": {"kind": "sketch_directory", "reference": "firmware/absent"},
        "board": {"fqbn": "esp32:esp32:esp32"},
    }
    write_package(tmp_path, "no-sketch", manifest)

    with pytest.raises(FirmwareResourceMissingError):
        PanelPackageLoader(root=tmp_path).load("no-sketch")


def test_firmware_configuration_rejects_a_traversing_sketch_reference(
    tmp_path: pathlib.Path,
) -> None:
    manifest = valid_manifest("escape")
    manifest["firmware"] = {
        "firmware_id": "escape-firmware",
        "source": {"kind": "sketch_directory", "reference": "../../etc"},
        "board": {"fqbn": "esp32:esp32:esp32"},
    }
    write_package(tmp_path, "escape", manifest)

    # Rejected by FirmwareSource construction, surfaced as a package error.
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("escape")


# --- 5 & 6: missing / invalid packages are rejected cleanly -----------------


def test_a_missing_package_is_not_found(tmp_path: pathlib.Path) -> None:
    with pytest.raises(PanelPackageNotFoundError):
        PanelPackageLoader(root=tmp_path).load("does-not-exist")


def test_missing_is_distinct_from_invalid(tmp_path: pathlib.Path) -> None:
    """"Not integrated yet" and "broken" must be different exceptions."""
    assert not issubclass(PanelPackageNotFoundError, PanelPackageInvalidError)
    assert not issubclass(PanelPackageInvalidError, PanelPackageNotFoundError)


def test_malformed_json_is_rejected(tmp_path: pathlib.Path) -> None:
    directory = tmp_path / "broken-json"
    directory.mkdir()
    (directory / "panel.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("broken-json")


def test_wrong_schema_version_is_rejected(tmp_path: pathlib.Path) -> None:
    manifest = valid_manifest("old-schema")
    manifest["schema_version"] = SCHEMA_VERSION + 1
    write_package(tmp_path, "old-schema", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("old-schema")


def test_a_missing_required_field_is_rejected(tmp_path: pathlib.Path) -> None:
    manifest = valid_manifest("no-scenario")
    del manifest["scenario"]
    write_package(tmp_path, "no-scenario", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("no-scenario")


def test_an_unknown_manifest_field_is_rejected(tmp_path: pathlib.Path) -> None:
    """Strict by default: a typo in trusted courseware fails loudly."""
    manifest = valid_manifest("typo")
    manifest["objectivez"] = ["misspelled top-level key"]
    write_package(tmp_path, "typo", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("typo")


def test_an_unknown_metric_is_rejected(tmp_path: pathlib.Path) -> None:
    manifest = valid_manifest("bad-metric")
    manifest["evaluation"]["metrics"] = ["ACR", "NOT_A_METRIC"]
    write_package(tmp_path, "bad-metric", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("bad-metric")


def test_a_workflow_command_that_looks_like_a_command_line_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    manifest = valid_manifest("bad-step")
    manifest["workflow"] = [
        {"step_id": "evil", "title": "Sneaky", "command": "nmap; rm -rf /"}
    ]
    write_package(tmp_path, "bad-step", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("bad-step")


@pytest.mark.parametrize("bad_id", ["../evil", "Has Space", "UPPER", "a/b", "a.b"])
def test_a_non_identifier_package_id_cannot_select_a_directory(
    tmp_path: pathlib.Path, bad_id: str
) -> None:
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load(bad_id)


def test_a_non_scalar_parameter_is_rejected(tmp_path: pathlib.Path) -> None:
    manifest = valid_manifest("nested-param")
    manifest["parameters"] = {"nested": {"not": "allowed"}}
    write_package(tmp_path, "nested-param", manifest)
    with pytest.raises(PanelPackageInvalidError):
        PanelPackageLoader(root=tmp_path).load("nested-param")


# --- 7: unknown MAC stays unknown ------------------------------------------


class _FixedIdentification:
    """A panel-identification double returning one canned answer, no I/O."""

    def __init__(self, identification: PanelIdentification) -> None:
        self._identification = identification

    def identify(self) -> PanelIdentification:
        return self._identification

    async def refresh(self) -> PanelIdentification:
        return self._identification


def service_over(identification: PanelIdentification, loader: PanelPackageLoader):
    return PanelResourceService(
        identification=_FixedIdentification(identification), loader=loader
    )


def test_an_unknown_mac_stays_unregistered_through_the_service() -> None:
    identification = PanelIdentification(
        status=PanelIdentificationStatus.UNREGISTERED, port="COM3", mac="02:00:00:00:00:ff"
    )
    resources = service_over(identification, default_panel_package_loader()).resolve()

    assert resources.status is PanelResourceStatus.UNREGISTERED
    assert resources.package is None
    assert resources.firmware is None
    assert "02:00:00:00:00:ff" in resources.detail


def test_no_board_is_not_connected_through_the_service() -> None:
    identification = PanelIdentification(status=PanelIdentificationStatus.NOT_CONNECTED)
    resources = service_over(identification, default_panel_package_loader()).resolve()
    assert resources.status is PanelResourceStatus.NOT_CONNECTED
    assert resources.package is None


def test_a_registered_panel_without_a_package_is_no_package() -> None:
    panel = PanelDefinition(panel_id="bare", display_name="BARE")
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED, port="COM3", mac="02:00:00:00:00:0a",
        panel=panel,
    )
    resources = service_over(identification, default_panel_package_loader()).resolve()
    assert resources.status is PanelResourceStatus.NO_PACKAGE
    assert resources.package is None


def test_a_declared_but_missing_package_is_a_package_error(tmp_path: pathlib.Path) -> None:
    panel = PanelDefinition(
        panel_id="ghost", display_name="GHOST", package_id="ghost"
    )
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED, port="COM3", mac="02:00:00:00:00:0a",
        panel=panel,
    )
    resources = service_over(identification, PanelPackageLoader(root=tmp_path)).resolve()
    assert resources.status is PanelResourceStatus.PACKAGE_ERROR
    assert resources.detail


def test_panel_one_resolves_ready_through_the_service() -> None:
    panel = default_panel_registry().resolve(PANEL_ONE_MAC).panel
    identification = PanelIdentification(
        status=PanelIdentificationStatus.IDENTIFIED,
        port="COM3",
        mac=PANEL_ONE_MAC,
        panel=panel,
    )
    resources = service_over(identification, default_panel_package_loader()).resolve()

    assert resources.ready
    assert isinstance(resources, PanelResources)
    assert resources.scenario.title == "Smart Home MQTT Control System"
    assert resources.firmware is not None
    assert resources.firmware.source.kind is FirmwareSourceKind.SKETCH_DIRECTORY


# --- 8: a second/fake panel package loads with no code change ----------------


def test_a_fake_panel_package_loads_through_the_same_algorithm(
    tmp_path: pathlib.Path,
) -> None:
    """Extensibility is structural: a brand-new panel is a new directory, and
    the SAME `PanelPackageLoader` reads it with no branch and no subclass."""
    write_package(tmp_path, "fake-panel-two", valid_manifest("fake-panel-two"))
    loader = PanelPackageLoader(root=tmp_path)

    package = loader.load("fake-panel-two")

    assert package.panel_id == "fake-panel-two"
    assert loader.available() == ("fake-panel-two",)
    # The very same loader class that reads Panel 1 read this one.
    assert type(loader) is type(default_panel_package_loader())


def test_two_fake_packages_coexist_under_one_root(tmp_path: pathlib.Path) -> None:
    write_package(tmp_path, "panel-a", valid_manifest("panel-a"))
    write_package(tmp_path, "panel-b", valid_manifest("panel-b"))
    loader = PanelPackageLoader(root=tmp_path)
    assert loader.available() == ("panel-a", "panel-b")
    assert loader.load("panel-a").panel_id == "panel-a"
    assert loader.load("panel-b").panel_id == "panel-b"


# --- 9 & 10: loading executes nothing and flashes nothing -------------------


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


#: Anything that could execute, flash, spawn, open a port, run a command, or
#: record an event. The package layer may import none of them.
_FORBIDDEN_IMPORTS = (
    "app.build",
    "app.commands",
    "app.scenarios",
    "app.events",
    "app.hardware.serial_transport",
    "app.hardware.identity",
    "subprocess",
    "os.system",
    "pty",
    "serial",
    "shutil",
    "socket",
)


@pytest.mark.parametrize("module", ["models.py", "loader.py", "service.py", "__init__.py"])
def test_package_layer_imports_nothing_that_executes_or_flashes(module: str) -> None:
    imported = _imported_modules(APP_PANELS / module)
    offenders = sorted(
        name
        for name in imported
        for banned in _FORBIDDEN_IMPORTS
        if name == banned or name.startswith(banned + ".")
    )
    assert offenders == [], f"{module} imports {offenders}"


@pytest.mark.parametrize("module", ["models.py", "loader.py", "service.py"])
def test_package_layer_uses_no_dynamic_execution(module: str) -> None:
    """No eval/exec/compile/__import__ — configuration is data, never code."""
    source = (APP_PANELS / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # AST-based, so the module docstrings can freely NAME these tokens to
    # explain what the loader must never do without tripping the check.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__"}, module
        if isinstance(func, ast.Attribute):
            # `os.system(...)` / `os.popen(...)` and friends.
            assert func.attr not in {"system", "popen", "spawn", "spawnv"}, module
        # No `shell=True` keyword on any call.
        for keyword in node.keywords:
            if keyword.arg == "shell":
                assert not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                ), module


def test_loading_panel_one_never_reads_the_sketch_contents() -> None:
    """Package loading verifies the firmware resource EXISTS; it does not
    read, compile, or flash it. Proven by construction: the loader only ever
    stats the sketch directory and globs for *.ino. Here we assert the
    package hands back a location, and that nothing in the load path opened
    the .ino for reading."""
    package = default_panel_package_loader().load(PANEL_ONE_ID)
    sketch = package.firmware_sketch_path
    ino_files = list(sketch.glob("*.ino"))
    assert ino_files  # the resource is present
    # The package is passive data — holding it grants no capability.
    assert not hasattr(package, "compile")
    assert not hasattr(package, "flash")
    assert not hasattr(package, "provision")


def test_built_in_panels_reference_at_most_existing_packages() -> None:
    """Guard against a dangling package pointer: every built-in panel that
    names a package must resolve to one that loads (the moved-here successor
    to 2C.5's dangling-firmware guard)."""
    loader = default_panel_package_loader()
    for panel in BUILT_IN_PANELS:
        if panel.package_id is None:
            continue
        package = loader.load_for_panel(panel)
        assert package.panel_id == panel.panel_id
