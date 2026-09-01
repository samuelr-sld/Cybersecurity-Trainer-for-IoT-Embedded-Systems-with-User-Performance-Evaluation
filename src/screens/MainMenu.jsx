import { useEffect, useState } from 'react'
import AppHeader from '../components/AppHeader'

function formatTime(seconds) {
  const h = String(Math.floor(seconds / 3600)).padStart(2, '0')
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')
  const s = String(seconds % 60).padStart(2, '0')
  return `${h}:${m}:${s}`
}

export default function MainMenu({ student, onHack, onBuild, onEval, onFooter, onMenu }) {
  const [elapsed, setElapsed] = useState(14 * 60 + 32)

  useEffect(() => {
    const id = setInterval(() => setElapsed((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="page">
      <AppHeader
        title="IoT CYBERSECURITY SANDBOX — MAIN MENU"
        right={
          <>
            <span className="dot ok" /> PI5 HOST ONLINE <span className="pipe">|</span> MQTT
            BROKER: RUNNING
          </>
        }
        onMenu={onMenu}
      />
      <div className="info-row">
        <div className="info-box">
          <span className="dot ok" /> MODULE DETECTED: <strong>IoT MQTT COMM PANEL</strong> (MAC:
          3C:71:BF:xx)
        </div>
        <div className="info-box">
          STUDENT: <strong>{student.name}</strong> · ID {student.id}
        </div>
        <div className="info-box">
          SESSION TIME: <strong>{formatTime(elapsed)}</strong>
        </div>
      </div>
      <main className="page-body mode-grid">
        <button type="button" className="mode-card" onClick={onHack}>
          <div className="mode-icon">⌨</div>
          <h3>HACK</h3>
          <p>Investigate the deliberately vulnerable device. Perform authorized recon & exploitation.</p>
          <span className="pill">Mode 01</span>
        </button>
        <button type="button" className="mode-card" onClick={onBuild}>
          <div className="mode-icon">{'</>'}</div>
          <h3>BUILD</h3>
          <p>Edit, compile, and flash firmware to fix the identified vulnerability.</p>
          <span className="pill">Mode 02</span>
        </button>
        <button type="button" className="mode-card dashed" onClick={onEval}>
          <div className="mode-icon">◉</div>
          <h3>EVALUATE STUDENT</h3>
          <p>View dashboard of hacking & remediation performance metrics.</p>
          <span className="pill">Instructor / Review</span>
        </button>
      </main>
      <div className="stat-row">
        <div className="stat-box">
          <strong>3 / 5</strong>
          <span>MODULES COMPLETED</span>
        </div>
        <div className="stat-box">
          <strong>Weak MQTT Auth</strong>
          <span>CURRENT SCENARIO</span>
        </div>
        <div className="stat-box">
          <strong>Ready</strong>
          <span>FIRMWARE STATUS</span>
        </div>
        <div className="stat-box">
          <strong>Isolated</strong>
          <span>NETWORK MODE</span>
        </div>
      </div>
      <footer className="link-footer">
        <button type="button" onClick={() => onFooter('modules')}>
          [ Module Selector ]
        </button>
        <button type="button" onClick={() => onFooter('guide')}>
          [ Activity Guide ]
        </button>
        <button type="button" onClick={() => onFooter('settings')}>
          [ Settings ]
        </button>
        <button type="button" onClick={() => onFooter('power')}>
          [ Power ]
        </button>
      </footer>
    </div>
  )
}
