"""FastAPI application for the Embedded IoT Cybersecurity Trainer backend."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, build_websocket, config, websocket
from app.models.build_messages import BUILD_PROTOCOL_VERSION
from app.models.messages import PROTOCOL_VERSION

app = FastAPI(
    title="IoT Cybersecurity Trainer backend",
    version=__version__,
)

# DEVELOPMENT CORS. Vite (:5173) and FastAPI (:8000) are separate origins
# during development, so the browser needs an explicit allowlist. See
# app/config.py — this is not a production configuration, and the origin list
# must be set via TRAINER_ALLOWED_ORIGINS for any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(websocket.router)
app.include_router(build_websocket.router)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness probe for the dev server and the frontend."""
    return {
        "status": "ok",
        "service": config.SERVICE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "build_protocol_version": BUILD_PROTOCOL_VERSION,
    }
