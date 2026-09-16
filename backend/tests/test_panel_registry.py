"""Phase 2C.5 — panel identification and firmware-configuration foundation.

    connected ESP32 -> DeviceMonitor (MAC) -> PanelRegistry -> PanelDefinition
                                                                   |
                                                         FirmwareConfiguration

Verified here:

A. MAC normalization — every common spelling of one MAC resolves the same.
B. A registered MAC resolves to its `PanelDefinition`.
C. An unknown MAC resolves to an explicit "unregistered" answer, never a panel.
D. The registry is table-driven: a different table resolves with no change
   to the algorithm, and a module can be re-bound without a code change.
E. A resolved panel exposes its `FirmwareConfiguration`, and that
   configuration refuses unsafe shapes at construction time.
F. Separation — the registry and definitions perform no hardware I/O and
   cannot compile or flash; the identification service delegates MAC
   acquisition to the monitor and lookup to the registry.

EVERY MAC HERE IS FAKE. They are locally-administered addresses (`02:...`),
which no manufacturer assigns, so none can collide with a real board. The
one real board MAC in the project is exercised only by
`tests/test_hardware_identity.py`. No esptool runs and no board is touched:
detection and identity probing are doubles throughout.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

from app import config
from app.build.flasher import DeviceDetectOutcome, SerialDevice
from app.hardware import (
    BUILT_IN_PANELS,
    BoardConfiguration,
    CompilationSettings,
    DeviceMonitor,
    DeviceState,
    DeviceStatus,
    FirmwareConfiguration,
    FirmwareSource,
    FirmwareSourceKind,
    FlashSettings,
    IdentityFailure,
    IdentityOutcome,
    PanelDefinition,
    PanelIdentificationService,
    PanelIdentificationStatus,
    PanelMatch,
    PanelRegistry,
    SerialSettings,
    default_panel_registry,
    identify_panel,
    normalize_mac,
)

APP_HARDWARE = pathlib.Path(__file__).resolve().parents[1] / "app" / "hardware"

FQBN = "esp32:esp32:esp32"

MAC_ALPHA = "02:00:00:00:00:0a"
MAC_BRAVO = "02:00:00:00:00:0b"
MAC_SPARE = "02:00:00:00:00:0c"
MAC_UNKNOWN = "02:00:00:00:00:ff"


def run(coro):
    return asyncio.run(coro)


def firmware(firmware_id: str = "alpha-firmware", **overrides) -> FirmwareConfiguration:
    fields = dict(
        firmware_id=firmware_id,
        source=FirmwareSource(FirmwareSourceKind.BUILD_PROJECT, "alpha-project"),
        board=BoardConfiguration(fqbn=FQBN),
    )
    fields.update(overrides)
    return FirmwareConfiguration(**fields)


# A standalone FirmwareConfiguration fixture. Since Phase 2D.1 firmware
# lives in a panel's resource package, not on the `PanelDefinition`, so this
# is exercised by the firmware-shape tests (section E) and by the package
# tests (`tests/test_panel_packages.py`) rather than attached to a panel.
ALPHA_FIRMWARE = firmware()

# ALPHA references a package (an identifier, not a path); BRAVO references
# none — the two states a definition can be in.
ALPHA = PanelDefinition(
    panel_id="test-panel-alpha",
    display_name="TEST PANEL ALPHA",
    mac_addresses=(MAC_ALPHA,),
    package_id="test-panel-alpha",
)
BRAVO = PanelDefinition(
    panel_id="test-panel-bravo",
    display_name="TEST PANEL BRAVO",
    mac_addresses=(MAC_BRAVO,),
)


def make_registry() -> PanelRegistry:
    return PanelRegistry((ALPHA, BRAVO))


# --- A: MAC normalization ---------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        "02:00:00:00:00:0a",
        "02:00:00:00:00:0A",
        "02-00-00-00-00-0A",
        "02-00-00-00-00-0a",
        "  02:00:00:00:00:0a\t",
    ],
)
def test_common_mac_spellings_resolve_to_the_same_panel(spelling: str) -> None:
    resolution = make_registry().resolve(spelling)

    assert resolution.match is PanelMatch.IDENTIFIED
    assert resolution.mac == MAC_ALPHA
    assert resolution.panel is ALPHA


@pytest.mark.parametrize(
    "junk",
    ["", "   ", "COM3", "02:00:00:00:00", "02:00:00:00:00:0a:0b", "0200000000 0a", "zz:00:00:00:00:0a"],
)
def test_things_that_are_not_macs_have_no_identity(junk: str) -> None:
    assert normalize_mac(junk) is None
    resolution = make_registry().resolve(junk)
    assert resolution.match is PanelMatch.NO_IDENTITY
    assert resolution.panel is None
    assert resolution.mac is None


def test_definitions_store_macs_in_canonical_form() -> None:
    panel = PanelDefinition(
        panel_id="spelled-oddly",
        display_name="SPELLED ODDLY",
        mac_addresses=("02-00-00-00-00-0A", "02:00:00:00:00:0a"),
    )
    # Normalized and de-duplicated, so the index holds one spelling.
    assert panel.mac_addresses == (MAC_ALPHA,)


def test_a_malformed_mac_in_a_definition_fails_loudly() -> None:
    """Backend source is trusted; a typo there must fail the suite, not
    silently leave a board unidentifiable."""
    with pytest.raises(ValueError):
        PanelDefinition(panel_id="bad-mac", display_name="BAD", mac_addresses=("not-a-mac",))


# --- B: known panel ---------------------------------------------------------


def test_a_registered_mac_resolves_to_its_panel_definition() -> None:
    resolution = make_registry().resolve(MAC_BRAVO)

    assert resolution.identified is True
    assert resolution.panel is BRAVO
    assert resolution.panel_id == "test-panel-bravo"
    assert resolution.display_name == "TEST PANEL BRAVO"


def test_resolution_is_deterministic() -> None:
    registry = make_registry()
    results = {id(registry.resolve(MAC_ALPHA).panel) for _ in range(20)}
    assert results == {id(ALPHA)}


def test_panels_are_retrievable_by_id() -> None:
    registry = make_registry()
    assert registry.get("test-panel-alpha") is ALPHA
    assert registry.get("no-such-panel") is None
    assert registry.panels == (ALPHA, BRAVO)


# --- C: unknown panel -------------------------------------------------------


def test_an_unknown_mac_is_unregistered_not_a_panel() -> None:
    resolution = make_registry().resolve(MAC_UNKNOWN)

    assert resolution.match is PanelMatch.UNREGISTERED
    assert resolution.identified is False
    assert resolution.panel is None
    assert resolution.display_name is None
    # The MAC is still reported, so the UI can show it instead of a name.
    assert resolution.mac == MAC_UNKNOWN


def test_no_mac_is_no_identity() -> None:
    assert make_registry().resolve(None).match is PanelMatch.NO_IDENTITY


def test_an_empty_registry_resolves_nothing() -> None:
    assert PanelRegistry(()).resolve(MAC_ALPHA).match is PanelMatch.UNREGISTERED


# --- D: extensibility -------------------------------------------------------


def test_an_alternate_table_resolves_with_the_same_registry_class() -> None:
    """A new panel is data. Nothing about `PanelRegistry` changes."""
    charlie = PanelDefinition(
        panel_id="test-panel-charlie",
        display_name="TEST PANEL CHARLIE",
        mac_addresses=(MAC_SPARE,),
        package_id="test-panel-charlie",
    )
    registry = PanelRegistry((ALPHA, BRAVO, charlie))

    assert registry.resolve(MAC_SPARE).panel is charlie
    # Existing panels are unaffected by the addition.
    assert registry.resolve(MAC_ALPHA).panel is ALPHA


def test_a_panel_may_have_several_interchangeable_modules() -> None:
    panel = PanelDefinition(
        panel_id="two-modules",
        display_name="TWO MODULES",
        mac_addresses=(MAC_ALPHA, MAC_SPARE),
    )
    registry = PanelRegistry((panel,))
    assert registry.resolve(MAC_ALPHA).panel is registry.resolve(MAC_SPARE).panel


def test_binding_a_spare_module_to_a_panel() -> None:
    registry = make_registry().bind({MAC_SPARE: "test-panel-bravo"})

    assert registry.resolve(MAC_SPARE).panel_id == "test-panel-bravo"
    assert registry.resolve(MAC_BRAVO).panel_id == "test-panel-bravo"


def test_binding_moves_a_module_between_panels() -> None:
    """Physically swapping an ESP32 to another panel is a re-binding."""
    registry = make_registry().bind({MAC_ALPHA: "test-panel-bravo"})

    assert registry.resolve(MAC_ALPHA).panel_id == "test-panel-bravo"
    assert MAC_ALPHA not in registry.get("test-panel-alpha").mac_addresses
    # The original registry is immutable and unchanged.
    assert make_registry().resolve(MAC_ALPHA).panel_id == "test-panel-alpha"


def test_binding_cannot_create_a_panel() -> None:
    with pytest.raises(ValueError):
        make_registry().bind({MAC_SPARE: "invented-panel"})


def test_one_mac_cannot_belong_to_two_panels() -> None:
    clash = PanelDefinition(panel_id="clash", display_name="CLASH", mac_addresses=(MAC_ALPHA,))
    with pytest.raises(ValueError):
        PanelRegistry((ALPHA, clash))


def test_panel_ids_are_unique() -> None:
    duplicate = PanelDefinition(panel_id="test-panel-alpha", display_name="AGAIN")
    with pytest.raises(ValueError):
        PanelRegistry((ALPHA, duplicate))


def test_environment_bindings_reach_the_default_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PANEL_MACS_RAW", f"{MAC_SPARE}=emergency-exit-lighting")

    resolution = default_panel_registry().resolve(MAC_SPARE)

    assert resolution.panel_id == "emergency-exit-lighting"


def test_the_built_in_registry_is_valid_and_covers_the_five_panel_scope() -> None:
    registry = default_panel_registry()
    assert len(registry.panels) == 5
    assert len({panel.panel_id for panel in BUILT_IN_PANELS}) == 5


def test_built_in_package_references_are_identifiers_not_paths() -> None:
    """Since Phase 2D.1 a definition points at a resource PACKAGE, by id.
    Firmware no longer lives on the definition, so the old dangling
    BUILD_PROJECT guard moved to the package layer
    (`tests/test_panel_packages.py`). Here we only assert the definition
    field is a safe identifier — never a path — for every built-in panel
    that has one."""
    import re

    from app.hardware.firmware import IDENTIFIER_PATTERN

    for panel in BUILT_IN_PANELS:
        assert not hasattr(panel, "firmware")
        if panel.package_id is not None:
            assert re.fullmatch(IDENTIFIER_PATTERN, panel.package_id), panel.panel_id


def test_only_panel_one_has_a_package_on_main() -> None:
    """Exactly one built-in panel references a package today (Panel 1). The
    other four honestly carry `package_id=None` — courseware not yet written."""
    with_package = [p.panel_id for p in BUILT_IN_PANELS if p.package_id is not None]
    assert with_package == ["smart-home-mqtt-control"]


# --- E: firmware configuration ---------------------------------------------


def test_a_resolved_panel_exposes_its_package_reference() -> None:
    # Firmware moved to the package (Phase 2D.1); a definition now only
    # names which package holds it, by id.
    panel = make_registry().resolve(MAC_ALPHA).panel

    assert panel.package_id == "test-panel-alpha"
    assert not hasattr(panel, "firmware")


def test_a_panel_without_a_package_says_so() -> None:
    assert make_registry().resolve(MAC_BRAVO).panel.package_id is None


def test_firmware_configuration_can_describe_every_provisioning_stage() -> None:
    described = FirmwareConfiguration(
        firmware_id="full-description",
        source=FirmwareSource(FirmwareSourceKind.SKETCH_DIRECTORY, "panels/alpha/sketch"),
        board=BoardConfiguration(fqbn="esp32:esp32:esp32:PartitionScheme=default"),
        compilation=CompilationSettings(build_properties=("build.extra_flags=-DPANEL_ALPHA",)),
        flashing=FlashSettings(verify=True),
        serial=SerialSettings(baud_rate=9600),
    )
    assert described.serial.baud_rate == 9600
    assert described.flashing.verify is True


def test_firmware_configuration_is_immutable() -> None:
    with pytest.raises(AttributeError):
        ALPHA_FIRMWARE.firmware_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "reference",
    ["../outside", "panels/../../etc", "/abs/sketch", "C:\\sketch", "C:/sketch", "panels\\alpha", "./here", ""],
)
def test_a_sketch_path_cannot_escape_its_root(reference: str) -> None:
    with pytest.raises(ValueError):
        FirmwareSource(FirmwareSourceKind.SKETCH_DIRECTORY, reference)


@pytest.mark.parametrize("reference", ["Has Spaces", "../x", "UPPER", "", "a/b"])
def test_a_build_project_reference_must_be_an_identifier(reference: str) -> None:
    with pytest.raises(ValueError):
        FirmwareSource(FirmwareSourceKind.BUILD_PROJECT, reference)


@pytest.mark.parametrize("fqbn", ["esp32", "esp32:esp32", "::", "--fqbn", "esp32:esp32:esp32 --port COM1"])
def test_an_invalid_fqbn_is_rejected(fqbn: str) -> None:
    with pytest.raises(ValueError):
        BoardConfiguration(fqbn=fqbn)


@pytest.mark.parametrize("prop", ["--verbose", "-Dx=1", "no_equals", "key=value\nsecond"])
def test_a_build_property_cannot_masquerade_as_a_flag(prop: str) -> None:
    with pytest.raises(ValueError):
        CompilationSettings(build_properties=(prop,))


@pytest.mark.parametrize("baud", [0, -115200, True, "115200"])
def test_an_invalid_baud_rate_is_rejected(baud) -> None:
    with pytest.raises(ValueError):
        SerialSettings(baud_rate=baud)


# --- F: separation ----------------------------------------------------------


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


#: Anything that can reach hardware, a process, the filesystem, a scenario,
#: or the event log. The registry-side modules may import none of them.
_IO_OR_BEHAVIOUR_MODULES = (
    "app.build",
    "app.scenarios",
    "app.events",
    "app.commands",
    "app.hardware.identity",
    "app.hardware.monitor",
    "app.hardware.serial_transport",
    "app.hardware.panel_identification",
    "asyncio",
    "glob",
    "os",
    "serial",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
)


@pytest.mark.parametrize("module", ["panels.py", "firmware.py", "mac.py"])
def test_registry_modules_import_nothing_that_performs_io(module: str) -> None:
    imported = _imported_modules(APP_HARDWARE / module)
    offenders = sorted(
        name
        for name in imported
        for banned in _IO_OR_BEHAVIOUR_MODULES
        if name == banned or name.startswith(banned + ".")
    )
    assert offenders == [], f"{module} imports {offenders}"


def test_identification_imports_no_build_scenario_or_event_code() -> None:
    """Identifying a panel cannot compile, flash, start a scenario, or record."""
    imported = _imported_modules(APP_HARDWARE / "panel_identification.py")
    for banned in ("app.build", "app.scenarios", "app.events", "app.commands"):
        assert not any(name == banned or name.startswith(banned + ".") for name in imported)


_ACTION_WORDS = ("compile", "flash", "upload", "provision", "probe", "read_mac", "detect", "open")


@pytest.mark.parametrize(
    "cls",
    [
        PanelDefinition,
        PanelRegistry,
        FirmwareConfiguration,
        FirmwareSource,
        BoardConfiguration,
        CompilationSettings,
        FlashSettings,
        SerialSettings,
    ],
)
def test_definitions_and_registry_have_no_actions(cls) -> None:
    """No coroutine, and no callable named for a toolchain or hardware action."""
    for name, member in inspect.getmembers(cls):
        if name.startswith("__") or not callable(member):
            continue
        assert not inspect.iscoroutinefunction(member), f"{cls.__name__}.{name} is async"
        assert not any(word in name.lower() for word in _ACTION_WORDS), f"{cls.__name__}.{name}"


class SpyRegistry(PanelRegistry):
    """A real registry that records every lookup it is asked for."""

    def __init__(self, panels) -> None:
        super().__init__(panels)
        self.lookups: list[str | None] = []

    def resolve(self, mac):
        self.lookups.append(mac)
        return super().resolve(mac)


class FakeProbe:
    def __init__(self, mac: str | None = MAC_ALPHA) -> None:
        self.mac = mac
        self.calls = 0

    async def read_mac(self, request) -> IdentityOutcome:
        self.calls += 1
        await asyncio.sleep(0)
        if self.mac is None:
            return IdentityOutcome(category=IdentityFailure.UNREACHABLE)
        return IdentityOutcome(mac=self.mac)


class DetectOnlyAdapter:
    """Detection double that fails the test if anything tries to flash."""

    def __init__(self, *devices: SerialDevice) -> None:
        self.outcome = DeviceDetectOutcome(devices=devices)
        self.detections = 0

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        self.detections += 1
        await asyncio.sleep(0)
        return self.outcome

    async def run_flash(self, _request):  # pragma: no cover - must never run
        raise AssertionError("panel identification must never flash")


def esp32(port: str = "COM3") -> SerialDevice:
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn=FQBN,
        has_usb_id=True,
    )


def service_for(*devices: SerialDevice, probe: FakeProbe | None = None, registry=None):
    adapter = DetectOnlyAdapter(*devices)
    probe = probe if probe is not None else FakeProbe()
    registry = registry if registry is not None else SpyRegistry((ALPHA, BRAVO))
    monitor = DeviceMonitor(detector=adapter, identity_probe=probe, panel_registry=registry)
    return PanelIdentificationService(monitor=monitor), monitor, adapter, probe, registry


def test_the_service_delegates_lookup_to_the_registry() -> None:
    service, _, _, _, registry = service_for(esp32())
    run(service.refresh())
    registry.lookups.clear()

    identification = service.identify()

    assert registry.lookups == [MAC_ALPHA]
    assert identification.panel is ALPHA


def test_the_service_delegates_mac_acquisition_to_the_monitor() -> None:
    """One probe, through the monitor's per-port cache — no second path."""
    service, monitor, _, probe, _ = service_for(esp32())

    for _ in range(3):
        run(service.refresh())
    service.identify()

    assert probe.calls == 1
    assert monitor.snapshot().mac == MAC_ALPHA


