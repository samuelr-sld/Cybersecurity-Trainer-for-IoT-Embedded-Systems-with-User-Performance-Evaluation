"""Serve the prebuilt production frontend from the backend (one origin).

    browser -> http://192.168.50.1:8000/            index.html   (this module)
            -> http://192.168.50.1:8000/assets/...  hashed JS/CSS (this module)
            -> http://192.168.50.1:8000/api/...     the API       (existing routers)
            -> ws://192.168.50.1:8000/ws/...        the sockets   (existing routers)

WHY THIS EXISTS. The React app derives every API and WebSocket URL from the
page's own hostname plus port 8000 (`src/api/trainerApi.js`,
`src/hooks/use*Socket.js`). Serving the build from this same process makes the
page and the backend one origin, so a lab PC on the training network needs
nothing but the Raspberry Pi's address — no second HTTP server, no frontend
port, and no CORS.

OPT-IN. `config.FRONTEND_DIST_DIR` (`TRAINER_FRONTEND_DIST`) names the
directory; empty means API-only, which is the development flow and the
behaviour before this module existed. This module only READS a build that
already exists. It never builds, rebuilds, or watches anything, and it is not
part of startup beyond one `is_file()` check.

IT MUST NOT CHANGE ANY EXISTING ROUTE. The mount is registered last (see
`app/main.py::create_app`), so every `/api`, `/ws` and `/health` route is
matched first. Two behaviours of a catch-all mount would otherwise leak
through, and both are pinned by `tests/test_frontend_serving.py`:

  * An UNKNOWN path must still answer FastAPI's own `{"detail": "Not Found"}`.
    The frontend's API client keys on exactly that body to tell an outdated
    backend process from a real 404 (`UNROUTED_DETAIL` in trainerApi.js).
    Raising `HTTPException(404)` from a mounted app reaches FastAPI's handler,
    so this holds without any code here.
  * An unknown WebSocket path must still be refused as before.
    `StaticFiles` asserts an HTTP scope, so a WebSocket reaching it would
    crash with an `AssertionError` instead; `FrontendFiles` refuses it the way
    the router's own not-found does (a handshake close, code 1000).

MISCONFIGURATION IS A LOG LINE, NOT A CRASH. A configured directory with no
`index.html` leaves the backend serving its API with a warning, in the same
spirit as the lab-env loader: the training backend's job is to stay up, and
the warning names exactly what is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose

logger = logging.getLogger(__name__)

#: The entry document every production build has. Its presence is the whole
#: "is this a frontend build?" test.
INDEX_DOCUMENT = "index.html"


class FrontendFiles(StaticFiles):
    """`StaticFiles` that is safe to mount at `/` beside the WebSocket routes."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Not this app's business: refuse exactly as the router does for a
            # WebSocket path nothing matched, rather than tripping StaticFiles'
            # HTTP-only assertion.
            await WebSocketClose()(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def mount_frontend(app: FastAPI, dist_dir: str) -> bool:
    """Serve `dist_dir` at `/` on `app`. True if it was mounted; never raises.

    Must be called AFTER every other route is registered: a mount at `/`
    matches any path, so anything added later would be shadowed by it.
    """
    if not dist_dir:
        logger.debug("no frontend build configured; serving the API only")
        return False

    root = Path(dist_dir)
    if not (root / INDEX_DOCUMENT).is_file():
        logger.warning(
            "TRAINER_FRONTEND_DIST=%s has no %s; the frontend will NOT be served "
            "(the API is unaffected)",
            root,
            INDEX_DOCUMENT,
        )
        return False

    # html=True is what makes `/` answer with index.html; without it
    # StaticFiles serves only files named in the URL.
    app.mount("/", FrontendFiles(directory=root, html=True), name="frontend")
    logger.info("serving the production frontend from %s", root)
    return True
