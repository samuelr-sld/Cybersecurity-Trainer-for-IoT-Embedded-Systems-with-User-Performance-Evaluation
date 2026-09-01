import { useMemo, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import HackTerminal from '../components/HackTerminal'
import { GUIDED_STEPS } from '../data'

const TOOLS = [
  { id: 'nmap', name: 'nmap', action: 'scan' },
  { id: 'sub', name: 'mosquitto_sub', action: 'listen' },
  { id: 'pub', name: 'mosquitto_pub', action: 'publish' },
  { id: 'cap', name: 'wireshark / tcpdump', action: 'capture' },
  { id: 'exp', name: 'mqtt-explorer', action: 'browse' },
]

const INITIAL_LINES = [
  { kind: 'prompt', text: 'student@sandbox:~$ nmap -p 1883 192.168.4.0/24' },
  { kind: 'ok', text: '1883/tcp open mqtt (no auth required)' },
  { kind: 'prompt', text: 'student@sandbox:~$ mosquitto_sub -h 192.168.4.1 -t "#" -v' },
  { kind: 'warn', text: '! receiving unauthenticated telemetry stream...' },
  { kind: 'prompt', text: 'student@sandbox:~$ mosquitto_pub -h 192.168.4.1 -t sandbox/mqtt/telemetry -m "..."' },
]

const INITIAL_LOG = [
  '10:41:02 — nmap scan started',
  '10:41:20 — port 1883 open (no auth)',
  '10:42:05 — subscribed to broker',
  '10:44:10 — forged payload published',
]

const PROMPT = 'student@sandbox:~$ '

// True-color ANSI escapes matching the legacy .term-line.ok / .term-line.warn
// colors, so the xterm-rendered output stays visually identical to before.
const ANSI = {
  ok: '\x1b[38;2;125;206;138m',
  warn: '\x1b[38;2;240;179;90m',
  reset: '\x1b[0m',
}

function formatLine({ kind, text }) {
  const color = ANSI[kind]
  return color ? `${color}${text}${ANSI.reset}\r\n` : `${text}\r\n`
}

function nowStamp() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

// Computed once at module scope (not per render/mount) so HackTerminal
// always receives the same string reference for its one-time boot write.
const INITIAL_BOOT_TEXT = INITIAL_LINES.map(formatLine).join('') + PROMPT

export default function HackMode({ onBack, onBuild, onSuccess, onMenu }) {
  const termRef = useRef(null)
  const inputBufferRef = useRef('')

  const [steps, setSteps] = useState([true, true, false])
  const [attempts, setAttempts] = useState(3)
  const [log, setLog] = useState(INITIAL_LOG)
  const [marked, setMarked] = useState(false)

  const responses = useMemo(
    () => ({
      nmap: [
        { kind: 'prompt', text: 'student@sandbox:~$ nmap -p 1883 192.168.4.0/24' },
        { kind: 'ok', text: 'Nmap scan report for 192.168.4.1' },
        { kind: 'ok', text: '1883/tcp open mqtt (sandbox broker, no auth required)' },
      ],
      sub: [
        { kind: 'prompt', text: 'student@sandbox:~$ mosquitto_sub -h 192.168.4.1 -t "#" -v' },
        { kind: 'warn', text: '! receiving unauthenticated telemetry stream...' },
        { kind: 'out', text: 'sandbox/mqtt/telemetry temp=24.1 hum=61' },
      ],
      pub: [
        {
          kind: 'prompt',
          text: 'student@sandbox:~$ mosquitto_pub -h 192.168.4.1 -t sandbox/mqtt/telemetry -m "SANDBOX_TEST"',
        },
        { kind: 'ok', text: 'sandbox broker accepted unauthenticated publish (lab finding).' },
      ],
      cap: [
        { kind: 'prompt', text: 'student@sandbox:~$ tcpdump -i lab0 port 1883' },
        { kind: 'out', text: 'capture started on isolated interface lab0 (MQTT in cleartext).' },
      ],
      exp: [
        { kind: 'prompt', text: 'student@sandbox:~$ mqtt-explorer 192.168.4.1' },
        { kind: 'out', text: 'topics visible without credentials: sandbox/mqtt/#' },
      ],
    }),
    [],
  )

  function writeLines(entries) {
    termRef.current?.write(entries.map(formatLine).join(''))
  }

  function runTool(id) {
    const extra = responses[id] || [{ kind: 'out', text: 'tool unavailable in this sandbox.' }]
    writeLines(extra)
    termRef.current?.write(PROMPT)
    if (id === 'nmap') {
      setSteps((s) => [true, s[1], s[2]])
      setLog((l) => [...l, `${nowStamp()} — nmap scan started`])
    }
    if (id === 'sub') {
      setSteps((s) => [s[0], true, s[2]])
      setLog((l) => [...l, `${nowStamp()} — subscribed to broker`])
    }
    if (id === 'pub') {
      setSteps([true, true, true])
      setAttempts((n) => n + 1)
      setLog((l) => [...l, `${nowStamp()} — lab telemetry published`])
    }
  }

  // The boot banner is written by HackTerminal itself (see `bootText` prop
  // below), inside the same effect that creates the Terminal instance, so
  // it can't run more times than the terminal instance itself is created.
  // ---------------------------------------------------------------------
  // PHASE 1 — LOCAL MOCK TRANSPORT (temporary).
  //
  // Everything below simulates a shell entirely client-side: it echoes
  // keystrokes itself, buffers a line locally, and pattern-matches the
  // submitted text by substring to decide which canned response to show.
  // It exists only to exercise HackTerminal's onInput/write seam before
  // a real backend exists.
  //
  // Phase 2 must REPLACE this block wholesale with a WebSocket transport
  // (raw keystrokes forwarded to a FastAPI-fronted constrained PTY, raw
  // bytes from the PTY written back via the same imperative `write` API).
  // Do not evolve the substring matching below into real shell semantics
  // — it is a placeholder, not a shell implementation to grow.
  // ---------------------------------------------------------------------
  function runMockCommand(cmd) {
    const lower = cmd.toLowerCase()
    if (lower.includes('nmap')) runTool('nmap')
    else if (lower.includes('mosquitto_sub')) runTool('sub')
    else if (lower.includes('mosquitto_pub')) runTool('pub')
    else if (lower.includes('tcpdump') || lower.includes('wireshark')) runTool('cap')
    else if (lower.includes('mqtt-explorer') || lower.startsWith('mqtt')) runTool('exp')
    else {
      termRef.current?.write(`sandbox: command recorded (${cmd.split(' ')[0]})\r\n${PROMPT}`)
    }
  }

  function handleTerminalInput(data) {
    const term = termRef.current
    if (!term) return
    for (const ch of data) {
      if (ch === '\r') {
        term.write('\r\n')
        const cmd = inputBufferRef.current.trim()
        inputBufferRef.current = ''
        if (cmd) runMockCommand(cmd)
        else term.write(PROMPT)
      } else if (ch === '\x7f') {
        if (inputBufferRef.current.length > 0) {
          inputBufferRef.current = inputBufferRef.current.slice(0, -1)
          term.write('\b \b')
        }
      } else if (ch === '\x03') {
        inputBufferRef.current = ''
        term.write(`^C\r\n${PROMPT}`)
      } else if (ch >= ' ') {
        inputBufferRef.current += ch
        term.write(ch)
      }
    }
  }
  // --- end Phase 1 mock transport -----------------------------------------

  // Reserved seam for Phase 2: forward {cols, rows} over the WebSocket as a
  // PTY resize message. No-op today — HackTerminal already fits/reflows
  // itself locally via ResizeObserver regardless of this callback.
  function handleTerminalResize() {}

  return (
    <div className="page">
      <AppHeader
        title="SECURITY TESTING TERMINAL — HACK MODE"
        right={<>SANDBOX NETWORK ISOLATED | SCENARIO: Weak MQTT Auth</>}
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
                  <button type="button" className="pill" onClick={() => runTool(t.id)}>
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
                <li key={label} className={steps[i] ? 'done' : ''}>
                  <span>{i + 1}</span>
                  {label}
                </li>
              ))}
            </ol>
          </section>
        </aside>
        <section className="terminal">
          <HackTerminal
            ref={termRef}
            onInput={handleTerminalInput}
            onResize={handleTerminalResize}
            bootText={INITIAL_BOOT_TEXT}
          />
        </section>
        <aside className="side-col">
          <section className="panel">
            <h3>LIVE HACK METRICS</h3>
            <div className="metric">
              <strong>00:04:12</strong>
              <span>TIME-TO-EXPLOITATION</span>
            </div>
            <div className="metric">
              <strong>{attempts}</strong>
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
              {log.map((row) => (
                <li key={row}>{row}</li>
              ))}
            </ul>
          </section>
          <section className="panel dashed">
            <h3>TARGET DEVICE</h3>
            <p>MAC: 3C:71:BF:2A:9E</p>
            <p>IP: 192.168.4.1</p>
            <p>Broker: Mosquitto (local)</p>
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
            setSteps([true, true, true])
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