def test_identify_performs_no_detection_and_no_probe() -> None:
    """`identify()` reads the monitor's cached state and nothing else — so on
    a monitor that has never detected, the honest answer is NOT_CHECKED.

    It is emphatically NOT_CONNECTED: a board IS attached in this fixture,
    and reporting "nothing is plugged in" because nobody has looked yet is
    the invention `DeviceStatus.NOT_CHECKED` exists to prevent."""
    service, _, adapter, probe, _ = service_for(esp32())

    identification = service.identify()

    assert adapter.detections == 0
    assert probe.calls == 0
    assert identification.status is PanelIdentificationStatus.NOT_CHECKED


def test_the_header_name_and_the_service_answer_come_from_one_registry() -> None:
    service, monitor, _, _, _ = service_for(esp32())

    identification = run(service.refresh())

    assert monitor.snapshot().panel == identification.panel.display_name == "TEST PANEL ALPHA"


# --- identification outcomes ------------------------------------------------


def test_an_identified_board_exposes_its_panel_and_package() -> None:
    service, _, _, _, _ = service_for(esp32(port="COM7"))

    identification = run(service.refresh())

    assert identification.status is PanelIdentificationStatus.IDENTIFIED
    assert identification.identified is True
    assert identification.port == "COM7"
    assert identification.mac == MAC_ALPHA
    assert identification.panel is ALPHA
    # Firmware moved to the package (Phase 2D.1); identification exposes the
    # package reference, and PanelResources is the route to the firmware.
    assert identification.package_id == "test-panel-alpha"


