"""Configuration for Hack Mode's physical ESP32 gate."""

from __future__ import annotations

import os

ARDUINO_CLI_PATH = os.getenv("TRAINER_ARDUINO_CLI_PATH", "arduino-cli")
DEVICE_FQBN = os.getenv("TRAINER_HACK_DEVICE_FQBN", "esp32:esp32:esp32")
DEVICE_DETECT_TIMEOUT_SECONDS = float(
    os.getenv("TRAINER_HACK_DEVICE_DETECT_TIMEOUT_SECONDS", "20")
)
