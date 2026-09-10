"""Minimal configuration for the Hack Mode backend.

Deliberately dependency-free: values come from environment variables with
development defaults, so no settings library is required at this stage.
"""

from __future__ import annotations

import os

SERVICE_NAME = "iot-cybersecurity-trainer"

# --- server ---------------------------------------------------------------

HOST: str = os.getenv("TRAINER_HOST", "127.0.0.1")
PORT: int = int(os.getenv("TRAINER_PORT", "8000"))


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# --- CORS -----------------------------------------------------------------
#
# DEVELOPMENT ONLY. Vite serves the React app on :5173 while FastAPI runs
# separately on :8000, so the browser treats them as different origins. This
# list is an explicit allowlist of local dev origins — it is NOT a production
# configuration. A deployment must set TRAINER_ALLOWED_ORIGINS to the real
# frontend origin(s); wildcard origins are never appropriate here because the
# Hack Mode channel is per-student session state.
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

ALLOWED_ORIGINS: list[str] = _split_origins(
    os.getenv("TRAINER_ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS))
)

# --- protocol limits ------------------------------------------------------
#
# Terminal traffic is untrusted input. These bounds keep a malformed or
# hostile client from forcing unbounded allocation during message parsing.

# Largest accepted raw WebSocket text frame, in characters.
MAX_MESSAGE_CHARS: int = 8192

# Largest accepted `input` payload, in characters.
MAX_INPUT_CHARS: int = 4096

# Accepted terminal geometry bounds for `resize` messages.
MIN_TERMINAL_COLS: int = 1
MAX_TERMINAL_COLS: int = 1000
MIN_TERMINAL_ROWS: int = 1
MAX_TERMINAL_ROWS: int = 1000

# Geometry assumed for a session until the client sends its first resize.
DEFAULT_TERMINAL_COLS: int = 80
DEFAULT_TERMINAL_ROWS: int = 24

# --- Build Mode protocol limits --------------------------------------------
#
# `/ws/build` traffic is untrusted input, same as `/ws/hack`. These bounds
# keep a malformed or hostile client from forcing unbounded allocation while
# editing a firmware region.

# Largest accepted raw WebSocket text frame, in characters.
MAX_BUILD_MESSAGE_CHARS: int = 32768

# Largest accepted submitted region source, in characters. Generous relative
# to `MAX_INPUT_CHARS` because a region holds pasted multi-line C++, not one
# terminal command line.
MAX_BUILD_REGION_SOURCE_CHARS: int = 16384

# Largest accepted `path` / `region_id` identifier, in characters. These
# identify a file/region by name, never by arbitrary length content.
MAX_BUILD_IDENTIFIER_CHARS: int = 128

# --- Build Mode compilation (Phase 3B) -------------------------------------
#
# Real `arduino-cli compile` invocation — see app/build/compiler.py.

# Path or bare command name for the Arduino CLI executable. Defaults to
# relying on PATH, which is the portable expectation; override with
# TRAINER_ARDUINO_CLI_PATH on a machine where the binary isn't on PATH (for
# example, only bundled inside an Arduino IDE install).
ARDUINO_CLI_PATH: str = os.getenv("TRAINER_ARDUINO_CLI_PATH", "arduino-cli")

# Bounded compile timeout, in seconds. A cold-cache ESP32 sketch compile
# commonly takes 30-90s (measured against this project's own reference
# firmware); this leaves headroom for a slower machine without being
# unbounded. A compile that exceeds this is killed and reported as a
# truthful timeout failure — see CompileFailureCategory.TIMEOUT.
BUILD_COMPILE_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_BUILD_COMPILE_TIMEOUT_SECONDS", "180")
)

# --- Build Mode flashing (Phase 3C) ----------------------------------------
#
# Real `arduino-cli board list` / `arduino-cli upload` invocation — see
# app/build/flasher.py. Both share ARDUINO_CLI_PATH above: one configured
# toolchain, chosen by this backend, for every Arduino operation. Nothing a
# client sends can name an executable, a board, a port, or a path.

# Bounded device-discovery timeout, in seconds. `board list` enumerates
# serial ports and returns promptly; this only exists so a wedged CLI or a
# hung USB driver cannot stall a Build session indefinitely.
BUILD_DEVICE_DETECT_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_BUILD_DEVICE_DETECT_TIMEOUT_SECONDS", "20")
)

# Bounded upload timeout, in seconds. An ESP32 upload over a 921600-baud
# USB-UART link commonly takes 15-40s for a sketch this size; this leaves
# generous headroom for a slower bridge without ever being unbounded. An
# upload that exceeds this is killed and reported as a truthful timeout
# failure — see FlashFailureCategory.TIMEOUT. Shorter than the compile
# timeout on purpose: a compile does real work for minutes, an upload that
# has not finished in two minutes is stuck, not busy.
BUILD_FLASH_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_BUILD_FLASH_TIMEOUT_SECONDS", "120")
)
