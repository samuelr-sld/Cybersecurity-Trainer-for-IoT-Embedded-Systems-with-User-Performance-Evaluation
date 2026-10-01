"""Minimal configuration for the Hack Mode backend.

Deliberately dependency-free: values come from environment variables with
development defaults, so no settings library is required at this stage.
"""

from __future__ import annotations

import os
import pathlib

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

# --- event logging (Phase 2B) ---------------------------------------------
#
# Durable Hack Mode event log — see app/events/store.py. One SQLite file
# holds the session/command/event rows Phase 2E will compute metrics from.
#
# The default lives beside the backend package rather than in the working
# directory, so the path does not change depending on where uvicorn was
# started from — a classroom's evidence must not end up split across several
# files because someone launched the server from a different folder. Set
# TRAINER_EVENT_DB_PATH to place it on a specific volume, or to ":memory:"
# for a throwaway run that keeps nothing.
EVENT_DB_PATH: str = os.getenv(
    "TRAINER_EVENT_DB_PATH",
    str(pathlib.Path(__file__).resolve().parent.parent / "data" / "trainer_events.sqlite3"),
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

# Largest accepted `preserved` fragment list on an `edit_section_blocks`
# frame (Phase B8). One entry per body position the toolbox cannot draw, so a
# real section has a handful; the whole frame is still bounded by
# `MAX_BUILD_MESSAGE_CHARS` above, and this caps the list's LENGTH so a
# payload of many tiny entries is rejected by count before it is walked.
MAX_BUILD_PRESERVED_FRAGMENTS: int = 256

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

# Whether the application lifespan runs the shared monitor's FIRST detection
# at startup (`app/main.py`, `DeviceMonitor.prime`). On by default, because
# the alternative is a backend whose device cache stays NOT_CHECKED until
# something happens to poll it — and a consumer reading that cache before
# then cannot distinguish "nobody has looked" from "nothing is plugged in".
#
# It changes WHEN the first `arduino-cli board list` runs, never whether a
# request path may run one: no endpoint, session or scenario selection
# detects, before or after this. Set TRAINER_HARDWARE_STARTUP_DETECT=0 for
# an environment that must not touch the toolchain at boot — the backend
# still works, it simply learns what is attached on the first poll instead.
# The test suite disables it (see tests/conftest.py) so constructing a
# TestClient never spawns a real CLI or resets a real board.
HARDWARE_STARTUP_DETECT: bool = os.getenv("TRAINER_HARDWARE_STARTUP_DETECT", "1") not in {
    "0",
    "false",
    "False",
    "no",
}

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

# See app/hardware/flash_reader.py. A real `esptool read_flash` of the
# student-facing command's documented full-image example (4 MiB) genuinely
# takes tens of seconds even with the stub flasher; this is deliberately far
# more generous than the MAC-read timeout above, which reads a handful of
# bytes rather than an image.
HARDWARE_FLASH_READ_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_HARDWARE_FLASH_READ_TIMEOUT_SECONDS", "240")
)

# Upper bound on a real `esptool.py read_flash <offset> <size> <file>`, in
# bytes. 4 MiB matches the ESP32's typical flash size and the size the
# scenario's own simulated banner text has always quoted; it exists so a
# student cannot ask the real hardware path for an arbitrarily large or slow
# read merely by typing a bigger number.
HARDWARE_MAX_FLASH_READ_BYTES: int = int(
    os.getenv("TRAINER_HARDWARE_MAX_FLASH_READ_BYTES", str(0x400000))
)

# Extra MAC -> panel bindings, as comma-separated `mac=panel-id` pairs, e.g.
# "02:00:00:00:00:02=environmental-monitoring". Applied over the built-in
# registry in app/hardware/panels.py so a replacement or additional ESP32
# module can be bound to a panel without a code change. A binding may only
# name a panel id already defined in backend source — it cannot create a
# panel, rename one, or point at firmware — and malformed entries are
# skipped. An unregistered board shows its MAC; nothing is ever guessed.
PANEL_MACS_RAW: str = os.getenv("TRAINER_PANEL_MACS", "")

# --- panel resource packages (Phase 2D.1) ----------------------------------
#
# Root of the trusted panel/scenario package tree — see app/panels/. One
# directory per panel, named by the panel's `package_id`, each holding a
# `panel.json` manifest and that panel's firmware resources.
#
# APPLICATION CONFIGURATION, NEVER CLIENT INPUT. No frontend, student, MAC
# address or WebSocket frame can name this root, a package, or a file inside
# one: a MAC resolves to a `PanelDefinition`, and only that definition's
# validated `package_id` (a lower-case hyphenated identifier, so it cannot
# traverse) selects a directory under here. The loader re-checks containment
# on top of that.
#
# Defaults beside the backend package rather than to the working directory,
# for the same reason EVENT_DB_PATH does: the resources a classroom runs must
# not change depending on where uvicorn was launched from.
PANEL_PACKAGE_ROOT: str = os.getenv(
    "TRAINER_PANEL_PACKAGE_ROOT",
    str(pathlib.Path(__file__).resolve().parent.parent / "panels"),
)

# --- provisioned lab fixtures (Phase B8) -----------------------------------
#
# Remediation validation needs real credentials to talk to the real training
# broker, and a repository must never hold one. A panel package therefore
# declares only the NAME of the environment variable a deployment provisions
# each value into (see app/panels/models.py::LabIdentity), and this is the one
# function that reads it.
#
# WHY IT LIVES HERE. `app/build/validation/` is statically forbidden from
# importing `os` (tests/test_build_pipeline_b7.py), which is what keeps a
# validator from reaching into the process environment or the filesystem on
# its own. Routing the one legitimate read through this module preserves that:
# a validator asks for a named fixture and gets a string, and it cannot ask
# for anything else.
#
# THE PREFIX IS THE ALLOWLIST. Only TRAINER_LAB_* names resolve, so even a
# mis-authored package cannot name an arbitrary variable of the process it
# runs in (AWS_SECRET_ACCESS_KEY, PATH, ...). An unset or ineligible name
# returns "", and every caller must treat that as "not provisioned" and
# report the check UNAVAILABLE rather than proceeding with a blank secret.
LAB_SECRET_PREFIX = "TRAINER_LAB_"


