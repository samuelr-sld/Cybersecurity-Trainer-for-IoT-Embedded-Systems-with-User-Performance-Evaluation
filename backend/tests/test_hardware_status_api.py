"""`GET /api/hardware/status` — the Main Menu's read-only window on the device layer.

What these pin, in order of how easily a later "helpful" change would break it:

1. The PANEL verdict comes from MAC -> registry, never from the USB port. Two
   different boards behind the SAME port name must come out as different
   panels, and a board whose MAC is bound to nothing is `unregistered`, not a
   guess.
2. The four things a board can be are told apart on the wire (identified /
   unregistered / unidentified / not_connected), and USB reports the REAL port
   (not the canonical training alias) or nothing.
3. It is lightweight: polling it never re-reads a MAC that is already known
   (reading one resets the board), and concurrent polls share one detection.
4. It is silent: no session, no command, no event, and the module reaches for
   nothing that can run a process or change the board.

No subprocess is spawned and no board is needed: the shared monitor's detector
and identity probe are replaced with doubles for the duration of each test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build.flasher import DeviceDetectOutcome, FlashFailureCategory, SerialDevice
from app.hardware import IdentityFailure, IdentityOutcome, device_monitor
from app.main import app
from app.sessions import session_manager

FQBN = "esp32:esp32:esp32"

#: Real bindings from `app/hardware/panels.py`, so the real table is exercised.
PANEL_1_MAC = "20:9b:a9:88:0b:e4"
PANEL_1_NAME = "SMART HOME MQTT CONTROL SYSTEM"
PANEL_2_MAC = "20:50:0d:4d:4e:a8"
PANEL_2_NAME = "ENVIRONMENTAL MONITORING SYSTEM"
UNBOUND_MAC = "aa:bb:cc:dd:ee:ff"


def _esp32(port: str = "COM7") -> SerialDevice:
    return SerialDevice(
        port=port,
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn=FQBN,
        has_usb_id=True,
    )


class _FakeDetector:
    """Canned detection, no subprocess. Counts calls."""

    def __init__(self, outcome: DeviceDetectOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        self.calls += 1
        return self.outcome


class _FakeProbe:
    """Canned MAC, no esptool, no board reset. Counts calls (each one is a reset)."""

    def __init__(self, mac: str | None = PANEL_1_MAC) -> None:
        self.mac = mac
        self.calls = 0

    async def read_mac(self, _request) -> IdentityOutcome:
        self.calls += 1
        if self.mac is None:
            return IdentityOutcome(category=IdentityFailure.UNREACHABLE)
        return IdentityOutcome(mac=self.mac)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(device_monitor, "_identity_probe", fake)
    return fake


@pytest.fixture
def detector(monkeypatch: pytest.MonkeyPatch, probe: _FakeProbe) -> _FakeDetector:
    """Point the SHARED monitor at one fake ESP32; no freshness cache by default."""
    fake = _FakeDetector(DeviceDetectOutcome(devices=(_esp32(),)))
    monkeypatch.setattr(device_monitor, "_detector", fake)
    monkeypatch.setattr(device_monitor, "_cache_seconds", 0.0)
    device_monitor.reset()
    return fake


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _status(client: TestClient) -> dict:
    response = client.get("/api/hardware/status")
    assert response.status_code == 200
    return response.json()


# --- 1: the panel is the MAC's, never the port's -----------------------------


def test_a_registered_panel_is_named_from_its_mac(
    client: TestClient, detector: _FakeDetector
) -> None:
    body = _status(client)

    assert body["panel"] == {
        "status": "identified",
        "name": PANEL_1_NAME,
        "mac": PANEL_1_MAC,
    }
    assert body["usb"] == {"connected": True, "port": "COM7"}


def test_the_same_port_with_a_different_mac_is_a_different_panel(
    client: TestClient, detector: _FakeDetector, probe: _FakeProbe
) -> None:
    """A port names a USB bridge, not a panel: it can never decide the name."""
    first = _status(client)
    assert first["panel"]["name"] == PANEL_1_NAME

    # Another board behind an identical bridge: same port, different MAC.
    device_monitor.reset()
    probe.mac = PANEL_2_MAC
    second = _status(client)

    assert first["usb"]["port"] == second["usb"]["port"] == "COM7"
    assert second["panel"]["name"] == PANEL_2_NAME
    assert second["panel"]["mac"] == PANEL_2_MAC


def test_a_board_whose_mac_is_bound_to_no_panel_is_unregistered_not_guessed(
    client: TestClient, detector: _FakeDetector, probe: _FakeProbe
) -> None:
    probe.mac = UNBOUND_MAC

    body = _status(client)

    assert body["panel"] == {"status": "unregistered", "name": None, "mac": UNBOUND_MAC}
    # The board is still there and its port is still the real one.
    assert body["usb"] == {"connected": True, "port": "COM7"}


def test_a_board_whose_mac_could_not_be_read_is_unidentified_not_unregistered(
    client: TestClient, detector: _FakeDetector, probe: _FakeProbe
) -> None:
    """"We could not read it" and "it is bound to nothing" are different answers."""
    probe.mac = None

    body = _status(client)

    assert body["panel"] == {"status": "unidentified", "name": None, "mac": None}
    assert body["usb"] == {"connected": True, "port": "COM7"}


# --- 2: no device, and the USB field's honesty --------------------------------


def test_nothing_attached_is_no_device_and_a_disconnected_usb(
    client: TestClient, detector: _FakeDetector
) -> None:
    detector.outcome = DeviceDetectOutcome(devices=())

    body = _status(client)

    assert body["panel"] == {"status": "not_connected", "name": None, "mac": None}
    assert body["usb"] == {"connected": False, "port": None}


def test_a_broken_toolchain_never_reports_a_panel_or_a_port(
    client: TestClient, detector: _FakeDetector
) -> None:
    detector.outcome = DeviceDetectOutcome(
        category=FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
    )

    body = _status(client)

    assert body["panel"]["status"] == "not_connected"
    assert body["panel"]["name"] is None
    assert body["usb"] == {"connected": False, "port": None}


def test_unplugging_clears_the_panel_and_the_port(
    client: TestClient, detector: _FakeDetector
) -> None:
    assert _status(client)["panel"]["status"] == "identified"

    detector.outcome = DeviceDetectOutcome(devices=())
    body = _status(client)

    assert body["panel"] == {"status": "not_connected", "name": None, "mac": None}
    assert body["usb"] == {"connected": False, "port": None}


def test_the_usb_field_is_the_real_port_not_the_training_alias(
    client: TestClient, detector: _FakeDetector
) -> None:
    """`/dev/ttyUSB0` is a label for the courseware, never what the board is on."""
    detector.outcome = DeviceDetectOutcome(devices=(_esp32("COM3"),))

    body = _status(client)

    assert body["usb"]["port"] == "COM3"


# --- 3: lightweight -----------------------------------------------------------


def test_polling_never_re_reads_a_known_mac(
    client: TestClient, detector: _FakeDetector, probe: _FakeProbe
) -> None:
    """Every MAC read resets the board: a menu left open must not do it again."""
    for _ in range(5):
        assert _status(client)["panel"]["name"] == PANEL_1_NAME

    assert probe.calls == 1


def test_polls_inside_the_monitors_freshness_window_share_one_detection(
    client: TestClient,
    detector: _FakeDetector,
    probe: _FakeProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(device_monitor, "_cache_seconds", 60.0)

    for _ in range(5):
        _status(client)

    assert detector.calls == 1
    assert probe.calls == 1


# --- 4: silent, and structurally unable to do anything else -------------------


def test_asking_for_status_creates_no_session(
    client: TestClient, detector: _FakeDetector
) -> None:
    _status(client)

    assert not session_manager._sessions


def test_the_module_imports_nothing_that_can_run_a_process_or_change_the_board() -> None:
    source = Path(__file__).resolve().parents[1] / "app" / "hardware_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in ("subprocess", "app.build", "app.scenarios", "app.events", "app.sessions"):
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.") for name in imported
        ), f"hardware_api must not import {forbidden}"

    text = source.read_text(encoding="utf-8")
    assert "reverify_identity=True" not in text
    assert "forget_identity" not in text
