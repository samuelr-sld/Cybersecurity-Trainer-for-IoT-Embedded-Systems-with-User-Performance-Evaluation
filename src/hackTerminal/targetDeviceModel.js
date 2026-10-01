// Which shape of scenario `state` snapshot the TARGET DEVICE panel was handed.
//
// The backend sends a scenario's own `snapshot()` verbatim (backend/app/
// scenarios/base.py), so the shape is the scenario's. The two activity
// scenarios share a skeleton - `target`, `discovery`, `attack` - and the panel
// reads it directly. A FOUNDATION scenario (a panel whose package defines no
// activity yet, e.g. the Environmental Monitoring System) has none of those
// keys, and says so with `foundation: true` and an empty `objectives` list.
//
// `state` is the one frame the backend sends unconditionally, on every session
// resume (backend/app/websocket.py `_replay_session`), so a panel that read
// `snapshot.target.device_status` without checking would throw on the first page
// reload of a foundation panel's session and blank the whole screen. Deciding
// the shape here, once, is what keeps that from being possible.
//
// The test is "does it carry the activity skeleton", not "is it flagged as a
// foundation": a snapshot nobody anticipated degrades to the neutral display
// instead of a TypeError.

export function hasActivityTarget(snapshot) {
  return Boolean(snapshot && snapshot.target && snapshot.discovery)
}

// What a snapshot with no activity target says about itself. Backend-owned
// text when it sent some, otherwise the generic fact. Never an objective.
export function foundationSummary(snapshot) {
  const summary = snapshot && snapshot.summary
  return typeof summary === 'string' && summary.trim()
    ? summary
    : 'No training activity is defined for this panel yet.'
}
