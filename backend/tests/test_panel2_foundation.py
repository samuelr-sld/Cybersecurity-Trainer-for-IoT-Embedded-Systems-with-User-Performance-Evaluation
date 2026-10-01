"""Panel 2 (Environmental Monitoring) FOUNDATION — identity, package, firmware.

Panel 2's package establishes who the board is and what firmware it runs. It
deliberately defines no training scenario, vulnerability, remediation or
evaluation yet; those are later phases, so nothing here asserts them (or their
absence). Everything below is the existing registry/package machinery driven
over Panel 2's data — there is no Panel-2-specific code path to test.

Hardware-free: the board is a constructed `DeviceState`, and the firmware is
inspected as text. Compiling it for real is a toolchain check, not part of the
ordinary run.
"""

from __future__ import annotations

import re

import pytest

from app.hardware.firmware import FirmwareSourceKind
from app.hardware.panel_identification import PanelIdentificationStatus, identify_panel
from app.hardware.panels import BUILT_IN_PANELS, PanelMatch, PanelRegistry
from app.hardware.state import DeviceState, DeviceStatus
from app.panels import PanelResourceStatus, default_panel_package_loader
from tests.test_panel_packages import service_over

PANEL_TWO_ID = "environmental-monitoring"
PANEL_TWO_MAC = "20:50:0d:4d:4e:a8"
PANEL_ONE_ID = "smart-home-mqtt-control"
PANEL_ONE_MAC = "20:9b:a9:88:0b:e4"


@pytest.fixture(scope="module")
def package():
    return default_panel_package_loader().load(PANEL_TWO_ID)


@pytest.fixture(scope="module")
def sketch_text(package) -> str:
    sketch_dir = package.firmware_sketch_path
    assert sketch_dir is not None
    return (sketch_dir / f"{sketch_dir.name}.ino").read_text(encoding="utf-8")


# --- registry identity -----------------------------------------------------


def test_panel_two_mac_resolves_to_panel_two() -> None:
    resolution = PanelRegistry(BUILT_IN_PANELS).resolve(PANEL_TWO_MAC)

    assert resolution.match is PanelMatch.IDENTIFIED
    assert resolution.panel.panel_id == PANEL_TWO_ID
    assert resolution.panel.display_name == "ENVIRONMENTAL MONITORING SYSTEM"
    assert resolution.panel.package_id == PANEL_TWO_ID


def test_panel_two_mac_resolves_in_any_spelling() -> None:
    registry = PanelRegistry(BUILT_IN_PANELS)
    for spelling in ("20:50:0D:4D:4E:A8", "20-50-0d-4d-4e-a8", "  20:50:0d:4d:4e:a8 "):
        assert registry.resolve(spelling).panel.panel_id == PANEL_TWO_ID, spelling


def test_panel_one_is_still_bound_to_its_own_board() -> None:
    resolution = PanelRegistry(BUILT_IN_PANELS).resolve(PANEL_ONE_MAC)

    assert resolution.match is PanelMatch.IDENTIFIED
    assert resolution.panel.panel_id == PANEL_ONE_ID
    assert resolution.panel.package_id == PANEL_ONE_ID


def test_the_two_boards_are_distinct_panels() -> None:
    registry = PanelRegistry(BUILT_IN_PANELS)
    assert registry.resolve(PANEL_ONE_MAC).panel is not registry.resolve(PANEL_TWO_MAC).panel


# --- MAC -> panel -> package -> firmware ------------------------------------


def test_the_attached_board_resolves_all_the_way_to_a_ready_panel_two() -> None:
    state = DeviceState(status=DeviceStatus.CONNECTED, port="COM3", mac=PANEL_TWO_MAC)

    identification = identify_panel(state, PanelRegistry(BUILT_IN_PANELS))
    assert identification.status is PanelIdentificationStatus.IDENTIFIED
    assert identification.port == "COM3"

    resources = service_over(identification, default_panel_package_loader()).resolve()

    assert resources.status is PanelResourceStatus.READY, resources.detail
    assert resources.package.panel_id == PANEL_TWO_ID
    assert resources.scenario.scenario_id == "environmental-sensing"
    assert resources.firmware.firmware_id == "environmental-monitoring-firmware"


# --- the package ------------------------------------------------------------


def test_package_identity(package) -> None:
    assert package.panel_id == PANEL_TWO_ID
    assert package.title == "Environmental Monitoring System"
    # Not the legacy scenario id: that one still names the MQTT/BME280 default.
    assert package.scenario_id == "environmental-sensing"


def test_package_firmware_configuration(package) -> None:
    firmware = package.firmware

    assert firmware.firmware_id == "environmental-monitoring-firmware"
    assert firmware.source.kind is FirmwareSourceKind.SKETCH_DIRECTORY
    assert firmware.source.reference == "firmware/environmental_monitoring"
    assert firmware.board.fqbn == "esp32:esp32:esp32"
    assert firmware.compilation.build_properties == ()
    assert firmware.serial.baud_rate == 115200


def test_package_sketch_resource_is_a_valid_arduino_sketch(package) -> None:
    sketch_dir = package.firmware_sketch_path

    assert sketch_dir is not None and sketch_dir.is_dir()
    # An Arduino sketch's folder and main file share a name.
    assert (sketch_dir / f"{sketch_dir.name}.ino").is_file()
    assert [p.name for p in sketch_dir.iterdir()] == [f"{sketch_dir.name}.ino"]


# --- the firmware matches the panel's hardware specification -----------------


@pytest.mark.parametrize(
    "fragment",
    [
        r"#define\s+DHTPIN\s+4\b",
        r"#define\s+DHTTYPE\s+DHT11\b",
        r"#define\s+LED_PIN\s+5\b",
        r"#define\s+FAN_PIN\s+25\b",
        r"#define\s+SCREEN_WIDTH\s+128\b",
        r"#define\s+SCREEN_HEIGHT\s+64\b",
        r"#define\s+SCREEN_ADDRESS\s+0x3C\b",
        r"TEMP_THRESHOLD\s*=\s*31\.0\s*;",
        r"READ_INTERVAL\s*=\s*2000\s*;",
        r"BLINK_INTERVAL\s*=\s*500\s*;",
        r"Serial\.begin\(\s*115200\s*\)",
    ],
)
def test_sketch_declares_the_specified_constants(sketch_text: str, fragment: str) -> None:
    assert re.search(fragment, sketch_text), fragment


def test_sketch_baud_rate_agrees_with_the_package(package, sketch_text: str) -> None:
    """One baud rate in two places must not be able to drift apart."""
    declared = int(re.search(r"Serial\.begin\(\s*(\d+)\s*\)", sketch_text).group(1))
    assert declared == package.firmware.serial.baud_rate


def test_sketch_includes_only_the_sensor_display_libraries(sketch_text: str) -> None:
    includes = re.findall(r"^\s*#include\s*<([^>]+)>", sketch_text, flags=re.MULTILINE)
    assert includes == ["Wire.h", "Adafruit_GFX.h", "Adafruit_SSD1306.h", "DHT.h"]


def test_sketch_has_no_networking(sketch_text: str) -> None:
    """Panel 2 is a USB-serial device: no Wi-Fi and no MQTT, anywhere."""
    lowered = sketch_text.lower()
    for forbidden in ("wifi", "pubsubclient", "mqtt", "esp_now", "httpclient"):
        assert forbidden not in lowered, forbidden
