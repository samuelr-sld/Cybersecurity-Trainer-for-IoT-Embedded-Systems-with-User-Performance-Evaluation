/**
 * What survives a page reload: which session this tab is working in.
 *
 * A reload destroys every React component but not the backend's session
 * (backend/app/session_residency.py), so the tab only has to remember two
 * things — the session id per mode, and which mode/student it was in — to
 * come back to the same work. `sessionStorage` is the right store: it is
 * per-tab (two tabs never share or fight over a session), survives a reload,
 * and is gone when the tab closes, at which point the backend's grace period
 * finishes the abandoned session. It is only ever a *pointer*; the backend
 * stays authoritative and is asked whether the session still exists.
 *
 * Every access is guarded: storage can be missing or throw (private windows,
 * blocked site data), and the app must then behave as it did before — a
 * reload starts over.
 */

const SESSION_KEY = (mode) => `trainer.session.${mode}`
const ACTIVE_MODE_KEY = 'trainer.activeMode'

export const RESUMABLE_MODES = ['hack', 'build']

function store() {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

function read(key) {
  try {
    return store()?.getItem(key) ?? null
  } catch {
    return null
  }
}

function write(key, value) {
  try {
    store()?.setItem(key, value)
  } catch {
    /* storage unavailable: a reload simply will not resume */
  }
}

function remove(key) {
  try {
    store()?.removeItem(key)
  } catch {
    /* nothing to clean */
  }
}

export function rememberSession(mode, sessionId) {
  if (RESUMABLE_MODES.includes(mode) && sessionId) write(SESSION_KEY(mode), sessionId)
}

export function recallSession(mode) {
  return RESUMABLE_MODES.includes(mode) ? read(SESSION_KEY(mode)) : null
}

export function forgetSession(mode) {
  remove(SESSION_KEY(mode))
}

/** The mode and student the tab was in, so a reload can reopen that screen. */
export function rememberActiveMode(mode, student) {
  if (!RESUMABLE_MODES.includes(mode) || !student?.id) return
  write(ACTIVE_MODE_KEY, JSON.stringify({ mode, student: { id: student.id, name: student.name } }))
}

export function recallActiveMode() {
  const raw = read(ACTIVE_MODE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (RESUMABLE_MODES.includes(parsed?.mode) && parsed?.student?.id) return parsed
  } catch {
    /* corrupt entry: treat as absent */
  }
  return null
}

export function forgetActiveMode() {
  remove(ACTIVE_MODE_KEY)
}
