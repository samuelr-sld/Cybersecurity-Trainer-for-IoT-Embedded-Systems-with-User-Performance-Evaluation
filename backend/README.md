# Hack Mode Backend — Phase 2D-A

FastAPI + WebSocket foundation for the Hack Mode terminal of the Embedded IoT
Cybersecurity Trainer.

## What this currently does

- Serves a FastAPI app with a `GET /health` liveness endpoint.
- Accepts WebSocket connections at `/ws/hack`.
- Creates an isolated in-memory session per connection and announces its id.
- Validates a small, typed message protocol (`input`, `resize`) and replies
  with structured `output` / `action` / `error` frames.
- Routes each completed command line through a controlled command router
  (`app/commands/`) against a closed set of simulated tools.
- Runs each tool against a per-session **scenario engine** (`app/scenarios/`)
  that simulates an Environmental Monitoring IoT target and its MQTT broker.
- Delivers the scenario's own domain events and state snapshots back over the
  same WebSocket as `event` / `state` frames, so the frontend can render the
  target device panel without polling or scraping terminal text.
- Removes the session (and its scenario) on disconnect.

## What it deliberately does NOT do

No OS command execution, no PTY, no shell, no real nmap, no real MQTT broker,
no ESP32 or serial/`esptool` access, no database, no persistence, and no
evaluation metrics. The IoT target is a deterministic in-memory simulation.
The React frontend is **not** wired to this backend yet — Hack Mode still uses
its client-side mock transport (Phase 2D-B). No scoring/evaluation pipeline
exists yet (Phase 2E); `event` frames are transported, not persisted or
graded.

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
{ "status": "ok", "service": "iot-cybersecurity-trainer", "protocol_version": 3 }
```

## WebSocket endpoint

`ws://127.0.0.1:8000/ws/hack`

One session per connection. Frames are JSON text; binary frames are refused.

### Client -> server

```json
{ "type": "input",  "data": "nmap -p 1883 192.168.4.0/24\r" }
{ "type": "resize", "cols": 100, "rows": 30 }
```

`input.data` is **one completed command line**, not a keystroke. The browser
owns line editing: xterm.js handles echo, Backspace, Ctrl+C, and the cursor,
and sends the finished line on Enter. The backend has no line discipline and
must not grow one.

`input.data` is capped at 4096 characters; `cols`/`rows` must be integers in
1..1000. Validation is strict: unknown keys, wrong types (including numbers
sent as strings), and oversized frames are rejected.

### Server -> client

```json
{ "type": "session", "session_id": "...", "protocol_version": 3 }
{ "type": "output",  "data": "..." }
{ "type": "action",  "action": "clear" }
{ "type": "error",   "message": "..." }
{ "type": "event",   "event": "...", "data": {} }
{ "type": "state",   "data": {} }
```

On connect the server sends one `session` frame, then an `output` banner.

An `input` frame is routed through the command router and its structured
result rendered back as frames, in this order:

1. `action` — zero or more instructions to the terminal itself. `clear` is
   the only action today — the backend says the screen should be reset and
   the front end decides how, rather than the backend emitting a screenful of
   newlines or a raw ANSI sequence.
2. `output` — terminal text, one coalesced frame per command (CRLF-terminated).
3. `event` — one frame per scenario domain event the command caused (see
   "Scenario event/state delivery" below). Most commands cause none.
4. `state` — sent once, only when the command caused at least one `event`,
   carrying the scenario's full `snapshot()`.

A blank command line produces no frames at all. A `resize` frame is recorded
on the session and acknowledged.

`error` remains reserved for **protocol** failures (bad JSON, schema
violations, oversized or binary frames). An unknown command is ordinary
terminal output rather than a protocol error, so `foo` produces an `output`
frame reading `foo: command not found`.

An invalid frame produces an `error` frame; the connection stays open.

### Scenario event/state delivery

Commands that act on the scenario (`firmware-extract`, `firmware-analyze`,
`nmap`, `mosquitto_sub`, `mosquitto_pub`, `mqtt-explorer`) can cause the
scenario to record domain events — see `app/scenarios/events.py` for the
event vocabulary (`firmware_extracted`, `broker_discovered`,
`spoof_succeeded`, `attack_completed`, and so on). When a command does, the
WebSocket layer sends one `event` frame per event, in the order the scenario
recorded them, followed by a single `state` frame carrying
`Scenario.snapshot()` — the same shape covered in "Scenario: Environmental
Monitoring target" below (target identity, environmental readings, and the
discovery/attack/completion flags).

