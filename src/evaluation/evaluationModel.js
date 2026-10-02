/**
 * Pure presentation helpers for the Evaluation page (src/screens/Dashboard.jsx).
 *
 * Every value shown comes from `GET /api/evaluation/{id}`
 * (backend/app/evaluation.py), which returns each metric exactly as the
 * backend's metric functions computed it: `{status, value, detail, unit}`.
 * This module only formats. It never computes a metric, never fills a gap,
 * and never renders an unavailable metric as 0 — `not_applicable` and
 * `not_yet_computable` stay visibly distinct from a real number.
 */

export const HACK_METRICS = ['ACR', 'RE', 'EAC', 'TTE']
export const BUILD_METRICS = ['TTR', 'AID', 'DEI']

/** The definitions the backend metric modules implement (manuscript §3.10.1). */
export const METRIC_DEFINITIONS = {
  ACR: 'Declared objectives completed in the session ÷ total declared objectives × 100%',
  RE: 'Recognized reconnaissance commands with all fields correct ÷ recognized reconnaissance commands × 100%',
  EAC: 'Hack Mode sessions attempted at this activity up to and including the first that reached successful exploitation',
  TTE: 'Successful exploitation timestamp − Hack Mode session start',
  TTR: 'Validated successful fix timestamp − Build Mode session start',
  AID: '(Compile + flash + validation attempts) ÷ session duration in minutes',
  DEI: 'Average duration of resolved debugging segments (failed attempt → next success of the same type)',
}

export const SESSION_STATUS_LABEL = {
  completed: 'Completed',
  incomplete: 'Incomplete',
  in_progress: 'In progress',
  interrupted: 'Interrupted',
}

/**
 * The metric names the Evaluation page shows, held in one place. These follow
 * the project's final manuscript terminology as specified for the UI. They are
 * LABELS only: the value, unit and reasoning under each one still come from the
 * backend untouched. (backend/app/evaluation.py `METRIC_INFO` spells EAC, TTE,
 * TTR, AID and DEI differently; that is documented, not edited, here.)
 */
export const METRIC_NAMES = {
  ACR: 'Attack Completion Rate',
  RE: 'Reconnaissance Efficiency',
  EAC: 'Exploit Action Completeness',
  TTE: 'Time to Exploit',
  TTR: 'Time to Remediation',
  AID: 'Attempt-to-Iteration Depth',
  DEI: 'Development Efficiency Index',
}

/**
 * The status chip colour for each session status. Colour is never the only
 * cue: the chip always carries the status word from SESSION_STATUS_LABEL.
 * An interrupted session reads as a warning (the backend stopped it), not as a
 * failure by the student.
 */
export const SESSION_STATUS_TONE = {
  completed: 'is-success',
  incomplete: 'is-danger',
  in_progress: 'is-warning',
  interrupted: 'is-warning',
  not_started: 'is-muted',
}

export const METRIC_STATUS_LABEL = {
  not_applicable: 'N/A',
  not_yet_computable: 'Pending',
}

/** Seconds -> `m:ss` or `h:mm:ss`. Null/undefined stays null. */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

/** ISO timestamp -> local date/time text, or an em dash when absent. */
export function formatTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

/** ISO timestamp -> `{ date, time }` in local format, or null when absent. */
export function splitTimestamp(iso) {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return { date: date.toLocaleDateString(), time: date.toLocaleTimeString() }
}

/**
 * One metric's display text. Only a `computed` metric shows a number; any
 * other status shows its label, and a missing metric shows "No data".
 */
export function formatMetric(metric) {
  if (!metric) return 'No data'
  if (metric.status !== 'computed' || metric.value === null || metric.value === undefined) {
    return METRIC_STATUS_LABEL[metric.status] || 'No data'
  }
  const { value, unit } = metric
  if (unit === '%') return `${Number(value).toFixed(1)}%`
  if (unit === 's') return formatDuration(value)
  if (unit === 'attempts/min') return `${Number(value).toFixed(2)}/min`
  return String(value)
}

export function isMetricAvailable(metric) {
  return Boolean(metric) && metric.status === 'computed' && metric.value !== null
}

/**
 * How one metric tile shows its value: `{ text, unit, tone }`. `tone` is
 * 'value' for a computed number, 'pending' for a metric that cannot be
 * computed yet, and 'na' for one that does not apply (or has no data). The
 * number is exactly what the backend computed, in the unit the backend gave —
 * only the presentation (big figure, small unit) is split out here.
 */
