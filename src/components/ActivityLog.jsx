import { useEffect, useRef } from 'react'

// Log copy. A plain lookup, not a switch, so an event type this map doesn't
// know about (the closed set changed) falls back to the raw value below
// rather than silently dropping the row — the same pattern HackMode.jsx
// uses for ScenarioEventType. The session/edit five (Phase 3A), the three
// compile events (Phase 3B) and the three flash events (Phase 3C) are all
// really emitted; the rest are reserved vocabulary for later phases
// (validation, security/functional testing).
const EVENT_LABELS = {
  build_session_started: 'Build session started',
  workspace_loaded: 'Workspace loaded',
  security_region_edited: 'Security region edited',
  code_edited: 'Code edited',
  build_session_ended: 'Build session ended',
  compile_started: 'Compile started',
  compile_failed: 'Compile failed',
  compile_succeeded: 'Compile succeeded',
  flash_started: 'Flash started',
  flash_failed: 'Flash failed',
  flash_succeeded: 'Flash succeeded',
  validation_started: 'Validation started',
  validation_failed: 'Validation failed',
  validation_succeeded: 'Validation succeeded',
  security_test_started: 'Security test started',
  security_test_failed: 'Security test failed',
  security_test_succeeded: 'Security test succeeded',
  functional_test_started: 'Functional test started',
  functional_test_failed: 'Functional test failed',
  functional_test_succeeded: 'Functional test succeeded',
  build_completed: 'Build completed',
}

// "Near enough to the bottom that a new entry should still auto-scroll into
// view."
const AUTOSCROLL_THRESHOLD_PX = 32

/**
 * The body of Build Mode's "Activity log" side panel: the session's recorded
 * events, each with its session-elapsed HH:MM:SS (not a time of day). `events`
 * is BuildMode's own state, fed by the backend's `event` frames and replayed
 * from the `session` frame on a resume — this component only draws it.
 *
 * Smart auto-scroll: a new entry scrolls into view only if the reader was
 * already at/near the bottom. `nearBottomRef` is kept current by the list's own
 * onScroll handler (so it reflects where the reader actually is right before a
 * new entry lands) and read — not set — by the effect, which also runs when the
 * panel mounts, so it opens on the newest entry rather than the first.
 */
export default function ActivityLog({ events }) {
  const scrollRef = useRef(null)
  const nearBottomRef = useRef(true)

  function onScroll(event) {
    const el = event.currentTarget
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < AUTOSCROLL_THRESHOLD_PX
  }

  useEffect(() => {
    const el = scrollRef.current
    if (el && nearBottomRef.current) el.scrollTop = el.scrollHeight
  }, [events])

  return (
    <div className="side-body activity-body">
      <div className="activity-log-scroll" ref={scrollRef} onScroll={onScroll}>
        <ul className="activity-log">
          {events.length === 0 ? (
            <li className="empty-line">No activity yet.</li>
          ) : (
            events.map((entry, i) => (
              <li key={i}>
                <span className="log-time">{entry.at}</span>
                <span>{EVENT_LABELS[entry.event] || entry.event}</span>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  )
}
