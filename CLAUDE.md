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

**All simulated/seeded data lives in `src/data.js`**: `STUDENTS` (48 procedurally generated student records with metrics/logs, index 0 is the fixed demo student "Juan Dela Cruz"), `SCENARIOS`, `PROJECT_FILES` (the fake Arduino/ESP32 sketch shown in Build Mode's editor), `GUIDED_STEPS`, `REMEDIATION_ITEMS`. When changing seeded/demo content, edit this file rather than hardcoding values in screens.

**Hack Mode's terminal is not xterm.js** — despite `@xterm/xterm` and `@xterm/addon-fit` being dependencies in `package.json`, `HackMode.jsx` implements its own lightweight terminal via a `lines` state array rendered as styled `<div>`s, with canned command responses keyed by tool id (`nmap`, `sub`, `pub`, `cap`, `exp`) in a `responses` map. Free-text input is pattern-matched by substring in `submitCommand`.

**Build Mode's "compile" is regex-based**, not a real compiler: `compile()` in `BuildMode.jsx` checks the concatenated source of `main.ino` + `mqtt_config.h` against regexes for auth/ACL/TLS keywords to decide whether validation "passes."

**Dashboard charts are hand-rolled inline SVG/CSS** (`BarChart`, `LineChart`, `RadarChart` inside `Dashboard.jsx`) — no charting library is used.

**Styling**: a single global `src/App.css` (~800 lines) plus a small `src/index.css` reset. No CSS modules, no Tailwind, no styled-components — class names are plain strings shared across screens (e.g. `.page`, `.panel`, `.btn-solid`, `.link-footer`).

**No routing library, no state management library, no backend/API calls.** Login, registration, and evaluation "lookups" are all synchronous in-memory array searches against `registry` (in `App.jsx`) or `STUDENTS` (in `data.js`) — nothing is persisted across a page reload.