export function metricDisplay(metric) {
  if (!isMetricAvailable(metric)) {
    return { text: formatMetric(metric), unit: null, tone: metric?.status === 'not_yet_computable' ? 'pending' : 'na' }
  }
  const { value, unit } = metric
  if (unit === '%') return { text: `${Number(value).toFixed(1)}%`, unit: null, tone: 'value' }
  if (unit === 's') {
    const text = formatDuration(value)
    return { text, unit: text.split(':').length === 3 ? 'h:mm:ss' : 'm:ss', tone: 'value' }
  }
  if (unit === 'attempts/min') return { text: Number(value).toFixed(2), unit: 'attempts/min', tone: 'value' }
  if (unit === 'sessions') return { text: String(value), unit: value === 1 ? 'session' : 'sessions', tone: 'value' }
  return { text: String(value), unit: unit || null, tone: 'value' }
}

// "no sessions recorded" -> "No sessions recorded."
function sentence(text) {
  const trimmed = String(text || '').trim()
  if (!trimmed) return ''
  const cased = trimmed[0].toUpperCase() + trimmed.slice(1)
  return /[.!?]$/.test(cased) ? cased : `${cased}.`
}

/**
 * The one-line explanation under a metric figure: the backend's own `detail`,
 * lightly formatted, prefixed to say why there is no number when there is none.
 * Empty when the backend gave no detail (a computed metric with nothing to add).
 */
export function metricNote(metric) {
  if (!metric) return 'No data was returned for this metric.'
  const detail = sentence(metric.detail)
  if (isMetricAvailable(metric)) return detail
  if (metric.status === 'not_yet_computable') return `Not yet computable. ${detail}`.trim()
  return `Not evaluated. ${detail}`.trim()
}

/** "succeeded/total" for one attempt bucket, or an em dash when there is none. */
export function attemptText(bucket) {
  return bucket ? `${bucket.succeeded}/${bucket.total}` : '—'
}

/** Tooltip text: the backend's own reason plus the metric's definition. */
export function metricTooltip(code, metric) {
  const parts = [METRIC_DEFINITIONS[code]]
  if (metric?.detail) parts.push(metric.detail)
  return parts.filter(Boolean).join(' — ')
}

/**
 * Overall state of one mode's section, in the page's own vocabulary:
 * 'not_started' when there are no sessions, otherwise the most recent
 * session's own status. Sessions arrive newest first from the backend.
 */
export function sectionState(sessions) {
  if (!Array.isArray(sessions) || sessions.length === 0) return 'not_started'
  return sessions[0].status
}

export const SECTION_STATE_LABEL = {
  not_started: 'Not started',
  ...SESSION_STATUS_LABEL,
}

/**
 * The metrics shown in the Hack Mode summary row: ACR/RE/TTE of the most
 * recent session, and EAC of that session's activity. Null when there is no
 * session, so the page can show an empty state instead of blanks.
 */
export function hackSummary(report) {
  const latest = report?.hack?.sessions?.[0]
  if (!latest) return null
  const activity = (report.hack.activities || []).find((a) => a.scenario_id === latest.scenario_id)
  return {
    session: latest,
    metrics: {
      ACR: latest.metrics.ACR,
      RE: latest.metrics.RE,
      EAC: activity?.metrics?.EAC ?? null,
      TTE: latest.metrics.TTE,
    },
  }
}

/** The Build Mode summary row: TTR/AID/DEI of the most recent session. */
export function buildSummary(report) {
  const latest = report?.build?.sessions?.[0]
  if (!latest) return null
  return { session: latest, metrics: latest.metrics }
}

/** Panel names seen across all of a participant's sessions, de-duplicated. */
export function panelsOf(report) {
  const names = [
    ...(report?.hack?.sessions || []),
    ...(report?.build?.sessions || []),
  ].map((s) => s.panel_name || s.panel_id || s.scenario_id)
  return [...new Set(names.filter(Boolean))]
}

/** True when the participant has no recorded Hack or Build session at all. */
export function hasNoSessions(report) {
  return !report?.hack?.sessions?.length && !report?.build?.sessions?.length
}

/**
 * The most recently STARTED session across both modes, as `{ mode, session }`
 * with `mode` 'hack' or 'build', or null when nothing has been recorded. Each
 * mode's list is newest-first, so only the two heads need comparing.
 */
export function latestSession(report) {
  const hack = report?.hack?.sessions?.[0]
  const build = report?.build?.sessions?.[0]
  if (!hack && !build) return null
  if (!build) return { mode: 'hack', session: hack }
  if (!hack) return { mode: 'build', session: build }
  return new Date(build.started_at).getTime() > new Date(hack.started_at).getTime()
    ? { mode: 'build', session: build }
    : { mode: 'hack', session: hack }
}
