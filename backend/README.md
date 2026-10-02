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
The React frontend connects to this backend over `/ws/*` and `/api/*`, and the
backend can serve the frontend's production build itself (see "Production
deployment" below). No scoring/evaluation pipeline
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

The bind address and port are **uvicorn's own `--host` / `--port` arguments**;
`TRAINER_HOST` / `TRAINER_PORT` in `app/config.py` are not read by anything.
`TRAINER_ALLOWED_ORIGINS` (CORS) only matters for a frontend served from a
different origin, such as Vite on `:5173` during development.

## Production deployment (Raspberry Pi, single origin)

In production the backend serves the frontend's **prebuilt** production build
itself, so the whole trainer is one origin — `http://192.168.50.1:8000` on the
`CyberTrainer` network — with no second HTTP server, no frontend port and no
CORS. The React app derives every API/WebSocket URL from the page's own
hostname plus port 8000, so nothing needs to be configured in the frontend.

```bash
cd ~/Embedded-IoT-Cybersecurity-Trainer/backend
TRAINER_FRONTEND_DIST=/home/arvis/Embedded-IoT-Cybersecurity-Trainer/dist \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `TRAINER_FRONTEND_DIST` is the directory holding the build's `index.html`
  (the `dist/` that `npm run build` produces, built on a development machine —
  the Pi has no Node). It is **opt-in**: unset (the development default) the
  backend serves only `/api`, `/ws` and `/health`. If it is set to a directory
  without an `index.html` the backend still starts, logs a warning naming the
  path, and serves no frontend. The backend never builds the frontend.
- On the Pi that directory is `<repo>/dist` (git-ignored), so the running
  service depends on nothing outside the repository; the offline provisioning
  bundle is an install-time artifact only. **Publishing a new UI build** is
  replacing the contents of `<repo>/dist` with the new `dist/` — the files are
  read per request, so no restart is needed provided `dist/index.html` existed
  when the service started (if it did not, the frontend was never mounted and
  one restart is needed).
- The mount is registered after every real route (`app/frontend.py`), so
  `/api/*`, `/ws/*`, `/health` and `/docs` are unchanged, and an unknown path
  still answers FastAPI's `{"detail": "Not Found"}`.
- `--host 0.0.0.0` exposes the API on every interface the Pi has. Today only
  the `CyberTrainer` AP interface is up; the API has no authentication, so do
  not connect another network to the Pi without re-checking this.

### Running it as a service (systemd)

`deploy/cybertrainer-backend.service` runs the command above as user `arvis`
from the repository's `backend/.venv`, restarts it if it exits, and reads
optional deployment settings and lab secrets from
`/etc/cybertrainer/backend.env`. That file is **optional** (`EnvironmentFile=-`):
the service starts without it, and it is where `TRAINER_LAB_*` values go when
they are provisioned (systemd reads it as root, so `root:root 0600` keeps them
from the `arvis` account). The backend's own `backend/lab.env.local` loader
still works and is not referenced by the unit; a variable set by the unit's
environment file wins over `lab.env.local`.

```bash
sudo install -m 0644 deploy/cybertrainer-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cybertrainer-backend     # only when you mean to deploy
journalctl -u cybertrainer-backend -f
```

Boot order is `CyberTrainer AP -> Mosquitto -> backend`: the unit is ordered
after `network-online.target` (reached once NetworkManager has activated the AP)
and `mosquitto.service`, and Mosquitto's own unit is ordered after
`network-online.target` because the broker binds `192.168.50.1`, which exists
only while the AP is up. The broker is `Wants=`, not `Requires=`, so the backend
still starts without it.

Starting the backend (by hand or as a service) runs one device detection, which
reads the ESP32's MAC and resets the board once. It writes no flash.

### Lab environment (Panel 1 live MQTT / Build validation)

Hack Mode's live attack path and Build Mode's remediation validation read their
credentials from the **process environment** (`TRAINER_LAB_*`, see
`app/config.py::lab_secret`). On import, `app/config.py` loads the optional,
gitignored `backend/lab.env.local` (located relative to `config.py`, not the
working directory) into the process environment, so a plain
`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` just works. Copy
`lab.env.example` to `lab.env.local` and fill it in. The required names are
`TRAINER_LAB_PANEL1_GUEST_PASSWORD`, `TRAINER_LAB_PANEL1_VALIDATOR_PASSWORD`
and `TRAINER_LAB_PANEL1_COMMAND_TOKEN`. If any is missing, validation stops
with "this deployment has not provisioned the training-lab credentials" rather
than running.

Loader rules: `KEY=value` lines only (blank lines and `#` comments ignored);
**only `TRAINER_LAB_*` keys are imported** (anything else in the file, such as
`TRAINER_HACK_LIVE_MQTT`, is ignored - set those in the real environment); a
variable already set in the process environment **wins** over the file; a
missing file is fine; nothing is logged. `TRAINER_LAB_ENV_PATH` points at a
different file, or `""` disables loading (the test suite does this).

Never commit real values; the token is deliberately not in any tracked file.

## Health endpoint

`GET http://127.0.0.1:8000/health`

```json
{ "status": "ok", "service": "iot-cybersecurity-trainer", "protocol_version": 7, "build_protocol_version": 7 }
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
{ "type": "session", "session_id": "...", "protocol_version": 7, "resumed": false, "scenario": {}, "state": {} }
{ "type": "output",  "data": "..." }
{ "type": "action",  "action": "clear" }
{ "type": "error",   "message": "..." }
{ "type": "event",   "event": "...", "data": {} }
{ "type": "state",   "data": {} }
```

On connect the server sends one `session` frame, then an `output` banner.

Since protocol v7 the `session` frame also carries the scenario the attached
panel declared, so a screen is correct before the first command and never
waits for a first event:

- `scenario` — the static briefing built by `app/hack_briefing.py` from the
  panel's package (`panel.json`): `kind` (`activity` / `foundation` /
  `unspecified`), `panel_id`, `scenario_id`, `title`, `objectives`
  (`{id, label, required_events}` — the package's `evaluation.objectives`),
  `outcomes`, `hints` (`{id, text, objective_id}`) and `guide`
  (`{heading, paragraphs}`). Hints and the guide come from the package's
  optional `hack` block (`HackDeclaration`), which is separate from Build
  Mode's `remediation`. `unspecified` (every field empty) means no package
  named this session's scenario. A resumed session is told the same briefing.
- `state` — `Scenario.snapshot()` at attach, the same payload a `state` frame
  carries. A scenario with an activity target includes a `readout` list
  (`{id, label, value, revealed}`; an undiscovered fact has `value: null`).

Objective progress is not sent: a client derives it from the recorded `event`
frames against each objective's `required_events`.

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

Commands that act on the scenario (`esptool.py`, `strings`, `grep`, `nmap`,
`mosquitto_sub`, `mosquitto_pub`) can cause the scenario to record domain events — see `app/scenarios/events.py` for the
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

| Command         | Behaviour                                                        |
| --------------- | ------------------------------------------------------------------ |
| `help`          | Lists the registered commands.                                     |
| `clear`         | Returns a `clear` terminal action.                                 |
| `esptool.py`    | `read_flash` reads the target's firmware (Phase 2C real-tool set). |
| `strings`       | Dumps the printable strings in a firmware image.                   |
| `grep`          | Searches a firmware image's strings for a pattern.                 |
| `nmap`          | Scans a host; scenario decides what's reachable.                   |
| `mosquitto_sub` | Subscribes to an MQTT topic; scenario decides what's observed.     |
| `mosquitto_pub` | Publishes to an MQTT topic; scenario decides the effect.           |

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

| Stage | Command                                                        | Effect                                              |
| ----- | --------------------------------------------------------------- | --------------------------------------------------- |
| 0     | *(initial)*                                                     | Target online: 28 °C, 65 %, 1008 hPa.               |
| 1     | `esptool.py read_flash 0x0 0x400000 firmware.bin`                | Obtains a firmware image (`firmware_extracted`).    |
| 2     | `strings firmware.bin` (or `grep <pattern> firmware.bin`)        | Recovers broker IP, port, and topic from firmware.  |
| 3     | `nmap -p 1883 <ip>`                                              | Confirms the MQTT service is reachable.             |
| 4     | `mosquitto_sub -h <ip> -t <topic>`                               | Observes legitimate telemetry (`mqtt_observed`).    |
| 5-6   | `mosquitto_pub -h <ip> -t <topic> -m temperature=150`             | Spoofs the reading; the target adopts it.         |
| 7     | *(all of the above)*                                             | Objective complete (`attack_successful`).           |

`esptool.py read_flash` / `strings` / `grep` are the real-tool trio Phase 2C
finalized on: the earlier placeholder `firmware-extract` / `firmware-analyze`
commands (and the never-real `mqtt-explorer`) are gone. `strings` dumps every
printable string in the firmware image unfiltered; `grep <pattern>` narrows
that to matching lines only, silently failing (like real `grep`) when nothing
matches. Analysis is what reveals the MQTT topic, so the topic is never
handed out before the student does the work — `nmap` confirms the service
but never names the topic.

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
that serves the frontend from the backend (see "Production deployment") is one
origin and does not use CORS; `TRAINER_ALLOWED_ORIGINS` is only needed for a
frontend served from a different origin. The WebSocket endpoints do no `Origin`
check.

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

## Panel/scenario packages — Phase 2D.1 + 2D.2

A connected panel's *experiment* is a trusted resource package, loaded but
never executed. The guiding split is **the engine provides the tools; the
connected panel provides the experiment** — `app/commands/` keeps offering
the same global toolbox to every panel, and a package only declares what a
student should do with those tools.

```
ESP32 MAC -> PanelRegistry -> PanelDefinition -> PanelPackage -> FirmwareConfiguration
 (fake/real)  app/hardware/     app/hardware/     app/panels/      app/hardware/
              panels.py         panels.py         (this layer)     firmware.py
```

- `app/panels/models.py` — `PanelPackage`, frozen validated data:
  `ScenarioDefinition` (which experiment, by `scenario_id` key),
  `LearningContent` (objectives, activity instructions, expected findings),
  a `WorkflowStep` sequence (each naming a *real* global tool), an
  `EvaluationDeclaration` (success conditions citing the canonical Phase 2B
  event vocabulary, plus which of the seven metrics ACR/RE/EAC/TTE/TTR/AID/DEI
  are relevant), a reused `FirmwareConfiguration`, and scalar-only static
  `parameters`. It holds no target network facts, no scenario behaviour, no
  display name and no metric formulas — one source of truth for each.
- `app/panels/loader.py` — `PanelPackageLoader`, the only filesystem-touching
  piece: one algorithm for every panel (`<root>/<package-id>/panel.json`,
  root = `TRAINER_PANEL_PACKAGE_ROOT`, default `backend/panels/`). Strict
  about unknown keys; missing (`PanelPackageNotFoundError`), invalid
  (`PanelPackageInvalidError`), and declared-but-absent firmware
  (`FirmwareResourceMissingError`) are distinct. No `eval`/`exec`/`import`,
  no `subprocess`, no command dispatch, no build-layer import, and no path
  from a client — the `package_id` is a validated identifier, re-checked for
  containment.
- `app/panels/service.py` — `PanelResourceService`, composing the existing
  panel identification service with the loader into one status
  (`NOT_CONNECTED`/`UNIDENTIFIED`/`UNREGISTERED`/`NO_PACKAGE`/`PACKAGE_ERROR`/
  `READY`). `PanelResources.firmware` is the single route to a panel's
  firmware configuration.

**Loading a package triggers nothing** — no compile, flash, scenario start,
event, or router dispatch — and nothing in this phase is wired into a request
path: identification stays silent, the Hack Mode `hardware` frame is
unchanged, and the frontend is untouched. **Panel 1** (Smart Home MQTT
Control System) is integrated as `backend/panels/smart-home-mqtt-control/`
(`panel.json` plus a real `.ino` firmware resource under `firmware/`). What
is **not** implemented: the complete Panel 1 attack/remediation/evaluation
lifecycle, any Panel 2 activity, and all metric computation — later phases
this seam exists to feed.

**Panel 2 (Environmental Monitoring System) is a foundation module**, and its
package (`backend/panels/environmental-monitoring/`) says so in data:

- *Hack Mode.* Its `scenario_id` (`environmental-sensing`) resolves to
  `EnvironmentalSensingScenario` (`app/scenarios/environmental_sensing.py`), a
  neutral target — no vulnerability, objectives, events or target facts; every
  tool answers that no activity is defined, and its `state` snapshot is
  `{"foundation": true, "objectives": []}`. Registering it is what stops Panel 2
  from falling back to the legacy MQTT/BME280 default scenario, and what lets
  Hack-mode preparation succeed for it. `tests/test_panel2_scenario.py`.
- *Build Mode.* The real sketch is opened through the same generic
  B1–B8/Blockly pipeline as Panel 1; no Panel-2 build code exists. Because a
  foundation panel has firmware but no remediation to carry a section policy,
  a package may declare one in an optional `build` block
  (`BuildDeclaration`: `editable_section_ids` / `explore_section_ids`). A package
  declares its policy there **or** in `remediation`, never both. Panel 2 opens
  `setup`, `loop`, `helper_updateDisplay` and `helper_showError`; the
  configuration runs (`global`, `global_2`: pins, OLED, 31.0 C threshold,
  intervals) stay locked. No security region, no validator, no remediation text.
  `tests/test_panel2_build.py`, including real `arduino-cli` compiles.

## Build Mode — Phase 3A + 3B + 3C, Phase 1 (Build Mode POC)

Build Mode is the defender/developer side of the trainer: a firmware
workspace that writes/edits real Arduino C++, compiles it with the real
Arduino CLI toolchain, and uploads the result to a physically connected
ESP32. It is a **separate channel and session type** from Hack Mode — its
own WebSocket endpoint, its own session manager, its own message protocol —
not Hack Mode commands, and Hack Mode is unmodified by its existence.

**Current default project: LED Blink pipeline-proof (`app/build/blink.py`,
`led-blink-poc`).** A fresh session's only job right now is to prove the
write → compile → flash → physical-execution pipeline works end to end
against real hardware — no vulnerability, no remediation objective. The
earlier Environmental Monitoring / Weak MQTT project (`app/build/
environmental.py`, `create_environmental_monitoring_project`) — where a
student remediated the vulnerability they found in Hack Mode — predates the
finalized five-panel scope and is no longer loaded by default; it remains
in the codebase, independently constructible, for when that scope resumes.
Everything below about the pipeline (WebSocket protocol, region model,
compiler, flasher) is project-agnostic and applies to either project —
only the loaded `BuildProject`'s identity, files, and region ids differ.

```
Student -> Build Mode UI -> WebSocket (/ws/build) -> Build Service -> Build Workspace -> BuildProject
                             app/build_websocket.py   app/build/service.py  app/build/workspace.py  app/build/models.py
                                                            |
                                                            +-> CompilerAdapter -> arduino-cli compile
                                                            |   app/build/compiler.py
                                                            |
                                                            +-> FlasherAdapter  -> arduino-cli upload -> ESP32
                                                                app/build/flasher.py
```

- `app/build/` — the domain layer: passive project/file/region models
  (`models.py`), the `BuildEvent` vocabulary (`events.py`), the workspace
  that enforces region protection and materializes a compile-ready sketch
  (`workspace.py`), the LED Blink pipeline-proof project that a fresh
  session loads by default (`blink.py`), the not-currently-loaded
  Environmental Monitoring reference project (`environmental.py`), the
  real-compiler adapter (`compiler.py`), the real-flasher adapter
  (`flasher.py`), the single sanctioned process-execution primitive both
  adapters call (`process.py`), and session-level orchestration
  (`service.py`).
- `app/build_sessions.py` — per-connection `BuildSession` state and its
  manager, mirroring `app/sessions.py`.
- `app/build_websocket.py` — the `/ws/build` endpoint, mirroring
  `app/websocket.py`.
- `app/models/build_messages.py` — Build Mode's own message protocol and
  `BUILD_PROTOCOL_VERSION`, tracked independently of Hack Mode's.

### Locked vs. editable firmware regions

A firmware file is not one opaque blob of text; it is an ordered sequence of
named segments, each tagged `locked` or `editable`. A segment's identity is
its `region_id`, not a line range, so a student's edit inside one region can
never shift another region's boundaries. The current default project's
`main.ino` (`blink.py`) has two segments — `locked_pre` and the editable
`blink_program` region — kept deliberately minimal; the not-currently-loaded
Environmental Monitoring project's `main.ino` has three (`locked_pre`, the
editable `security_logic` region, and `locked_post`) plus a fully-locked
`mqtt_config.h` file, and remains available for the five-panel phase.

**Enforcement is structural, not textual.** The only mutation
`BuildWorkspace` exposes is `update_region(path, region_id, source)`,
addressed by region id — there is no "submit a whole file" path for a
locked region's text to hide inside. Submitting an unknown `region_id`, or
one that names a `locked` segment, raises (`RegionNotFoundError` /
`RegionNotEditableError`) rather than silently doing nothing, and the
WebSocket layer turns that into an `error` frame. This is enforced entirely
server-side: the frontend's read-only styling of locked code is a UI
convenience, never the actual protection.

A student can submit syntactically broken C++ into the editable region — the
region model does not parse or validate submitted source at all, only which
region it may land in. Real syntax errors are now caught by the real
compiler instead (see "Compilation" below), not by anything in this layer.

### WebSocket endpoint

`ws://127.0.0.1:8000/ws/build` — one session per connection, JSON text
frames, same strict validation discipline as `/ws/hack` (unknown keys and
type coercion are rejected).

Client -> server:

```json
{ "type": "edit_region", "path": "main.ino", "region_id": "security_logic", "source": "..." }
{ "type": "compile" }
{ "type": "flash" }
```

Server -> client:

```json
{ "type": "session", "session_id": "...", "protocol_version": 3 }
{ "type": "state",   "data": {} }
{ "type": "event",   "event": "...", "data": {} }
{ "type": "error",   "message": "..." }
```

On connect, the server creates a session with its default (LED Blink
pipeline-proof, `blink.py`) workspace already loaded, sends `session`, then the
`build_session_started` and `workspace_loaded` events, then a `state`
snapshot — `BuildSession.snapshot()`, which is the project's identity, every
file's segments (kind, region id, current text), `compile_status`/
`flash_status`/`validation_status`, `compile_output` and `flash_output`
(each `null` until that operation has run), and `flash_ready` (whether a
flash would be accepted right now). A successful `edit_region` gets
`code_edited` (and, when the edited region is the project's
`security_region_id`, `security_region_edited`) followed by a fresh `state`;
a rejected one gets a single `error` and nothing else. `compile` is
field-less — see "Compilation" below — and always gets `compile_started`
followed by either `compile_succeeded` or `compile_failed`, then a fresh
`state`; a `compile` sent while one is already running for the session (or
while a flash is in flight) gets a single `error` instead (no second process
is spawned). `flash` is field-less too — see "Flashing" below — and, when
accepted, gets `flash_started` followed by either `flash_succeeded` or
`flash_failed`, then a fresh `state`; when its preconditions are not met it
gets a single `error` and nothing is attempted at all.

### Compilation (Phase 3B)

A `compile` request runs the real `arduino-cli compile` against the
session's current workspace — not a simulation, not a regex over the
source, not a frontend guess. The flow:

```
BuildService.compile_workspace
    -> BuildWorkspace.materialize(tmp_dir)   writes a throwaway sketch copy
    -> CompilerAdapter.run_compile(request)  real argv (app/build/compiler.py),
                                             spawned by app/build/process.py
    -> CompileOutcome                        real exit code, stdout, stderr, duration
    -> session.compile_status / compile_output updated; events emitted
```

`materialize` never lets the compiler see (or touch) the live workspace —
it writes a fresh temporary sketch directory named after the project's
`.ino` file, and that directory is deleted again once the compile finishes,
success or failure. The compiler's own argv is a fixed array built entirely
from backend/project data (`--fqbn` from `BuildProject.board.fqbn`,
`--build-path` a second temp directory, the sketch path) — a `compile`
request carries no fields a client could use to supply a different board or
raw source; `app/build/compiler.py` has no student input in its command
line at all.

`ARDUINO_CLI_PATH` (env `TRAINER_ARDUINO_CLI_PATH`, defaults to relying on
`arduino-cli` being on PATH) and `BUILD_COMPILE_TIMEOUT_SECONDS` (env
`TRAINER_BUILD_COMPILE_TIMEOUT_SECONDS`, default 180) are in `app/config.py`.
A compile that exceeds the timeout is killed and reported as a truthful
`compile_failed` (category `timeout`), never a false success. If the
configured executable cannot be found, the compile fails as
`toolchain_unavailable` — set `TRAINER_ARDUINO_CLI_PATH` to the full path of
`arduino-cli` when it is not on the PATH of the process that runs uvicorn.

**Spawning is loop-independent, by design.** `arduino-cli` is launched by
`app/build/process.py`, which runs the blocking `subprocess.run` on a worker
thread via `asyncio.to_thread` rather than using `asyncio`'s own subprocess
API. That API is not available on every event loop: on Windows it needs a
`ProactorEventLoop`, and uvicorn (>= 0.36) runs the server on a
`SelectorEventLoop` on win32 whenever it needs worker subprocesses of its
own — i.e. under `--reload` or `--workers N`. Compiling used to raise
`NotImplementedError` out of `loop._make_subprocess_transport` and kill the
`/ws/build` handler in exactly that (development-default) configuration.
The adapters, their `CompilerAdapter`/`FlasherAdapter` protocols and
`BuildService` are unchanged by this: `run_capture` is a normal awaitable,
still takes an argument list, and still never involves a shell.

Both Build Mode projects target board `esp32:esp32:esp32` (the Espressif
`esp32:esp32` core's "ESP32 Dev Module"), and the same `BoardInfo` supplies
the `--fqbn` for both compiling and uploading. The current default (LED
Blink) project needs only that core — no extra libraries — since it uses
nothing beyond `pinMode`/`digitalWrite`/`delay`. The not-currently-loaded
Environmental Monitoring project additionally needs `PubSubClient`,
`Adafruit BME280 Library`, and `Adafruit SSD1306` installed in the Arduino
CLI environment the configured `ARDUINO_CLI_PATH` uses. **The toolchain,
core, and library requirements are environment-dependent**: a machine
without them compiles nothing, and a machine without an ESP32 attached
flashes nothing — in both cases the backend reports the real reason rather
than a substitute one.

### Flashing (Phase 3C)

A `flash` request uploads the firmware this session **actually compiled** to
a physically connected ESP32, using the real `arduino-cli upload`. A
physical serial device is required: with no board attached there is no
upload and no success, and the backend says so in those words.

```
BuildService.flash_workspace
    -> gates: a successful compile exists, and the workspace still matches it
    -> FlasherAdapter.detect_devices(...)  real `arduino-cli board list --format json`
    -> 0 devices -> NO_DEVICE | >1 -> AMBIGUOUS_DEVICE | exactly 1 -> upload to it
    -> FlasherAdapter.run_flash(request)   real argv (app/build/flasher.py),
                                          spawned by app/build/process.py
    -> FlashOutcome                        real exit code, stdout, stderr, duration, port
    -> session.flash_status / flash_output updated; events emitted
```

**Compilation must succeed first, and must still correspond to the code on
screen.** A successful compile retains its build directory as the session's
`CompiledArtifact`, recorded with a content hash of the workspace it was
built from. A flash is refused outright — nothing spawned, no state change —
unless `compile_status` is `succeeded`, that artifact still exists, and the
workspace still hashes to the same value. Editing the security region after a
green build therefore makes flashing unavailable until the student compiles
again, which is what makes "you are flashing what you compiled" a checked
fact rather than a convention. (Because it is a content hash, an edit that is
typed and then undone correctly leaves the build flashable.) A failed compile
leaves no artifact at all, so stale output from an earlier build can never be
uploaded.

**Device discovery is the backend's job.** The frontend cannot name a port,
an executable, a binary, or an upload flag — the `flash` frame has no fields,
and any extra key is rejected by the schema. `ArduinoCliFlasher` reads the
attached devices from `arduino-cli board list` and narrows them with a
documented preference ladder: serial ports only; then ports the CLI
positively identified as this project's platform; then, failing that, ports
reporting a USB vendor id (a classic ESP32 sits behind a CP2102/CH340 bridge
the CLI cannot map to a board, but is always a USB device). Exactly one
survivor is used. **Zero is `NO_DEVICE` and more than one is
`AMBIGUOUS_DEVICE` — the backend never picks a board to write firmware to.**

**Failure domains stay distinct.** `FlashStatus` has `no_device` as its own
terminal state, separate from `failed`, and `FlashFailureCategory`
distinguishes `no_device`, `ambiguous_device`, `device_disconnected`,
`upload_error`, `timeout`, `toolchain_unavailable` and `internal_error`.
Nothing is plugged in is never reported as a compiler error; a missing
`arduino-cli` is never reported as a missing board. Success is decided by
the upload process's real exit code and nothing else — the categories only
choose the label a failure carries.

`BUILD_FLASH_TIMEOUT_SECONDS` (env `TRAINER_BUILD_FLASH_TIMEOUT_SECONDS`,
default 120) and `BUILD_DEVICE_DETECT_TIMEOUT_SECONDS` (env
`TRAINER_BUILD_DEVICE_DETECT_TIMEOUT_SECONDS`, default 20) are in
`app/config.py`; the flasher shares `ARDUINO_CLI_PATH` with the compiler.
An upload that exceeds its timeout is killed and reported as a truthful
`flash_failed` (category `timeout`), never a false success. Only one flash
runs per session, a second request while one is in flight is rejected, a
failed flash leaves both the workspace and the artifact intact so the student
can simply retry, and sessions remain fully isolated from one another.

### Flashing is not validation

`flash_succeeded` means one thing: `arduino-cli upload` exited 0, so the
firmware was transferred to the board. It does **not** mean the firmware
runs, that its sensors read correctly, or that the vulnerability is actually
fixed. Nothing in this codebase checks any of that.

### What Phase 3C still deliberately does not do

No validation of any kind — no MQTT connection, no sensor/LCD inspection, no
automatic Hack Mode re-test, no security or functional test runner — and no
Blockly editor, no scoring, no persistence, and no module identification by
MAC address. Flashing is implemented at the `BuildProject`/`BoardInfo`/
toolchain level, proven against the LED Blink pipeline-proof project
(current default) and previously against Environmental Monitoring (not
currently loaded); the other four modules are not generalised to yet. The
`validation_started`/`security_test_started`/... event vocabulary and
`ValidationStatus` remain declared-but-unused in `app/build/events.py` and
`app/build/models.py`, always `NOT_STARTED`, and the frontend's
Validate/Security-Test controls are disabled placeholders.

### Tests

`tests/test_build_workspace.py` covers loading the Environmental Monitoring
project (still valid, independently constructible, region-model coverage
data) and that `create_default_workspace` now loads the LED Blink
pipeline-proof project instead, locked/editable region identification,
successful and rejected edits (locked region, unknown region, unknown file),
snapshot shape,
`materialize`'s sketch layout and isolation, and the no-execution boundary
(with `process.py`'s one sanctioned exception carved out and separately
tested in `tests/test_build_process.py`, which also exercises `run_capture`
— exit code, stdout/stderr capture, timeout, missing binary, argument-array
safety — on both an ordinary event loop and one that cannot spawn
subprocesses at all). `tests/test_build_compiler.py` covers
`ArduinoCliCompiler` against a small fake executable (success, real
exit-code failure, stdout/stderr capture, timeout, missing binary,
argument-array safety), repeats that whole contract on a
subprocess-incapable event loop as a regression guard for the Windows
`NotImplementedError` crash, and statically checks that `compiler.py` itself
stays entirely execution-primitive-free; it also
includes two tests that run the *real* `arduino-cli` end to end (skipped
unless `TRAINER_ARDUINO_CLI_PATH`/PATH resolves to a working binary — never
faked). `tests/test_build_flasher.py` does the same for `ArduinoCliFlasher`:
successful upload, real exit-code failure, output capture, timeout, missing
binary, discovery finding zero/one/several devices, a device that disappears
mid-upload, `board list` JSON parsing (both CLI output shapes), the
device-selection ladder, argument-array safety, and the static check that
`flasher.py` stays otherwise execution-primitive-free. Its one real-toolchain
test runs **device discovery only** — a test suite must never write firmware
to whatever board happens to be plugged into the machine running it, so real
upload verification is done by hand through Build Mode with a known board
attached. `tests/test_build_service.py` covers session/event bootstrap, edit
orchestration, session isolation, repeated-edit stability,
`compile_workspace` orchestration against a fake `CompilerAdapter`
(status/event correctness, output exposure, failure leaving the workspace
intact, edit-then-retry after a failure, and concurrent-request rejection),
and `flash_workspace` orchestration against a fake `FlasherAdapter`
(rejection with no/failed/stale compile, the no-device and ambiguous-device
results, status/event correctness, output exposure, concurrency, retry, and
artifact lifetime). `tests/test_build_websocket.py` covers the protocol
end-to-end over a real socket, including `compile` and `flash` requests
(against fakes), that a `flash` frame carrying a port/executable/path/flag is
schema-rejected, and that Build Mode never touches Hack Mode's session
registry or scenario state.
