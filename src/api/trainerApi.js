/**
 * HTTP client for the backend's participant and Evaluation endpoints
 * (backend/app/evaluation_api.py).
 *
 * The base URL follows the WebSocket hooks' convention: `VITE_API_URL`
 * overrides everything, otherwise the page's own protocol/hostname plus the
 * backend's dev port (`VITE_API_PORT`, default 8000).
 */
export function resolveApiUrl() {
  const configured = import.meta.env?.VITE_API_URL
  if (configured) return configured.replace(/\/$/, '')
  const port = import.meta.env?.VITE_API_PORT || '8000'
  return `${window.location.protocol}//${window.location.hostname}:${port}`
}

/**
 * Append the owning participant to a mode WebSocket URL, so the backend
 * attributes the recorded session to them (backend/app/participants.py).
 * With no participant the URL is returned unchanged.
 */
export function withParticipant(url, participantId) {
  if (!participantId) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}participant=${encodeURIComponent(participantId)}`
}

/**
 * Ask a mode WebSocket to re-attach to a session that is still running (the
 * page was reloaded). The backend honours it only for a live session that
 * belongs to the same participant; otherwise it starts a normal new one.
 */
export function withResumeSession(url, sessionId) {
  if (!sessionId) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}session=${encodeURIComponent(sessionId)}`
}

export class ApiError extends Error {
  constructor(message, status, { endpointMissing = false } = {}) {
    super(message)
    this.status = status
    // True when the backend has no such route at all (an older backend
    // process), as opposed to a real 404 from the endpoint, such as an
    // unregistered student number.
    this.endpointMissing = endpointMissing
  }
}

// FastAPI's own detail for an unrouted path. The trainer's endpoints never
// use it: their 404s name what was not found.
const UNROUTED_DETAIL = 'Not Found'

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${resolveApiUrl()}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new ApiError('Backend unreachable. Is the trainer backend running?', 0)
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 404 && body?.detail === UNROUTED_DETAIL) {
      throw new ApiError(
        'The running trainer backend has no participant/evaluation API. It is an outdated process: restart the backend.',
        404,
        { endpointMissing: true },
      )
    }
    const detail = typeof body?.detail === 'string' ? body.detail : `request failed (${response.status})`
    throw new ApiError(detail, response.status)
  }
  return body
}

export function registerParticipant({ id, name }) {
  return request('/api/participants', {
    method: 'POST',
    body: JSON.stringify({ participant_id: id, full_name: name }),
  })
}

export function signInParticipant({ id, name }) {
  return request('/api/participants/sign-in', {
    method: 'POST',
    body: JSON.stringify({ participant_id: id, full_name: name }),
  })
}

export function listParticipants() {
  return request('/api/participants')
}

export function fetchEvaluation(participantId) {
  return request(`/api/evaluation/${encodeURIComponent(participantId)}`)
}

/**
 * The backend's liveness probe (GET /health). True when it answers OK; any
 * network failure or non-OK answer is "not reachable". Read-only: it touches
 * no session, no hardware and no database.
 */
export async function fetchHealth(signal) {
  try {
    const response = await fetch(`${resolveApiUrl()}/health`, { signal, cache: 'no-store' })
    return response.ok
  } catch {
    return false
  }
}

/** Is this Hack/Build session still running on the backend? */
export function fetchSessionLive(mode, sessionId) {
  return request(`/api/sessions/${mode}/${encodeURIComponent(sessionId)}`).then((body) => Boolean(body?.live))
}

/**
 * End a session for good. A WebSocket closing never does this (a reload must
 * be able to come back), so leaving a mode on purpose says so explicitly.
 * Best effort: if the backend is unreachable its grace period ends the
 * session anyway.
 */
export function endSession(mode, sessionId) {
  return request(`/api/sessions/${mode}/${encodeURIComponent(sessionId)}/end`, {
    method: 'POST',
    keepalive: true,
  }).catch(() => null)
}
