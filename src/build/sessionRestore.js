/**
 * What a Build Mode `session` frame says about the work already done.
 *
 * A new session sends no history and ~0 elapsed seconds; a resumed one sends
 * the backend's own event log and the server-measured age of the session, so
 * the Activity Log and its clock continue instead of restarting at page load.
 * Pure: the caller supplies "now" and does the React state updates.
 */

export function formatElapsed(totalSeconds) {
  const total = Math.max(0, Math.floor(totalSeconds))
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`
}

const OP_STARTED = {
  compile_started: 'compile',
  flash_started: 'flash',
  validation_started: 'validation',
}

export function restoreFromSessionFrame(message, nowMs) {
  const history = Array.isArray(message?.history) ? message.history : []
  const elapsed = Number.isFinite(message?.elapsed_seconds) ? message.elapsed_seconds : 0
  let activeOp = null
  for (const item of history) activeOp = OP_STARTED[item.event] ?? activeOp
  return {
    events: history.map((item) => ({
      event: item.event,
      data: item.data ?? {},
      at: formatElapsed(item.elapsed_seconds ?? 0),
    })),
    startMs: nowMs - elapsed * 1000,
    activeOp,
  }
}
