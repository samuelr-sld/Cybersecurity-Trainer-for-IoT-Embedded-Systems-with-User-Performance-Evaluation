"""Hack Mode's real ESP32 USB presence gate.

Hack Mode's scenario engine remains simulated and sandboxed. This module does
only one physical operation: ask the same Arduino CLI serial-device detector
used by Build Mode whether an ESP32 candidate is actually connected over USB.
It never flashes, compiles, opens a serial console, or runs student commands
on the host.

The device-selection policy is intentionally delegated to
`app.build.flasher.ArduinoCliFlasher.detect_devices`, so Hack Mode and Build
Mode cannot quietly drift into different definitions of "connected ESP32":
serial-only, identified ESP32 preference, USB-ID preference, then the honest
serial fallback, with ambiguity preserved rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app import config
from app.build.flasher import DeviceDetectRequest, SerialDevice, default_flasher


class HackHardwareStatus(str, Enum):
    """Live USB hardware state for a Hack Mode session."""

    NOT_CHECKED = "not_checked"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True)
class HackHardwareResult:
    """One real Arduino CLI device-discovery result."""

    status: HackHardwareStatus
    device: SerialDevice | None = None
    detail: str = ""


async def detect_hack_hardware() -> HackHardwareResult:
    """Detect the intended ESP32 candidate using Build Mode's detector."""
    outcome = await default_flasher.detect_devices(
        DeviceDetectRequest(
            fqbn=config.HACK_DEVICE_FQBN,
            timeout_seconds=config.HACK_DEVICE_DETECT_TIMEOUT_SECONDS,
        )
    )

    # DeviceDetectOutcome uses the Build Mode failure vocabulary. Empty
    # devices after a successful listing means genuinely nothing connected;
    # other categories mean the detector itself could not establish a
    # trustworthy answer.
    if outcome.category.value != "none":
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
