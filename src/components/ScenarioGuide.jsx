import { useEffect, useRef } from 'react'
import Icon from './Icon'
import RichText from './RichText'

/**
 * The Hack Mode scenario guide: what this system is, what the task is and how to
 * work, as the attached panel's package declares it (backend/panels/<id>/
 * panel.json `hack.guide`, delivered on the `session` frame). It is Hack Mode's
 * own content - not Build Mode's remediation brief - and holds no panel-specific
 * text: a foundation panel's guide simply says that no activity is defined.
 *
 * Closes on Escape, on the backdrop and on its own close button; focus starts on
 * the close button.
 */
export default function ScenarioGuide({ briefing, connecting, onClose }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  let body
  if (briefing === null) {
    body = (
      <p className="empty-line">
        {connecting ? 'Loading the scenario…' : 'The scenario is unavailable: the backend is not connected.'}
      </p>
    )
  } else if (briefing.guide.length === 0 && briefing.outcomes.length === 0) {
    body = (
      <p className="empty-line">
        <Icon name="info" size={14} /> No scenario guide is available for this session.
      </p>
    )
  } else {
    body = (
      <>
        {briefing.guide.map((section) => (
          <section key={section.heading} className="guide-section">
            <h3>{section.heading}</h3>
            {section.paragraphs.map((paragraph, index) => (
              <p key={index}>
                <RichText text={paragraph} />
              </p>
            ))}
          </section>
        ))}
        {briefing.outcomes.length > 0 ? (
          <section className="guide-section">
            <h3>What you should take away</h3>
            <ul>
              {briefing.outcomes.map((outcome) => (
                <li key={outcome}>{outcome}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </>
    )
  }

  return (
    <div
      className="overlay is-centered"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="scenario-guide-title">
        <div className="dialog-head">
          <div>
            <p className="eyebrow">Scenario guide</p>
            <h2 id="scenario-guide-title">{briefing?.title || 'Scenario guide'}</h2>
          </div>
          <button ref={closeRef} type="button" className="icon-btn" aria-label="Close scenario guide" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="dialog-body">{body}</div>
      </div>
    </div>
  )
}
