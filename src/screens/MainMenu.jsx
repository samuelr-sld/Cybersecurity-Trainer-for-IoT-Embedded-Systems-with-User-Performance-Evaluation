import { useEffect, useState } from 'react'
import FullscreenToggle from '../components/FullscreenToggle'
import Icon from '../components/Icon'
import Logo from '../components/Logo'
import { APP_VERSION } from '../appInfo'
import { fetchEvaluation } from '../api/trainerApi'
import useBackendStatus, { BACKEND_STATUS } from '../hooks/useBackendStatus'
import useHardwareStatus from '../hooks/useHardwareStatus'
import { menuHardwareView } from '../hardware/menuHardwareModel'
import {
  SESSION_STATUS_LABEL,
  SESSION_STATUS_TONE,
  formatTimestamp,
  latestSession,
} from '../evaluation/evaluationModel'

const MODE_NAME = { hack: 'Hack Mode', build: 'Build Mode' }

// The "pick up where you left off" strip. Everything in it is the student's own
// last recorded session (GET /api/evaluation/{id}); with none recorded, or if
// the backend cannot say, it is a plain pointer to the first step instead.
// Opening a mode always goes through session preparation, so this never claims
// to resume a running session.
function ContinueStrip({ last, onHack, onBuild }) {
  if (!last) {
    return (
      <section className="continue-strip" aria-label="Get started">
        <span className="continue-icon" aria-hidden="true">
          <Icon name="play" size={22} />
        </span>
        <div className="continue-text">
          <p className="eyebrow">Get started</p>
          <p className="continue-title">
            Hack Mode <span>first, then rebuild the panel in Build Mode</span>
          </p>
        </div>
        <button type="button" className="btn btn-primary btn-pill" onClick={onHack}>
          Open Hack Mode
          <Icon name="arrow-right" size={16} />
        </button>
      </section>
    )
  }
  const { mode, session } = last
  const where = session.panel_name || session.panel_id || session.scenario_id
  return (
    <section className="continue-strip" aria-label="Last session">
      <span className="continue-icon" aria-hidden="true">
        <Icon name="play" size={22} />
      </span>
      <div className="continue-text">
        <p className="eyebrow">Your last session</p>
        <p className="continue-title">
          {MODE_NAME[mode]}{' '}
          <span>
            {where ? `${where} · ` : ''}
            {formatTimestamp(session.started_at)}
          </span>
        </p>
      </div>
      <span className={`chip ${SESSION_STATUS_TONE[session.status] || 'is-muted'}`}>
        {SESSION_STATUS_LABEL[session.status] || session.status}
      </span>
      <button type="button" className="btn btn-primary btn-pill" onClick={mode === 'hack' ? onHack : onBuild}>
        Open {MODE_NAME[mode]}
        <Icon name="arrow-right" size={16} />
      </button>
    </section>
  )
}

// One `LABEL: ● value` readout in the head's status column. The text is always
// the status; the dot only reinforces it (status is never colour alone).
function MenuStat({ label, field }) {
  return (
    <span className="stat">
      {label}: <span className={`dot ${field.tone}`} aria-hidden="true" />
      <span className={`stat-value${field.tone === 'is-ok' ? ' stat-ok' : ''}`}>{field.label}</span>
    </span>
  )
}

function ModeTile({ icon, title, text, tags, onClick }) {
  return (
    <button type="button" className="tile" onClick={onClick}>
      <Icon name={icon} size={84} />
      <h2>{title}</h2>
      <p>{text}</p>
      <span className="tags">
        {tags.map((tag) => (
          <span className="tag" key={tag}>
            {tag}
          </span>
        ))}
      </span>
    </button>
  )
}

export default function MainMenu({ student, onHack, onBuild, onEval, onMenu }) {
  const hub = useBackendStatus()
  // Read-only: the backend's shared device state, polled only while this menu is
  // on screen. The panel is the backend's MAC -> registry verdict, not the port.
  const { report, unreachable } = useHardwareStatus()
  const hardware = menuHardwareView(report, unreachable)
  const [last, setLast] = useState(null)

  useEffect(() => {
    let active = true
    fetchEvaluation(student.id)
      .then((report) => active && setLast(latestSession(report)))
      .catch(() => active && setLast(null))
    return () => {
      active = false
    }
  }, [student.id])

  const hubLabel = hub === BACKEND_STATUS.ONLINE ? 'ONLINE' : hub === BACKEND_STATUS.OFFLINE ? 'UNREACHABLE' : 'CHECKING'

  return (
    <div className="screen screen-dotted menu-screen">
      <button type="button" className="icon-btn menu-burger" aria-label="Open menu" onClick={onMenu}>
        <Icon name="menu" size={32} />
      </button>
      <FullscreenToggle className="is-corner" />

      <main className="menu-panel">
        <header className="menu-head">
          <div className="menu-welcome">
            <h1>
              Welcome Back, <b>{student.name}</b>!
            </h1>
            <p>
              <b>STUDENT NO.</b>: <span className="mono">{student.id}</span>
            </p>
          </div>
          <Logo width={62} />
          <div className="menu-status">
            <MenuStat label="PANEL" field={hardware.panel} />
            <MenuStat label="USB" field={hardware.usb} />
            <span className="stat">
              HUB:{' '}
              <span className={`dot ${hub === BACKEND_STATUS.ONLINE ? 'is-ok' : hub === BACKEND_STATUS.OFFLINE ? 'is-bad' : ''}`} aria-hidden="true" />
              <span className={hub === BACKEND_STATUS.ONLINE ? 'stat-ok' : undefined}>{hubLabel}</span>
            </span>
          </div>
        </header>

        <ContinueStrip last={last} onHack={onHack} onBuild={onBuild} />

        <div className="tile-grid">
          <ModeTile
            icon="tile-hack"
            title="Hack Mode"
            text="Investigate vulnerable embedded IoT systems, analyze their weaknesses, and perform authorized attacks using real security tools and techniques."
            tags={['esptool', 'strings', 'MQTT']}
            onClick={onHack}
          />
          <ModeTile
            icon="tile-build"
            title="Build Mode"
            text="Secure and rebuild vulnerable IoT systems by modifying firmware, applying security fixes, and deploying your improved solution to the connected panel."
            tags={['firmware', 'blockly', 'flash']}
            onClick={onBuild}
          />
          <ModeTile
            icon="tile-progress"
            title="Progress"
            text="Track your activities, completed tasks, and performance throughout your cybersecurity training sessions."
            tags={['metrics', 'sessions', 'report']}
            onClick={onEval}
          />
        </div>

        <p className="menu-note">
          Your panel is detected, and reset to its vulnerable baseline firmware, each time you open Hack Mode or Build Mode.
        </p>
      </main>

      <footer className="menu-footer">
        <span className="version">V {APP_VERSION}</span>
      </footer>
    </div>
  )
}
