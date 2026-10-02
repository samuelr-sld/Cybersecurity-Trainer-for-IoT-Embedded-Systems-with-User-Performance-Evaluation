import { useEffect, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import Icon from '../components/Icon'
import StatusBar from '../components/StatusBar'
import usePreparationSocket from '../hooks/usePreparationSocket'
import {
  OUTCOME,
  PREPARATION_STEPS,
  STEP_STATUS,
  applyPreparationFrame,
  canEnterMode,
  connectionLost,
  initialPreparation,
  stepLabel,
} from '../preparation/preparationModel'

// How long the completed checklist stays on screen before the mode opens —
// presentation only; every checkmark was already confirmed by the backend.
const READY_HOLD_MS = 700

const MODE_NAMES = { hack: 'Hack Mode', build: 'Build Mode' }

const MARKS = {
  [STEP_STATUS.PENDING]: 'status-pending',
  [STEP_STATUS.RUNNING]: 'status-partial',
  [STEP_STATUS.SUCCEEDED]: 'status-done',
  [STEP_STATUS.FAILED]: 'status-failed',
}

/**
 * The screen between tapping HACK/BUILD and the mode itself.
 *
 * Restores the attached panel's vulnerable baseline firmware through
 * backend/app/mode_preparation.py and renders each real stage as it
 * completes. The requested mode is never mounted underneath: App.jsx only
 * switches to it when `onReady` fires, and that only happens once
 * `canEnterMode` holds. A failure keeps the student here with RETRY / BACK.
 */
export default function ModePreparation({ mode, onReady, onRetry, onBack, onMenu }) {
  const [prep, setPrep] = useState(() => initialPreparation(mode))

  usePreparationSocket(mode, {
    onFrame: (frame) => setPrep((current) => applyPreparationFrame(current, frame)),
    onClose: () => setPrep((current) => connectionLost(current)),
  })

  // Read through a ref so a parent re-render (a new inline callback) does not
  // restart the hold timer.
  const onReadyRef = useRef(onReady)
  useEffect(() => {
    onReadyRef.current = onReady
  })

  const ready = canEnterMode(prep)
  useEffect(() => {
    if (!ready) return undefined
    const id = setTimeout(() => onReadyRef.current(), READY_HOLD_MS)
    return () => clearTimeout(id)
  }, [ready])

  const failed = prep.outcome === OUTCOME.FAILED
  const panel = prep.facts.panel_name
  const port = prep.facts.port
  const modeName = MODE_NAMES[mode] || 'the mode'

  return (
    <div className="screen screen-fixed screen-dotted">
      <AppHeader title={MODE_NAMES[mode] || 'Mode'} onMenu={onMenu} />
      <main className="prep-body">
        <section className="prep-card" aria-live="polite">
          <h2>Preparing training session</h2>
          <p className="prep-sub">
            Restoring the panel&apos;s vulnerable baseline firmware before {modeName} opens. Every session starts from
            this baseline.
          </p>
          <ol className="prep-steps">
            {PREPARATION_STEPS.map((step) => {
              const status = prep.steps[step.id]
              return (
                <li key={step.id} className={`prep-step is-${status}`}>
                  <span className="prep-mark">
                    <Icon name={MARKS[status]} size={18} />
                  </span>
                  <span>{stepLabel(step, status)}</span>
                </li>
              )
            })}
          </ol>

          {ready ? (
            <p className="prep-ready">
              <Icon name="status-done" size={16} />
              READY — opening {modeName}…
            </p>
          ) : null}

          {failed ? (
            <div className="prep-failure" role="alert">
              <p className="prep-failure-title">Unable to prepare the ESP32.</p>
              <p>{prep.message}</p>
              {prep.detail ? <pre className="prep-detail">{prep.detail}</pre> : null}
              <div className="prep-actions">
                <button type="button" className="btn btn-primary" onClick={onRetry}>
                  Retry
                </button>
                <button type="button" className="btn" onClick={onBack}>
                  Back to menu
                </button>
              </div>
            </div>
          ) : (
            <div className="prep-actions">
              <button type="button" className="text-link" onClick={onBack}>
                <Icon name="arrow-left" size={14} />
                Cancel
              </button>
            </div>
          )}
        </section>
      </main>
      <StatusBar>
        <span>
          PANEL: <span className={panel ? undefined : 'muted'}>{panel || '—'}</span>
        </span>
        <span className="hw-sep" aria-hidden="true">
          |
        </span>
        <span>
          USB: <span className={port ? undefined : 'muted'}>{port || '—'}</span>
        </span>
      </StatusBar>
    </div>
  )
}
