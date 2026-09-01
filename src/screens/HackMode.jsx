import { useMemo, useState } from 'react'
import AppHeader from '../components/AppHeader'
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

function nowStamp() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

export default function HackMode({ onBack, onBuild, onSuccess, onMenu }) {
  const [lines, setLines] = useState(INITIAL_LINES)
  const [input, setInput] = useState('')
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

  function runTool(id) {
    const extra = responses[id] || [{ kind: 'out', text: 'tool unavailable in this sandbox.' }]
    setLines((prev) => [...prev, ...extra])
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

  function submitCommand(e) {
    e.preventDefault()
    const cmd = input.trim()
    if (!cmd) return
    const lower = cmd.toLowerCase()
    if (lower.includes('nmap')) runTool('nmap')
    else if (lower.includes('mosquitto_sub')) runTool('sub')
    else if (lower.includes('mosquitto_pub')) runTool('pub')
    else if (lower.includes('tcpdump') || lower.includes('wireshark')) runTool('cap')
    else if (lower.includes('mqtt-explorer') || lower.startsWith('mqtt')) runTool('exp')
    else {
      setLines((prev) => [
        ...prev,
        { kind: 'prompt', text: `student@sandbox:~$ ${cmd}` },
        { kind: 'out', text: `sandbox: command recorded (${cmd.split(' ')[0]})` },
      ])
    }
    setInput('')
  }

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
          <div className="term-body">
            {lines.map((line, i) => (
              <div key={i} className={`term-line ${line.kind}`}>
                {line.text}
              </div>
            ))}
          </div>
          <form className="term-input" onSubmit={submitCommand}>
            <span>&gt;</span>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              aria-label="Terminal input"
              autoComplete="off"
            />
          </form>
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
