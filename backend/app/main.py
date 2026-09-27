"""FastAPI application for the Embedded IoT Cybersecurity Trainer backend."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, build_websocket, config, preparation_websocket, websocket
from app.hardware import device_monitor
from app.models.build_messages import BUILD_PROTOCOL_VERSION
from app.models.messages import PROTOCOL_VERSION

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application startup/shutdown — currently one job: warm the device cache.

    WHY STARTUP OWNS THE FIRST DETECTION. Every consumer of the shared
    device state reads `DeviceMonitor.snapshot()` rather than detecting, so
    that opening a Hack Mode terminal or creating a session costs no
    `arduino-cli` call and never resets the board. That contract only works
    if somebody fills the cache, and the honest answer out of an unfilled
    cache is NOT_CHECKED — "nobody has looked" — which a consumer must not
    mistake for "nothing is attached". Startup is the right somebody: it is
    the one place that runs before any consumer exists, and it belongs to
    the application lifecycle rather than to any one request.

    NOT AWAITED, AND THAT IS DELIBERATE. Detection is bounded by
    `BUILD_DEVICE_DETECT_TIMEOUT_SECONDS` (20s) and an identity probe by
    `HARDWARE_IDENTITY_TIMEOUT_SECONDS` (30s), so awaiting it here would let
    one wedged USB driver hold the whole backend unavailable for the better
    part of a minute. Instead the detection starts on the first pass of the
    event loop — before any client can realistically have connected — and
    the cache is initialized moments later. Any consumer that does arrive
    first reads NOT_CHECKED and says so, which is the accurate answer for
    that instant; nothing waits, sleeps, retries or polls for it.

    IT CANNOT BREAK STARTUP OR SHUTDOWN. `refresh()` never raises (a missing
    toolchain and an empty port list are both *answers*), the broad guard
    below covers a bug that made it raise anyway, and the task is cancelled
    and awaited on shutdown so a detection in flight cannot outlive the app.
    """
    task: asyncio.Task | None = None
    if config.HARDWARE_STARTUP_DETECT:
        task = asyncio.create_task(_prime_device_monitor(), name="device-monitor-prime")
    # Published on `app.state` so the lifecycle is observable rather than
    # implicit: a test awaits this exact task to drive the real mechanism
    # deterministically, with no sleep and no second initialization path.
    _app.state.device_monitor_prime = task
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _prime_device_monitor() -> None:
    """Run the shared monitor's first detection, once. Never fails loudly."""
    try:
        state = await device_monitor.prime()
    except Exception:  # pragma: no cover - refresh() is documented not to raise
        logger.warning("initial device detection failed", exc_info=True)
        return
    logger.info("device monitor primed: %s", state.status.value)


app = FastAPI(
    title="IoT Cybersecurity Trainer backend",
    version=__version__,
    lifespan=lifespan,
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
app.include_router(preparation_websocket.router)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness probe for the dev server and the frontend."""
    return {
        "status": "ok",
        "service": config.SERVICE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "build_protocol_version": BUILD_PROTOCOL_VERSION,
    }