def lab_secret(name: str) -> str:
    """The provisioned value of one TRAINER_LAB_* fixture, or "" if there is none.

    Never raises and never guesses: an unknown, ineligible or unset name is
    an empty string, which is the caller's signal that this deployment cannot
    run the check.
    """
    if not isinstance(name, str) or not name.startswith(LAB_SECRET_PREFIX):
        return ""
    return os.getenv(name, "")


# --- lab environment file bootstrap -----------------------------------------
#
# `backend/lab.env.local` (gitignored) is read ONCE, when this module is first
# imported, so a plain `uvicorn app.main:app` needs no manual export.
#
#   * Located relative to this file (backend/), never the working directory.
#   * Optional: absent or unreadable => silently nothing happens.
#   * Only `KEY=value` lines whose KEY starts with LAB_SECRET_PREFIX are
#     imported; everything else in the file is ignored, so this is not a
#     general .env loader.
#   * PRECEDENCE: a variable already in the process environment always wins;
#     the file only fills names that are not set at all.
#   * Nothing is logged or printed, and values never leave os.environ.
#   * TRAINER_LAB_ENV_PATH (a path, read from the process environment only)
#     points at a different file; set it to "" to disable loading (the test
#     suite does this so it never depends on a developer's real credentials).
LAB_ENV_PATH_VARIABLE = "TRAINER_LAB_ENV_PATH"
DEFAULT_LAB_ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / "lab.env.local"


def _parse_lab_env_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export "):].lstrip()
    key, separator, value = text.partition("=")
    key = key.strip()
    if not separator or not key.startswith(LAB_SECRET_PREFIX):
        return None
    if not all(ch.isalnum() or ch == "_" for ch in key) or not key.isascii():
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key, value


def load_lab_env_file(
    path: pathlib.Path | str | None = None,
    environ: "os._Environ[str] | dict[str, str] | None" = None,
) -> int:
    """Import TRAINER_LAB_* entries from the lab env file; return how many were set.

    Never raises and never overwrites a variable that is already set.
    """
    target_environ = os.environ if environ is None else environ
    if path is None:
        override = target_environ.get(LAB_ENV_PATH_VARIABLE)
        if override is not None and not override.strip():
            return 0
        path = override.strip() if override else DEFAULT_LAB_ENV_FILE
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return 0
    loaded = 0
    for line in text.splitlines():
        parsed = _parse_lab_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in target_environ:
            target_environ[key] = value
            loaded += 1
    return loaded


load_lab_env_file()


# --- Hack Mode live MQTT attack path --------------------------------------
#
# When a Panel that declares a machine-checkable authorization criterion is
# attached and its lab credentials are provisioned, Hack Mode's `mosquitto_pub`
# and `mosquitto_sub` act over a REAL authenticated connection to the training
# broker instead of the in-memory simulation. See `app/hack_live_mqtt.py` (the
# composition seam), `app/mqtt/transport.py` (the generic transport), and
# `app/scenarios/smart_home_live.py` (the scenario-side link).
#
# This is a kill switch, not the enabling condition. Even set to "1", the live
# path only activates when a criterion IS declared, paho IS installed, and the
# credentials ARE provisioned; otherwise the session stays a pure simulation —
# which is exactly the no-hardware development flow. Set to "0" to force
# simulation everywhere (for instance during a class where the physical
# actuator must not move).
HACK_LIVE_MQTT_ENABLED: bool = os.getenv("TRAINER_HACK_LIVE_MQTT", "1") not in {
    "0",
    "false",
    "False",
    "no",
}

# Bounded connect timeout for the Hack Mode live MQTT path, in seconds. Kept
# in step with the Build validator's own connect bound; a broker that has not
# answered in this long is unreachable, not slow.
HACK_MQTT_CONNECT_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_HACK_MQTT_CONNECT_TIMEOUT_SECONDS", "10")
)

# Bounded window the live path watches the device's state topic for the
# actuation a forged command would cause, in seconds. Short: a real ESP32
# republishes its state within a second of acting, so this only needs headroom
# for network jitter. It bounds how long one `mosquitto_pub` may take.
HACK_MQTT_OBSERVE_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_HACK_MQTT_OBSERVE_TIMEOUT_SECONDS", "5")
)

# Bounded window a live `mosquitto_sub` listens for traffic before returning,
# in seconds. It caps how long one observation command runs; no live MQTT
# operation blocks the session indefinitely.
HACK_MQTT_LISTEN_TIMEOUT_SECONDS: float = float(
    os.getenv("TRAINER_HACK_MQTT_LISTEN_TIMEOUT_SECONDS", "5")
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


# How long a Hack/Build session outlives the WebSocket that was serving it.
# A browser reload (or a dropped connection) is a UI reconnection, not the end
# of a student's work: the session is detached and waits this long for the
# same client to resume it (`?session=<id>`). Only an explicit end
# (`POST /api/sessions/{mode}/{id}/end`) or this timeout finishes it. Bounded
# on purpose — an abandoned tab must not hold a session (and, for Build, its
# retained compiled artifact) for the life of the process.
SESSION_RESUME_GRACE_SECONDS: float = float(
    os.getenv("TRAINER_SESSION_RESUME_GRACE_SECONDS", "600")
)