def test_an_unknown_board_is_unregistered() -> None:
    service, monitor, _, _, _ = service_for(esp32(), probe=FakeProbe(mac=MAC_UNKNOWN))

    identification = run(service.refresh())

    assert identification.status is PanelIdentificationStatus.UNREGISTERED
    assert identification.mac == MAC_UNKNOWN
    assert identification.panel is None
    assert identification.package_id is None
    # The header keeps showing the MAC and no invented name.
    assert monitor.snapshot().panel is None


def test_a_board_whose_mac_could_not_be_read_is_unidentified_not_unknown() -> None:
    service, _, _, _, _ = service_for(esp32(), probe=FakeProbe(mac=None))

    identification = run(service.refresh())

    assert identification.status is PanelIdentificationStatus.UNIDENTIFIED
    assert identification.port == "COM3"
    assert identification.mac is None


def test_no_board_is_not_connected() -> None:
    service, _, _, probe, _ = service_for()

    identification = run(service.refresh())

    assert identification.status is PanelIdentificationStatus.NOT_CONNECTED
    assert identification.panel is None
    assert probe.calls == 0


def test_identification_during_a_flash_hold_does_not_probe() -> None:
    service, monitor, _, probe, _ = service_for(esp32())

    with monitor.hold_identity_probe():
        identification = run(service.refresh())

    assert probe.calls == 0
    assert identification.status is PanelIdentificationStatus.UNIDENTIFIED


