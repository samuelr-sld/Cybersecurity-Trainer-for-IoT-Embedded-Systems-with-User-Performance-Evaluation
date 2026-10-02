// What the TARGET DEVICE panel shows, read from the scenario's own `state`
// snapshot (backend/app/scenarios/*::snapshot), which the backend sends on the
// `session` frame and after every command that causes an event.
//
// The scenario describes its own target. A scenario with an activity target puts
// a `readout` list in its snapshot - rows of `{id, label, value, revealed}`,
// already gated by what the student has discovered (backend/app/scenarios/
// base.py `readout_row`) - so this page renders rows without knowing whether the
// target is a motor controller, a sensor or anything else, and holds no list of
// which facts to hide. A FOUNDATION scenario (a panel whose package defines no
// activity yet) has no `readout` and says so with `foundation: true`.
//
//   * 'pending' - no snapshot yet (the socket has not delivered the session).
//   * 'rows'    - the scenario's readout rows.
//   * 'none'    - no activity target: the scenario's own summary, or the generic
//                 fact. Never a made-up row.
//
// `state` is also the frame the backend replays on every session resume, so a
// snapshot nobody anticipated degrades to 'none' instead of a TypeError that
// would blank the whole screen on the first reload.

export const TARGET_KIND = {
  PENDING: 'pending',
  ROWS: 'rows',
  NONE: 'none',
}

const NO_ACTIVITY = 'No training activity is defined for this panel yet.'

function readRow(row) {
  if (!row || typeof row !== 'object') return null
  if (typeof row.id !== 'string' || typeof row.label !== 'string') return null
  const revealed = row.revealed === true && row.value !== null && row.value !== undefined
  return { id: row.id, label: row.label, revealed, value: revealed ? String(row.value) : '' }
}

/** @returns {{kind: 'pending'|'rows'|'none', rows: Array, summary: string}} */
export function describeTarget(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') {
    return { kind: TARGET_KIND.PENDING, rows: [], summary: '' }
  }
  if (Array.isArray(snapshot.readout)) {
    const rows = snapshot.readout.map(readRow).filter(Boolean)
    if (rows.length > 0) return { kind: TARGET_KIND.ROWS, rows, summary: '' }
  }
  // Backend-owned text when it sent some, otherwise the generic fact.
  const summary =
    typeof snapshot.summary === 'string' && snapshot.summary.trim() ? snapshot.summary : NO_ACTIVITY
  return { kind: TARGET_KIND.NONE, rows: [], summary }
}
