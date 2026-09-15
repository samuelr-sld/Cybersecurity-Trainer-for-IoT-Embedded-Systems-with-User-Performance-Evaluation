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

# --- shared hardware layer (Phase 1) ---------------------------------------
#
# Platform-level ESP32 presence — see app/hardware/. These describe the one
# physical board attached to this machine, which both Build Mode and Hack
# Mode read through `device_monitor`; they belong to neither mode.

# Board target the shared monitor detects against when no caller names one
# (Hack Mode does not; Build Mode passes its session's own project board).
# Used only to *prefer* a port the Arduino CLI positively identified as this
# platform — never as a filter, and never supplied by a frontend. See
# app/build/flasher.py::_select_candidates.
HARDWARE_TARGET_FQBN: str = os.getenv("TRAINER_HARDWARE_TARGET_FQBN", "esp32:esp32:esp32")

# Label used when a device is connected but the Arduino CLI could not name
# the board — the common case for a classic ESP32 behind a generic
# CP2102/CH340 USB-UART bridge. This is a backend-chosen fallback, not a
# hardcoded frontend string, and it is never used to claim a board is
# connected: it only names one that detection already found.
HARDWARE_BOARD_LABEL: str = os.getenv("TRAINER_HARDWARE_BOARD_LABEL", "ESP32")

# How long a completed detection is reused before the shared monitor runs
# `arduino-cli board list` again. Deliberately far shorter than the
# frontend's poll interval (10s, see src/hardware/deviceState.js), so this
# only ever collapses *concurrent* polls from several sessions into one CLI
# invocation — a single poller still gets a genuinely fresh answer on every
# tick, and an unplug is never hidden for longer than this.
HARDWARE_CACHE_SECONDS: float = float(
    os.getenv("TRAINER_HARDWARE_CACHE_SECONDS", "4")
)

# Canonical Linux/training serial path — see app/hardware/serial_alias.py.
# The trainer deploys to a Raspberry Pi, so the courseware and Hack Mode
# command examples speak one stable Linux path regardless of what the
# development host enumerates the same board as ("COM3" on Windows). This is
# a student-facing LABEL and a command target only: it is resolved back to
# the real `DeviceState.port` before any serial I/O, and it never sets,
# replaces, or reaches the physical port. Override for a lab that
# standardises on a different node.
CANONICAL_SERIAL_ALIAS: str = os.getenv(
    "TRAINER_CANONICAL_SERIAL_ALIAS", "/dev/ttyUSB0"
)

# --- Hack Mode serial transport (Phase 2A) ---------------------------------
#
# Real USB-serial I/O with the attached ESP32 — see
# app/hardware/serial_transport.py. The port is never configured here: it
# always comes from the shared `DeviceState.port` that detection established.

# Line rate for the physical ESP32 panels. One place, so no handler, command,
# or transport carries a literal baud value of its own.
SERIAL_BAUD_RATE: int = int(os.getenv("TRAINER_SERIAL_BAUD_RATE", "115200"))

# How long one blocking read may wait before returning empty, in seconds.
# This is what keeps the reader thread responsive to a stop request without
# ever busy-looping: it parks in the OS driver, not in Python.
SERIAL_READ_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_SERIAL_READ_TIMEOUT_SECONDS", "0.1")
)

# Bytes requested per read. A chunk size, not a minimum — a read returns as
# soon as the device has anything or the timeout above elapses.
SERIAL_READ_CHUNK_BYTES: int = int(os.getenv("TRAINER_SERIAL_READ_CHUNK_BYTES", "4096"))

# How long a write may block before it is abandoned, in seconds. Bounded so a
# board that has stopped draining its input buffer cannot park a worker
# thread indefinitely.
SERIAL_WRITE_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_SERIAL_WRITE_TIMEOUT_SECONDS", "5")
)

# Chunks buffered between the reader thread and the WebSocket before the
# oldest are dropped. A talkative board must not grow memory without bound;
# dropping the oldest keeps the terminal showing the most recent output.
SERIAL_MAX_BUFFERED_CHUNKS: int = int(
    os.getenv("TRAINER_SERIAL_MAX_BUFFERED_CHUNKS", "512")
)

# Largest payload one `serial-send` may write, in characters.
#
# Deliberately well under the parser's MAX_COMMAND_CHARS (1024, see
# app/commands/parser.py), for two reasons. It bounds what reaches the
# *device* rather than what reaches the parser — a training panel's firmware
# reads short lines, and a kilobyte at 115200 baud is a long write to sit
# behind. And it is a second, independent check: the handler does not assume
# its caller enforced anything, the same discipline the parser documents for
# itself. Being tighter than the line limit is what keeps it a reachable
# bound rather than a decorative one.
MAX_SERIAL_SEND_CHARS: int = int(os.getenv("TRAINER_MAX_SERIAL_SEND_CHARS", "512"))

# --- panel identity (ESP32 MAC) --------------------------------------------
#
# See app/hardware/identity.py. The MAC burned into the ESP32's OTP ROM is
# the only stable per-panel identifier available without running our own
# firmware; `esptool read_mac` is how it is read.

# Path to the esptool binary. Empty means "discover the one the ESP32
# Arduino core already installed" (see identity.py::discover_esptool) — the
# same binary `arduino-cli upload` drives, so no new tool is introduced.
# Set TRAINER_ESPTOOL_PATH to override on an unusual install.
ESPTOOL_PATH: str = os.getenv("TRAINER_ESPTOOL_PATH", "")

# Bounded MAC-read timeout, in seconds. A `--no-stub read_mac` against a
# responsive board takes ~2s; this leaves generous headroom while ensuring
# a board stuck in reset cannot hold the shared device layer indefinitely.
HARDWARE_IDENTITY_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_HARDWARE_IDENTITY_TIMEOUT_SECONDS", "30")
)

# Extra MAC -> panel-name entries, as comma-separated `mac=NAME` pairs, e.g.
# "24:6f:28:ab:cd:ef=ENVIRONMENTAL MONITORING SYSTEM". Merged over the
# built-in table in app/hardware/panels.py so a second machine, or the
# remaining panels of the five-panel scope, can be mapped without a code
# change. An unmapped board shows its MAC; nothing is ever guessed.
PANEL_NAMES_RAW: str = os.getenv("TRAINER_PANEL_NAMES", "")

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
