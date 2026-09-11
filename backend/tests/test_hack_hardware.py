import pytest

from app import hack_hardware
from app.build.flasher import DeviceDetectOutcome, FlashFailureCategory, SerialDevice


@pytest.mark.asyncio
async def test_no_device_locks_hack_mode(monkeypatch):
    async def fake_detect(request):
        return DeviceDetectOutcome(category=FlashFailureCategory.NONE, devices=())

    monkeypatch.setattr(hack_hardware.default_flasher, "detect_devices", fake_detect)

    result = await hack_hardware.detect_hack_hardware()

    assert result.status is hack_hardware.HackHardwareStatus.DISCONNECTED
    assert result.device is None


@pytest.mark.asyncio
async def test_one_usb_esp32_unlocks_hack_mode(monkeypatch):
    device = SerialDevice(
        port="COM7",
        protocol="serial",
        board_name="ESP32 Dev Module",
        board_fqbn="esp32:esp32:esp32",
        has_usb_id=True,
    )

    async def fake_detect(request):
        assert request.fqbn == "esp32:esp32:esp32"
        return DeviceDetectOutcome(
            category=FlashFailureCategory.NONE,
            devices=(device,),
        )

    monkeypatch.setattr(hack_hardware.default_flasher, "detect_devices", fake_detect)

    result = await hack_hardware.detect_hack_hardware()

    assert result.status is hack_hardware.HackHardwareStatus.CONNECTED
    assert result.device is device


@pytest.mark.asyncio
async def test_multiple_candidates_are_ambiguous(monkeypatch):
    devices = (
        SerialDevice(port="COM7", has_usb_id=True),
        SerialDevice(port="COM8", has_usb_id=True),
    )

    async def fake_detect(request):
        return DeviceDetectOutcome(
            category=FlashFailureCategory.NONE,
            devices=devices,
        )

    monkeypatch.setattr(hack_hardware.default_flasher, "detect_devices", fake_detect)

    result = await hack_hardware.detect_hack_hardware()

    assert result.status is hack_hardware.HackHardwareStatus.AMBIGUOUS
    assert result.device is None


@pytest.mark.asyncio
async def test_detector_error_does_not_look_like_no_device(monkeypatch):
    async def fake_detect(request):
        return DeviceDetectOutcome(category=FlashFailureCategory.INTERNAL_ERROR)

    monkeypatch.setattr(hack_hardware.default_flasher, "detect_devices", fake_detect)

    result = await hack_hardware.detect_hack_hardware()

    assert result.status is hack_hardware.HackHardwareStatus.ERROR
    assert result.detail == "internal_error"