def test_a_panel_without_a_package_is_identified_with_no_package() -> None:
    service, _, _, _, _ = service_for(esp32(), probe=FakeProbe(mac=MAC_BRAVO))

    identification = run(service.refresh())

    assert identification.identified is True
    assert identification.panel is BRAVO
    assert identification.package_id is None


@pytest.mark.parametrize(
    "status",
    [DeviceStatus.DISCONNECTED, DeviceStatus.AMBIGUOUS, DeviceStatus.ERROR],
)
def test_identify_panel_needs_a_single_connected_board(status: DeviceStatus) -> None:
    """A detection RAN and there is no single board to identify."""
    state = DeviceState(status=status, mac=MAC_ALPHA)
    assert identify_panel(state, make_registry()).status is PanelIdentificationStatus.NOT_CONNECTED


def test_a_monitor_that_has_not_detected_is_not_checked_not_disconnected() -> None:
    """The distinction the device layer makes must survive the panel layer:
    "we have not looked" is its own answer, never "nothing is there"."""
    state = DeviceState(status=DeviceStatus.NOT_CHECKED, mac=MAC_ALPHA)
    identification = identify_panel(state, make_registry())
    assert identification.status is PanelIdentificationStatus.NOT_CHECKED
    assert identification.panel is None


def test_the_first_in_flight_detection_is_also_not_checked() -> None:
    """DETECTING with no previous verdict behind it is still "nobody has
    looked" — there is no earlier answer to fall back on."""
    state = DeviceState(status=DeviceStatus.DETECTING)
    assert identify_panel(state, make_registry()).status is (
        PanelIdentificationStatus.NOT_CHECKED
    )


def test_identification_never_mutates_the_device_state() -> None:
    state = DeviceState(status=DeviceStatus.CONNECTED, port="COM3", mac=MAC_ALPHA)
    before = state.snapshot()

    identify_panel(state, make_registry())

    assert state.snapshot() == before
