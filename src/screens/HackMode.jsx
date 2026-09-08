import { useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import HackTerminal from '../components/HackTerminal'
import useHackSocket, { CONNECTION_STATUS } from '../hooks/useHackSocket'
import { GUIDED_STEPS } from '../data'

const TOOLS = [
  { id: 'nmap', name: 'nmap', action: 'scan', command: 'nmap' },
  { id: 'sub', name: 'mosquitto_sub', action: 'listen', command: 'mosquitto_sub' },
  { id: 'pub', name: 'mosquitto_pub', action: 'publish', command: 'mosquitto_pub' },
  { id: 'cap', name: 'wireshark / tcpdump', action: 'capture', command: 'tcpdump -i lab0 port 1883' },
  { id: 'exp', name: 'mqtt-explorer', action: 'browse', command: 'mqtt-explorer' },
]

// LIVE HACK METRICS stays hardcoded on purpose: TTE/attempt-count/efficiency
// scoring is Phase 2E's job, not this phase's — see CLAUDE.md and the 2D-C
// brief. Everything else on this screen (Guided Steps, Activity Log, Target
// Device) is now driven by scenarioEvents/scenarioState below.
const ATTEMPTS = 3

// Backend `ScenarioEventType` values (app/scenarios/events.py) -> Activity
// Log copy. Deliberately a plain lookup, not a switch: an event type this
// map doesn't know about (the closed set changed) falls back to the raw
// value below rather than silently dropping the row.
const EVENT_LABELS = {
  firmware_extracted: 'Firmware extracted',
  firmware_analyzed: 'Firmware analyzed',
  broker_discovered: 'MQTT broker discovered',
  topic_discovered: 'MQTT topic discovered',
  mqtt_service_scanned: 'MQTT service scanned',
  mqtt_observed: 'MQTT telemetry observed',
  spoof_attempted: 'Spoof attempt',
  spoof_rejected: 'Spoof rejected',
  spoof_succeeded: 'Spoof successful',
  target_impacted: 'Target impacted',
  attack_completed: 'Attack completed',
}

function nowStamp() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

// Terminal-line elapsed timestamp — Phase 2E. This is deliberately NOT the
// Activity Log's wall-clock `nowStamp()` above: the terminal shows time
// elapsed *since the WebSocket session began* (see the `onSession` handler,
// which stamps `sessionStartRef` once per connection), always as full
// [HH:MM:SS] regardless of magnitude. `pad2` avoids a locale-dependent
// formatter for a fixed, always-2-digit field.
function pad2(n) {
  return String(n).padStart(2, '0')
}

// A module-level wrapper around the impure `Date.now()`, for the same reason
// `nowStamp()` above wraps `new Date()`: eslint's react-hooks purity check
// flags a direct call to a known-impure global textually inside a component
// body (even one that, like `onSession` below, only ever runs later as an
// event callback, never during render) — routing it through an ordinary
// named function the component merely calls sidesteps that false positive.
function sessionStartTimestamp() {
  return Date.now()
}

function formatElapsed(startMs) {
  const totalSeconds = Math.max(0, Math.floor((Date.now() - startMs) / 1000))
  const hh = Math.floor(totalSeconds / 3600)
  const mm = Math.floor((totalSeconds % 3600) / 60)
  const ss = totalSeconds % 60
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}`
}

// The 3 existing Guided Steps map onto milestones along the backend's real
// 6-stage flow (firmware extract -> analyze -> broker/topic discovery ->
// MQTT observation -> spoof attempt -> successful attack): this screen's
// step list isn't growing to 6 rows, so each row reflects the state flags
// that mark its milestone reached, not a single event in isolation.
function guidedStepsDone(scenarioState) {
  if (!scenarioState) return [false, false, false]
  const { discovery, attack } = scenarioState
  return [
    Boolean(discovery?.broker_discovered),
    Boolean(discovery?.mqtt_observed),
    Boolean(attack?.spoof_successful),
  ]
}

const STATUS_LABEL = {
  [CONNECTION_STATUS.CONNECTING]: 'LINK: CONNECTING…',
  [CONNECTION_STATUS.CONNECTED]: 'LINK: CONNECTED',
  [CONNECTION_STATUS.DISCONNECTED]: 'LINK: DISCONNECTED',
  [CONNECTION_STATUS.ERROR]: 'LINK: ERROR',
}

// True-color ANSI escapes matching the legacy .term-line.warn color, used
// only for frontend-injected notices (protocol errors) — never for backend
// `output` text, which is written verbatim.
const ANSI_ERR = '\x1b[38;2;220;90;90m'
const ANSI_RESET = '\x1b[0m'

export default function HackMode({ onBack, onBuild, onSuccess, onMenu }) {
  const termRef = useRef(null)
  const inputBufferRef = useRef('')

  // Phase 2E elapsed terminal timestamps. `sessionStartRef` is set once, in
  // `onSession` below — the earliest point the backend confirms a Hack Mode
  // session actually exists — and is never read reactively (no re-render,
  // no ticking interval to duplicate under Strict Mode): elapsed time is
  // computed fresh, on demand, only when a line is about to be written.
  // `atLineStartRef` tracks whether the terminal's cursor is at the start of
  // a not-yet-timestamped line, so both live keystroke echo and whole
  // backend output blocks share one prefixing rule instead of two.
  const sessionStartRef = useRef(null)
  const atLineStartRef = useRef(true)

  const [marked, setMarked] = useState(false)

  // Session id, scenario events, and the scenario state snapshot now drive
  // the Activity Log / Guided Steps / Target Device panels below. All three
  // are local to this component (not lifted to App.jsx): HackMode already
  // fully unmounts on every "back to menu" -> "hack" round trip (App.jsx has
  // no stable key keeping it alive across screen changes), which is what
  // resets this state for a new session — no extra teardown code needed.
  const [, setSessionId] = useState(null)
  const [scenarioEvents, setScenarioEvents] = useState([])
  const [scenarioState, setScenarioState] = useState(null)

  // Writes a (possibly multi-line) chunk to the terminal, prefixing each
  // line with the current elapsed timestamp — the single mechanism every
  // terminal-rendered line goes through, whether it's a whole backend output
  // block, a protocol error notice, or a tool-button-echoed command line.
  // Splitting strictly on "\r\n" is safe here (not a fragile byte scanner):
  // that literal sequence is the transport's own defined line terminator
  // (see LINE_ENDING in backend/app/websocket.py) and an ANSI SGR sequence
  // never contains a raw CR/LF, so this can never cut one in half. Blank
  // lines are left unstamped (no floating bare bracket); a line's own
  // content — including any ANSI color codes in it — is untouched, since
  // the prefix is always written as a separate, preceding term.write() call.
  function writeTimestamped(text) {
    const term = termRef.current
    if (!term || !text) return
    const endsWithNewline = text.endsWith('\r\n')
    const segments = text.split('\r\n')
    if (endsWithNewline) segments.pop()
    segments.forEach((segment, i) => {
      if (atLineStartRef.current) {
        if (segment.length > 0 && sessionStartRef.current) {
          term.write(`[${formatElapsed(sessionStartRef.current)}] `)
        }
        atLineStartRef.current = false
      }
      term.write(segment)
      if (i < segments.length - 1 || endsWithNewline) {
        term.write('\r\n')
        atLineStartRef.current = true
      }
    })
  }

  const { status, sendInput, sendResize } = useHackSocket({
    onSession: (message) => {
      setSessionId(message.session_id)
      // A `session` frame only ever arrives once per connection (right after
      // accept — see backend/app/websocket.py), so this is defensive rather
      // than something that fires mid-session: it guarantees a brand new
      // session can never inherit a stale event/state carried over in this
      // component's own state. It is also the Phase 2E session-start anchor:
      // the earliest backend-confirmed point a Hack Mode session exists, so
      // the elapsed clock and the scenario state reset share one trigger.
      setScenarioEvents([])
      setScenarioState(null)
      sessionStartRef.current = sessionStartTimestamp()
      atLineStartRef.current = true
    },
    onOutput: (data) => writeTimestamped(data),
    onAction: (action) => {
      if (action === 'clear') {
        termRef.current?.clear()
        // `clear` only wipes the visible screen — sessionStartRef is
        // untouched, so the elapsed clock keeps counting straight through.
        // Only the "start of an unstamped line" bookkeeping resets, so the
        // next line drawn after a clear gets a fresh, current timestamp.
        atLineStartRef.current = true
      }
    },
    onError: (message) => {
      writeTimestamped(`${ANSI_ERR}[protocol error] ${message}${ANSI_RESET}\r\n`)
    },
    onEvent: (message) => {
      setScenarioEvents((events) => {
        // Guard against the exact same frame landing twice (e.g. a stray
        // redelivery), not against the backend legitimately re-emitting the
        // same event type later for a distinct occurrence (a rejected spoof
        // attempt, for instance, can happen more than once and each is real
        // activity worth logging).
        const last = events[events.length - 1]
        if (last && last.event === message.event && JSON.stringify(last.data) === JSON.stringify(message.data)) {
          return events
        }
        return [...events, { event: message.event, data: message.data, at: nowStamp() }]
      })
    },
    onState: (data) => setScenarioState(data),
  })

  const stepsDone = guidedStepsDone(scenarioState)
  const currentStepIndex = stepsDone.findIndex((done) => !done)
  const attackSuccessful = Boolean(scenarioState?.completion?.attack_successful)

  // The single path a completed command line takes to the backend, whether
  // it came from the terminal's own Enter key or a tool button below —
  // there is no second, mock execution path.
  function submitCommand(line) {
    sendInput(line)
  }

  function runTool(tool) {
    // Tool buttons must send exactly what typing the command and pressing
    // Enter would: this locally echoes it (mirroring the character-by-
    // character echo `handleTerminalInput` does for real typing, and using
    // the same `writeTimestamped` prefixing) and then sends it through the
    // same `submitCommand` — one echo mechanism, one submission path.
    writeTimestamped(`${tool.command}\r\n`)
    submitCommand(tool.command)
  }

  // Local terminal line discipline: the browser still owns character echo,
  // backspace, and Ctrl+C. Only a completed line is ever sent onward — see
  // backend/app/websocket.py, which expects exactly one `input` frame per
  // completed command line, never individual keystrokes. The elapsed
  // timestamp is written once, right before the first visible character of
  // a fresh line (never per keystroke after that — backspace and further
  // typing on the same line never re-trigger it), using the same
  // `atLineStartRef` bookkeeping `writeTimestamped` uses for backend output,
  // so a typed line and a backend-printed line are indistinguishable in how
  // they got their prefix.
  function handleTerminalInput(data) {
    const term = termRef.current
    if (!term) return
    for (const ch of data) {
      if (ch === '\r') {
        term.write('\r\n')
        atLineStartRef.current = true
        const cmd = inputBufferRef.current.trim()
        inputBufferRef.current = ''
        if (cmd) submitCommand(cmd)
      } else if (ch === '\x7f') {
        if (inputBufferRef.current.length > 0) {
          inputBufferRef.current = inputBufferRef.current.slice(0, -1)
          term.write('\b \b')
        }
      } else if (ch === '\x03') {
        inputBufferRef.current = ''
        term.write('^C\r\n')
        atLineStartRef.current = true
      } else if (ch >= ' ') {
        if (atLineStartRef.current) {
          if (sessionStartRef.current) {
            term.write(`[${formatElapsed(sessionStartRef.current)}] `)
          }
          atLineStartRef.current = false
        }
        inputBufferRef.current += ch
        term.write(ch)
      }
    }
  }

  function handleTerminalResize({ cols, rows }) {
    sendResize(cols, rows)
  }

  return (
    <div className="page">
      <AppHeader
        title="SECURITY TESTING TERMINAL — HACK MODE"
        right={
          <>
            SANDBOX NETWORK ISOLATED | SCENARIO: Weak MQTT Auth | {STATUS_LABEL[status]}
            {attackSuccessful ? ' | OBJECTIVE COMPLETE' : ''}
          </>
        }
        onMenu={onMenu}
      />
      <main className="page-body hack-grid">
        <aside className="side-col">
          <section className="panel">
            <h3>RECON & HACKING ESSENTIALS</h3>
            <ul className="tool-list">
              {TOOLS.map((t) => (
                <li key={t.id}>
                  <code>{t.name}</code>
                  <button type="button" className="pill" onClick={() => runTool(t)}>
                    {t.action}
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="panel">
            <h3>GUIDED STEPS</h3>
            <ol className="steps">
              {GUIDED_STEPS.map((label, i) => (
                <li
                  key={label}
                  className={stepsDone[i] ? 'done' : i === currentStepIndex ? 'current' : ''}
                >
                  <span>{i + 1}</span>
                  {label}
                </li>
              ))}
            </ol>
          </section>
        </aside>
        <section className="terminal">
          <HackTerminal ref={termRef} onInput={handleTerminalInput} onResize={handleTerminalResize} />
        </section>
        <aside className="side-col">
          <section className="panel">
            <h3>LIVE HACK METRICS</h3>
            <div className="metric">
              <strong>00:04:12</strong>
              <span>TIME-TO-EXPLOITATION</span>
            </div>
            <div className="metric">
              <strong>{ATTEMPTS}</strong>
              <span>EXPLOITATION ATTEMPTS</span>
            </div>
            <div className="metric">
              <strong>82%</strong>
              <span>RECONNAISSANCE EFFICIENCY</span>
            </div>
          </section>
          <section className="panel">
            <h3>ACTIVITY LOG</h3>
            <ul className="plain-log">
              {scenarioEvents.length === 0 ? (
                <li className="muted">No activity yet — run a command to begin.</li>
              ) : (
                scenarioEvents.map((entry, i) => (
                  <li key={i}>
                    {entry.at} — {EVENT_LABELS[entry.event] || entry.event}
                  </li>
                ))
              )}
            </ul>
          </section>
          <section className="panel dashed">
            <h3>TARGET DEVICE</h3>
            {scenarioState ? (
              <>
                <p>Status: {scenarioState.target.device_status.toUpperCase()}</p>
                <p>
                  Broker:{' '}
                  {scenarioState.discovery.broker_discovered
                    ? `${scenarioState.target.ip_address}:${scenarioState.target.mqtt_port}`
                    : 'UNKNOWN — recover from firmware'}
                </p>
                <p>
                  Topic:{' '}
                  {scenarioState.discovery.topic_discovered
                    ? scenarioState.target.mqtt_topic
                    : 'UNKNOWN — recover from firmware'}
                </p>
                {scenarioState.discovery.mqtt_observed ? (
                  <p>
                    Telemetry: {scenarioState.environment.temperature}°C
                    {scenarioState.attack.spoof_active ? ' (SPOOFED)' : ''}
                  </p>
                ) : null}
              </>
            ) : (
              <p>No contact with the target yet — begin recon.</p>
            )}
          </section>
        </aside>
      </main>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          ← BACK TO MENU
        </button>
        <button
          type="button"
          className="btn-solid"
          onClick={() => {
            setMarked(true)
            onSuccess?.()
          }}
        >
          {marked ? '[ ATTACK MARKED ]' : '[ MARK ATTACK SUCCESSFUL ]'}
        </button>
        <button type="button" onClick={onBuild}>
          PROCEED TO BUILD MODE →
        </button>
      </footer>
    </div>
  )
}
