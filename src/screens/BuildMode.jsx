import { useState } from 'react'
import AppHeader from '../components/AppHeader'
import { PROJECT_FILES, REMEDIATION_ITEMS } from '../data'

export default function BuildMode({ onBack, onMenu }) {
  const [files, setFiles] = useState(PROJECT_FILES)
  const [active, setActive] = useState('main.ino')
  const [openTabs, setOpenTabs] = useState(['main.ino', 'mqtt_config.h'])
  const [checks, setChecks] = useState([false, false, false])
  const [output, setOutput] = useState('✓ Compilation successful — 812,340 bytes (61%)')
  const [busy, setBusy] = useState(false)
  const [auth, setAuth] = useState('NONE')

  function compile(kind) {
    setBusy(true)
    const src = `${files['main.ino']}\n${files['mqtt_config.h']}`
    const hasAuth = /username|password|mqttUser|MQTT_USER/i.test(src)
    const hasAcl = /ACL|allow.*topic|subscribe.*restrict/i.test(src)
    const hasTls = /8883|WiFiClientSecure|setCACert|TLS/i.test(src)
    setChecks([hasAuth, hasAcl, hasTls])
    setAuth(hasAuth ? 'USER/PASS' : 'NONE')
    setTimeout(() => {
      if (kind === 'upload') setOutput('✓ Upload complete — device /dev/ttyUSB0')
      else if (kind === 'validate') {
        setOutput(
          hasAuth && hasAcl && hasTls
            ? '✓ Validation passed — broker link secured'
            : '✗ Validation failed — complete the remediation checklist',
        )
      } else setOutput('✓ Compilation successful — 812,340 bytes (61%)')
      setBusy(false)
    }, 400)
  }

  return (
    <div className="page">
      <AppHeader
        title="ESP32 WORKSTATION IDE — BUILD MODE"
        right={<>MODULE: IoT MQTT COMM PANEL | PORT: /dev/ttyUSB0</>}
        onMenu={onMenu}
      />
      <nav className="ide-menu" aria-label="IDE menu">
        <span>File</span>
        <span>Edit</span>
        <span>Sketch</span>
        <span>Board: ESP32 Dev Module</span>
        <span>Help</span>
      </nav>
      <main className="page-body ide-grid">
        <aside className="side-col">
          <section className="panel">
            <h3>VULNERABILITY SCENARIO</h3>
            <p className="file-active">weak_mqtt_auth.ino</p>
          </section>
          <section className="panel">
            <h3>PROJECT FILES</h3>
            <ul className="file-list">
              {Object.keys(files).map((name) => (
                <li key={name}>
                  <button
                    type="button"
                    className={name === active ? 'active' : ''}
                    onClick={() => {
                      setActive(name)
                      setOpenTabs((tabs) => (tabs.includes(name) ? tabs : [...tabs, name]))
                    }}
                  >
                    {name}
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="panel">
            <h3>REMEDIATION CHECKLIST</h3>
            <ul className="checks">
              {REMEDIATION_ITEMS.map((item, i) => (
                <li key={item}>
                  <label>
                    <input
                      type="checkbox"
                      checked={checks[i]}
                      onChange={() =>
                        setChecks((c) => c.map((v, idx) => (idx === i ? !v : v)))
                      }
                    />
                    {item}
                  </label>
                </li>
              ))}
            </ul>
          </section>
        </aside>
        <section className="editor">
          <div className="tabs">
            {openTabs.map((tab) => (
              <button
                type="button"
                key={tab}
                className={tab === active ? 'active' : ''}
                onClick={() => setActive(tab)}
              >
                {tab}
              </button>
            ))}
          </div>
          <div className="editor-body">
            <pre className="gutter">
              {files[active].split('\n').map((_, i) => (
                <span key={i}>{i + 1}</span>
              ))}
            </pre>
            <textarea
              value={files[active]}
              spellCheck={false}
              onChange={(e) => setFiles({ ...files, [active]: e.target.value })}
            />
          </div>
          <div className="console">
            <div className="console-out">{output}</div>
            <div className="console-actions">
              <button type="button" className="btn-solid" disabled={busy} onClick={() => compile('upload')}>
                ▲ UPLOAD
              </button>
              <button type="button" className="btn-outline" disabled={busy} onClick={() => compile('compile')}>
                ▶ COMPILE
              </button>
              <button type="button" className="btn-outline" onClick={() => setBusy(false)}>
                ■ STOP
              </button>
              <button type="button" className="btn-outline grow" disabled={busy} onClick={() => compile('validate')}>
                RUN VALIDATION TEST
              </button>
            </div>
          </div>
        </section>
        <aside className="side-col">
          <section className="panel dashed">
            <h3>GPIO MONITOR</h3>
            <p>GPIO 4 MODE: OUTPUT</p>
            <p>GPIO 5 STATE: HIGH</p>
            <p>GPIO 18 SDA / GPIO 19 SCL</p>
            <div className="live-box">
              <table className="mini">
                <thead>
                  <tr>
                    <th>PIN</th>
                    <th>MODE</th>
                    <th>STATE</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>4</td>
                    <td>OUT</td>
                    <td>LOW</td>
                  </tr>
                  <tr>
                    <td>5</td>
                    <td>OUT</td>
                    <td>HIGH</td>
                  </tr>
                  <tr>
                    <td>18</td>
                    <td>I2C</td>
                    <td>SDA</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel dashed">
            <h3>LIVE SENSOR / MQTT DATA</h3>
            <p>Broker: 192.168.4.1</p>
            <p>Topic: sandbox/mqtt/telemetry</p>
            <p>
              Auth: {auth} {auth === 'NONE' ? <span className="warn-tri">▲</span> : null}
            </p>
            <div className="live-box">
              <code>temp=24.1 hum=61 rssi=-47</code>
            </div>
          </section>
        </aside>
      </main>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          ← BACK TO MENU
        </button>
        <span>BUILD MODE — STEP 2 OF 3: APPLY REMEDIATION.</span>
        <span />
      </footer>
    </div>
  )
}