A command that causes no scenario event (e.g. observing the wrong topic, or
publishing to a topic the device isn't subscribed to) sends neither frame —
the client's last snapshot is still current, so nothing is resent. This
keeps event/state delivery inside the existing pipeline
(`WebSocket -> Session -> Command Router -> Command Handler -> Scenario`)
without teaching the WebSocket layer, the router, or any handler anything
about individual commands or scenario semantics: `app/websocket.py` only
knows that a `CommandResult` may carry `events`, forwarded unchanged from
`ScenarioOutcome.events` by `app/commands/scenario_adapter.py`.

## Commands

The router recognises a closed set of commands and nothing else:

| Command         | Phase 2B behaviour                                    |
| --------------- | ----------------------------------------------------- |
| `help`          | Lists the registered commands.                         |
| `clear`         | Returns a `clear` terminal action.                     |
| `nmap`          | Deterministic stub; scenario results land in Phase 2C. |
| `mosquitto_sub` | Deterministic stub; scenario data lands in Phase 2C.   |
| `mosquitto_pub` | Deterministic stub; scenario behaviour lands in 2C.    |
| `mqtt-explorer` | Deterministic stub; scenario data lands in Phase 2C.   |

The pipeline, and the module owning each step:

```
raw line -> parser.py -> ParsedCommand -> registry.py -> handlers/ -> CommandResult
```

`app/commands/router.py` drives that chain and is the containment boundary: a
blank line, unsupported syntax, an unknown command, and a handler that raises
all return a `CommandResult`, never an exception that would drop the
student's connection. Handlers return data and never touch the socket, which
is what keeps the router transport-independent — Phase 2D can wire it to the
React terminal without either side reaching into the other.

### Command grammar

The parser is a lexer, not an interpreter. It splits a line into
whitespace-separated words, with single or double quotes to keep spaces
inside one argument, and performs **no** expansion of any kind: no variables,
no globbing, no command substitution, no redirection, no pipelines, no
backslash escapes.

Shell metacharacters (`|`, `&`, `;`, `<`, `>`, backtick, `$`, `(`, `)`) are
not syntax here. Unquoted they are a syntax error; quoted they are ordinary
literal characters. So `nmap 192.168.4.1 && whoami` is refused as unsupported
input, and `mosquitto_pub -m "a; b"` yields the single literal argument
`a; b` — in neither case does anything run. Control characters (ESC included)
are rejected outright, which keeps ANSI escape sequences out of any value the
router might echo back. `#` and `+` stay literal, because they are MQTT topic
wildcards.

The router caps a line at 1024 characters and 64 tokens itself, so it is safe
regardless of which caller hands it a line.

## Scenario: Environmental Monitoring target

Phase 2C puts a simulated IoT target behind the commands. The conceptual
device is an ESP32 + BME280 (temperature, humidity, pressure) with an OLED
display, reporting over Wi-Fi to an MQTT broker. The learning objective is a
**data-integrity attack**: the device trusts whatever arrives on its telemetry
topic, so an attacker who publishes a manipulated reading makes the device
report a value that was never measured.

Everything is a deterministic in-memory simulation — there is no real device,
serial port, `esptool`, network scan, or MQTT broker. That is both a security
requirement (a student's command must never become a real system action) and
what makes the scenario fully testable without hardware. The `Scenario`
interface (`app/scenarios/base.py`) is the seam where a physical ESP32 could
later replace the simulation without changing the router, the handlers, the
protocol, or the terminal.

### Progression

Each stage is gated by the specific scenario facts it needs, not by a global
counter, so out-of-order attempts behave deterministically:

| Stage | Command                                             | Effect                                              |
| ----- | --------------------------------------------------- | --------------------------------------------------- |
| 0     | *(initial)*                                         | Target online: 28 °C, 65 %, 1008 hPa.               |
| 1     | `firmware-extract`                                  | Obtains a firmware image (`firmware_extracted`).    |
| 2     | `firmware-analyze`                                  | Recovers broker IP, port, and topic from firmware.  |
| 3     | `nmap -p 1883 <ip>`                                 | Confirms the MQTT service is reachable.             |
| 4     | `mosquitto_sub -h <ip> -t <topic>`                  | Observes legitimate telemetry (`mqtt_observed`).    |
| 5-6   | `mosquitto_pub -h <ip> -t <topic> -m temperature=150` | Spoofs the reading; the target adopts it.         |
| 7     | *(all of the above)*                                | Objective complete (`attack_successful`).           |

`firmware-extract` / `firmware-analyze` are the two commands Phase 2C added:
the Phase 2B tool set could not represent obtaining and analysing firmware
without overloading an unrelated tool. Analysis is what reveals the MQTT
topic, so the topic is never handed out before the student does the work —
`nmap` confirms the service but never names the topic, and `mqtt-explorer`
will not enumerate it pre-analysis.

### Attack success

Success is the full learning objective, never merely "a command ran". It
requires firmware analysed, broker and topic discovered, MQTT traffic
observed, and a spoof accepted so the target's reported state actually
changed. Publishing to the wrong topic, or a payload with no numeric
temperature, is recorded as an attempt but changes nothing. Only
`temperature` is spoofable in this scenario; humidity and pressure stay put.

### Payload format

`mosquitto_pub -m` accepts either JSON (`'{"temperature": 150}'`, wrapped in
single quotes so the terminal keeps it as one token) or a `key=value` form
(`temperature=150`). Payloads are parsed with `json.loads` / a manual
splitter — never `eval` — and are data the simulated device inspects, never
anything that runs.

### Per-session isolation

The scenario lives on `HackSession` (created via a `default_factory`), so each
connection gets an independent target. Handlers reach it through
`CommandContext.scenario`, a property delegating to the session, so there is a
single source of truth and no shared mutable global. One student can never
observe or alter another's scenario.

## Security boundary

**Terminal input is untrusted and is never executed.** There is no path from
this WebSocket to a host shell, and none may be added. Anything under `app/`
is forbidden from using `subprocess`, `os.system`/`os.popen`, `shell=True`,
`eval`/`exec`, `pty`, or any CMD/PowerShell/shell invocation;
`tests/test_hack_backend.py` enforces this with a static token scan of the
package. Command handling is a controlled router matching input against a closed set
of simulated tools — never shell delegation, and the scenario engine behind it
computes every result from in-memory state. `tests/test_command_router.py` and
`tests/test_scenario_engine.py` assert that neither the command layer nor the
scenario layer imports the transport, that both are free of execution
primitives, that results are deterministic, and that shell operators, command
injection, and hostile MQTT payloads all stay inert.

The CORS configuration in `app/main.py` is **development-oriented**: it
allowlists the local Vite dev origins because the frontend (`:5173`) and the
backend (`:8000`) run as separate origins during development. A deployment
must set `TRAINER_ALLOWED_ORIGINS` explicitly.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_hack_backend.py` covers app startup, `/health`, WebSocket
accept, session create/announce/isolate/remove, resize validation, controlled
errors on malformed frames, and the no-execution security boundary.

`tests/test_command_router.py` covers parsing (arguments, quoting, blank
input, limits), the closed registry, dispatch of every registered command,
unknown-command handling, handler-failure containment, session isolation,
result determinism, `output`/`action` rendering, and that a session survives
malformed and hostile input.

`tests/test_scenario_engine.py` covers the Environmental Monitoring scenario:
deterministic initial state, per-session isolation, each stage's state
transition and gate, the happy path to attack success, invalid and
out-of-order paths, that only the complete chain counts as success, that
repeated commands do not corrupt state, and the no-execution boundary of the
scenario layer.

`tests/test_websocket_scenario_sync.py` covers Phase 2D-A: that a command
whose scenario events are non-empty produces one `event` frame per event
followed by exactly one `state` frame equal to the session's own
`scenario.snapshot()`, that a command with no scenario events (e.g. `help`,
`clear`, or observing/publishing to the wrong topic) sends neither, that a
wrong-topic observe leaves scenario state untouched, that a full successful
spoof produces the expected event chain and final state, and that two
concurrent sessions' event/state streams stay isolated — alongside regression
coverage that session creation, the banner, `input`/`resize` handling, the
`clear` action, and malformed-frame rejection are all unchanged.

## Architecture and future phases

```
React + HackTerminal
        |
        |  WebSocket  (JSON envelope, app/models/messages.py)
        v
FastAPI  (app/main.py, app/websocket.py)
        |
        +-- Session management        app/sessions.py       [implemented]
        +-- Command router            app/commands/         [implemented]
        +-- Scenario engine           app/scenarios/        [implemented]
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

The scenario engine exposes two seams, one now wired end to end and one still
ahead:

- **Phase 2D-A (this phase, backend)**: `Scenario.snapshot()` and
  `Scenario.events` are now delivered over `/ws/hack` as `state` and `event`
  frames (see "Scenario event/state delivery" above) — `app/websocket.py`
  transports them without gaining any scenario-specific knowledge of its own.
- **Phase 2E (evaluation)**: `Scenario.events`, combined with the command name
  and `CommandResult.exit_code` available at the router seam, is what a future
  event logger and scorer will read to know exactly what a student did
  without parsing terminal output. No event store, timing, or scoring exists
  yet — Phase 2D-A only transports the events, it does not persist or grade
  them.

Planned next steps: **Phase 2D-B** — the React WebSocket client (replacing the
mock transport in `HackMode.jsx`, rendering the target panel from the `state`
frame, appending `output`/`event` frames to the terminal, and acting on
`action`), then **Phase 2E** — event recording and evaluation.
