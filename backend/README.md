# Hack Mode Backend — Phase 2A

FastAPI + WebSocket foundation for the Hack Mode terminal of the Embedded IoT
Cybersecurity Trainer.

## What this currently does

- Serves a FastAPI app with a `GET /health` liveness endpoint.
- Accepts WebSocket connections at `/ws/hack`.
- Creates an isolated in-memory session per connection and announces its id.
- Validates a small, typed message protocol (`input`, `resize`) and replies
  with structured `output` / `error` frames.
- Removes the session on disconnect.

## What it deliberately does NOT do

No command execution, no PTY, no shell, no scenario simulation, no MQTT, no
ESP32 integration, no database, and no evaluation metrics. The React frontend
is **not** wired to this backend yet — Hack Mode still uses its client-side
mock transport. Those are later phases.

## Setup

From the `backend/` directory:

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Run the development server

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Host, port, and allowed origins can be overridden with the `TRAINER_HOST`,
`TRAINER_PORT`, and `TRAINER_ALLOWED_ORIGINS` environment variables (see
`app/config.py`).

## Health endpoint

`GET http://127.0.0.1:8000/health`

```json
{ "status": "ok", "service": "iot-cybersecurity-trainer", "protocol_version": 1 }
```

## WebSocket endpoint

`ws://127.0.0.1:8000/ws/hack`

One session per connection. Frames are JSON text; binary frames are refused.

### Client -> server

```json
{ "type": "input",  "data": "nmap -p 1883 192.168.4.0/24\r" }
{ "type": "resize", "cols": 100, "rows": 30 }
```

`input.data` is capped at 4096 characters; `cols`/`rows` must be integers in
1..1000. Validation is strict: unknown keys, wrong types (including numbers
sent as strings), and oversized frames are rejected.

### Server -> client

```json
{ "type": "session", "session_id": "...", "protocol_version": 1 }
{ "type": "output",  "data": "..." }
{ "type": "error",   "message": "..." }
```

On connect the server sends one `session` frame, then an `output` banner.
During Phase 2A, an `input` frame is answered with an acknowledgement
`output` frame — the input is **not** interpreted or executed. A `resize`
frame is recorded on the session and acknowledged.

A fourth server frame is reserved for a later phase and is never emitted yet:

```json
{ "type": "event", "event": "...", "data": {} }
```

An invalid frame produces an `error` frame; the connection stays open.

## Security boundary

**Terminal input is untrusted and is never executed.** There is no path from
this WebSocket to a host shell, and none may be added. Anything under `app/`
is forbidden from using `subprocess`, `os.system`/`os.popen`, `shell=True`,
`eval`/`exec`, `pty`, or any CMD/PowerShell/shell invocation;
`tests/test_hack_backend.py` enforces this with a static token scan of the
package. Command handling will arrive as a controlled command router matching
input against a closed set of simulated tools — never as shell delegation.

The CORS configuration in `app/main.py` is **development-oriented**: it
allowlists the local Vite dev origins because the frontend (`:5173`) and the
backend (`:8000`) run as separate origins during development. A deployment
must set `TRAINER_ALLOWED_ORIGINS` explicitly.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Covers app startup, `/health`, WebSocket accept, session create/announce/
isolate/remove, input acknowledgement, resize validation, controlled errors on
malformed frames, and the no-execution security boundary.

## Architecture and future phases

```
React + HackTerminal
        |
        |  WebSocket  (JSON envelope, app/models/messages.py)
        v
FastAPI  (app/main.py, app/websocket.py)
        |
        +-- Session management        app/sessions.py       [implemented]
        +-- Command router            app/commands/         [scaffold only]
        +-- Scenario engine           app/scenarios/        [scaffold only]
        +-- Event logger              app/events/           [scaffold only]
```

Intended pipelines once those are filled in:

```
WebSocket -> Session -> Command Router -> Scenario Engine -> simulated IoT env
Session   -> Event Logger -> Evaluation System
```

The scenario engine is the seam that keeps the frontend terminal independent
of what backs it, so a simulated device can later be supplemented or replaced
by a physical ESP32 without changing the WebSocket protocol or the terminal
component.

Planned next steps: 2B command router, 2C scenario/IoT simulation, 2D frontend
WebSocket integration (replacing the mock transport in `HackMode.jsx`).
