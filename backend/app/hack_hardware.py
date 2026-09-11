"""Hack Mode's real ESP32 USB presence gate.

This module performs only physical-device discovery. It never flashes,
compiles, opens a serial console, or executes student commands on the host.
The candidate-selection policy is reused from Build Mode's Arduino CLI
flasher so both modes share the same definition of an attached ESP32.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.build.flasher import (
    DeviceDetectRequest,
    FlashFailureCategory,
    SerialDevice,
    default_flasher,
)
from app.hack_config import DEVICE_DETECT_TIMEOUT_SECONDS, DEVICE_FQBN


class HackHardwareStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True)
class HackHardwareResult:
    status: HackHardwareStatus
    device: SerialDevice | None = None
    detail: str = ""


async def detect_hack_hardware() -> HackHardwareResult:
    """Detect one ESP32 candidate using Build Mode's real detector."""
    outcome = await default_flasher.detect_devices(
        DeviceDetectRequest(
            fqbn=DEVICE_FQBN,
            timeout_seconds=DEVICE_DETECT_TIMEOUT_SECONDS,
        )
    )

    if outcome.category is not FlashFailureCategory.NONE:
        return HackHardwareResult(
            status=HackHardwareStatus.ERROR,
            detail=outcome.category.value,
        )

    if not outcome.devices:
        return HackHardwareResult(
            status=HackHardwareStatus.DISCONNECTED,
            detail="no ESP32 candidate detected",
        )

    if len(outcome.devices) > 1:
        return HackHardwareResult(
            status=HackHardwareStatus.AMBIGUOUS,
            detail="more than one ESP32 candidate detected",
        )

    return HackHardwareResult(
        status=HackHardwareStatus.CONNECTED,
        device=outcome.devices[0],
    )
