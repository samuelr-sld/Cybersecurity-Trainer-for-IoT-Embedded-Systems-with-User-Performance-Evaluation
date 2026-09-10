# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A React + Vite single-page app simulating an IoT/embedded cybersecurity training sandbox ("Weak MQTT Auth" scenario). Students perform recon/exploitation against a simulated ESP32 + MQTT broker device ("Hack Mode"), then remediate the firmware ("Build Mode"), and professors review performance metrics ("Dashboard"). This is currently a **low-fidelity frontend prototype** — there is no backend, no real device, no real MQTT broker, and no persistence; everything is simulated client-side with in-memory React state and fabricated/seeded data.

## Commands

```
npm run dev       # start Vite dev server with HMR
npm run build     # production build to dist/
npm run preview   # preview the production build
npm run lint      # eslint over the whole repo
```

There is no test suite configured in this repo.

## Architecture

**Single-file screen router.** `src/App.jsx` holds all top-level state (`screen`, `role`, `registry`, `student`, `evalStudent`, `professor`, `overlay`, `menuOpen`) and renders one of the screen components below based on a `screen` string — there is no router library. Screens communicate purely through callback props (`onBack`, `onMenu`, `onNext`, etc.) passed down from `App.jsx`; there is no context or global store.

Screen flow: `RoleSelect` → (`StudentAccess` | `ProfessorAccess`) → `MainMenu` → (`HackMode` | `BuildMode` | `Dashboard`). `HackMode` and `BuildMode` link to each other directly. `Dashboard` is reachable both from a student's own `MainMenu` (evaluating themselves) and from `ProfessorAccess` (evaluating any registered student), which is why it takes a `role` prop that changes its back-navigation target and enables/disables the "next student" control.

**Screens** live in `src/screens/`, one file per screen, each a default-exported component taking navigation callbacks + data as props. **Shared components** (`AppHeader`, `InfoBanner`) live in `src/components/`.

**All simulated/seeded data lives in `src/data.js`**: `STUDENTS` (48 procedurally generated student records with metrics/logs, index 0 is the fixed demo student "Juan Dela Cruz"), `SCENARIOS`, `GUIDED_STEPS`. When changing seeded/demo content, edit this file rather than hardcoding values in screens. Build Mode's firmware project is no longer here — it is backend-sourced (see below).

**Hack Mode's terminal is not xterm.js** — despite `@xterm/xterm` and `@xterm/addon-fit` being dependencies in `package.json`, `HackMode.jsx` implements its own lightweight terminal via a `lines` state array rendered as styled `<div>`s, with canned command responses keyed by tool id (`nmap`, `sub`, `pub`, `cap`, `exp`) in a `responses` map. Free-text input is pattern-matched by substring in `submitCommand`.

**Build Mode is a real backend-backed workspace with real compilation, not a mock.** `BuildMode.jsx` connects to `/ws/build` via `src/hooks/useBuildSocket.js`. The backend (`backend/app/build/`, `backend/app/build_sessions.py`, `backend/app/build_websocket.py`) owns one project per session, represented as an ordered sequence of locked/editable regions (`backend/app/build/models.py`, `workspace.py`). The default project a fresh session loads is the **LED Blink pipeline-proof project** (`backend/app/build/blink.py`, project id `led-blink-poc`) — a deliberately trivial one-file, one-editable-region firmware whose only job is to prove the write → compile → flash → physical-execution pipeline works end to end against a real ESP32. The earlier Environmental Monitoring / Weak MQTT firmware (`backend/app/build/environmental.py`, `create_environmental_monitoring_project`) predates the finalized five-panel scope and is no longer loaded by default; the module remains in the codebase, unused, for when that scope resumes. Only the one editable region can be edited (named `security_logic` on the environmental project, `blink_program` on the current default); the backend rejects any attempt to edit a locked region or an unknown region id, regardless of what the frontend sends. A `compile` request runs the real `arduino-cli` (`backend/app/build/compiler.py`, which builds the argument array and hands it to `backend/app/build/process.py` — the one module in the whole backend allowed to spawn a process, and the one both the compiler and the flasher go through) against a throwaway materialized copy of the workspace — `compile_status` moves through `not_started`/`running`/`succeeded`/`failed` for real, driven by the actual compiler exit code, and `compile_output` carries its real stdout/stderr back to the UI.

A `flash` request runs the real `arduino-cli upload` (`backend/app/build/flasher.py`) against a **physically connected ESP32**, so it needs real hardware attached. The backend discovers the serial device itself via `arduino-cli board list` and never lets the frontend name a port, executable, binary, or flag — the `flash` frame is field-less. Flashing requires a successful compile whose retained build output still matches the workspace by content hash (`BuildSession.flash_ready`), so editing the region after a green build disables flashing until it is compiled again. `flash_status` adds two members `compile_status` does not have: `detecting`, and `no_device` as a terminal state distinct from `failed`, because "no ESP32 is plugged in" must never be presented as a build or toolchain problem. A successful flash means the upload process exited 0 — nothing more.

Validation is still not implemented: nothing connects to MQTT, reads sensors, re-runs Hack Mode, or checks whether the remediation actually works. `validation_status` stays always `"not_started"`, and the Validate/Security-Test controls in the UI are disabled placeholders.

**Dashboard charts are hand-rolled inline SVG/CSS** (`BarChart`, `LineChart`, `RadarChart` inside `Dashboard.jsx`) — no charting library is used.

**Styling**: a single global `src/App.css` (~800 lines) plus a small `src/index.css` reset. No CSS modules, no Tailwind, no styled-components — class names are plain strings shared across screens (e.g. `.page`, `.panel`, `.btn-solid`, `.link-footer`).

**No routing library, no state management library, no backend/API calls.** Login, registration, and evaluation "lookups" are all synchronous in-memory array searches against `registry` (in `App.jsx`) or `STUDENTS` (in `data.js`) — nothing is persisted across a page reload.
