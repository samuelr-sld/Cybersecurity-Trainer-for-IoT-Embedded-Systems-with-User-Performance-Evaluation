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
