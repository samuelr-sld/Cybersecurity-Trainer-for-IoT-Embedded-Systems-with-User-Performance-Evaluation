"""Mode Session Preparation WebSocket protocol (`/ws/prepare`).

Its own module and version for the reason `build_messages.py` gives: nothing
here may force a change on `/ws/hack` or `/ws/build`, which are unchanged.

Client -> server (exactly one frame per connection)
    {"type": "prepare", "mode": "hack" | "build"}

Server -> client
    {"type": "stage",  "stage": "compiling", "status": "running",
     "message": "...", "data": {}}
    {"type": "result", "success": true, "mode": "hack", "message": "...",
     "failed_stage": null, "detail": "", "data": {}}
    {"type": "error",  "message": "..."}

`mode` is the ONLY thing a client chooses. There is deliberately no field
naming a panel, a port, a firmware or a board: the panel is whatever ESP32 is
physically attached (identified by its MAC), and everything written to it
comes from that panel's own package — the same trust model as the field-less
`compile`/`flash` frames on `/ws/build`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

PREPARATION_PROTOCOL_VERSION = 1

#: A `prepare` frame is tiny; anything larger is refused before parsing.
MAX_PREPARATION_MESSAGE_CHARS = 256


class _PreparationFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrepareMessage(_PreparationFrame):
    type: Literal["prepare"] = "prepare"
    mode: Literal["hack", "build"]


PREPARATION_CLIENT_MESSAGE_ADAPTER: TypeAdapter[PrepareMessage] = TypeAdapter(PrepareMessage)


class PreparationStageMessage(_PreparationFrame):
    type: Literal["stage"] = "stage"
    stage: str
    status: Literal["running", "succeeded", "failed"]
    message: str
    data: dict[str, Any] = {}


class PreparationResultMessage(_PreparationFrame):
    type: Literal["result"] = "result"
    protocol_version: int = PREPARATION_PROTOCOL_VERSION
    success: bool
    mode: Literal["hack", "build"]
    message: str
    failed_stage: str | None = None
    detail: str = ""
    data: dict[str, Any] = {}


class PreparationErrorMessage(_PreparationFrame):
    type: Literal["error"] = "error"
    message: str
