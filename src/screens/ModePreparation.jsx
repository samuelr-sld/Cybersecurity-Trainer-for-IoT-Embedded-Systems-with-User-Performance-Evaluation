import { useEffect, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
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

const MODE_NAMES = { hack: 'HACK MODE', build: 'BUILD MODE' }

const MARKS = {
  [STEP_STATUS.PENDING]: '○',
  [STEP_STATUS.RUNNING]: '●',
  [STEP_STATUS.SUCCEEDED]: '✓',
  [STEP_STATUS.FAILED]: '✕',
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

  return (
    <div className="page">
      <AppHeader
        title={`${MODE_NAMES[mode] || 'MODE'} — SESSION PREPARATION`}
        right={
          panel ? (
            <>
              PANEL: {panel}
              {port ? <> <span className="pipe">|</span> USB: {port}</> : null}
            </>
          ) : (
            'PANEL: —'
          )
        }
        onMenu={onMenu}
      />
      <main className="page-body prep-body">
        <section className="prep-card" aria-live="polite">
          <h2>PREPARING TRAINING SESSION</h2>
          <p className="prep-sub">
            Restoring the panel&apos;s vulnerable baseline firmware before {MODE_NAMES[mode] || 'the mode'} opens.
          </p>
          <ol className="prep-steps">
            {PREPARATION_STEPS.map((step) => {
              const status = prep.steps[step.id]
              return (
                <li key={step.id} className={`prep-step is-${status}`}>
                  <span className="prep-mark" aria-hidden="true">
                    {MARKS[status]}
                  </span>
                  <span>{stepLabel(step, status)}</span>
                </li>
              )
            })}
          </ol>

          {ready ? <p className="prep-ready">READY — opening {MODE_NAMES[mode]}…</p> : null}

          {failed ? (
            <div className="prep-failure" role="alert">
              <p className="prep-failure-title">Unable to prepare the ESP32.</p>
              <p>{prep.message}</p>
              {prep.detail ? <pre className="prep-detail">{prep.detail}</pre> : null}
              <div className="prep-actions">
                <button type="button" className="btn-solid" onClick={onRetry}>
                  RETRY
                </button>
                <button type="button" className="btn-outline" onClick={onBack}>
                  BACK
                </button>
              </div>
            </div>
          ) : null}
        </section>
      </main>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          {failed ? '← BACK TO MENU' : '← CANCEL'}
        </button>
        <span>EVERY SESSION STARTS FROM THE VULNERABLE BASELINE.</span>
        <span />
      </footer>
    </div>
  )
}
