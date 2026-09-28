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

export const SESSION_STATUS_CLASS = {
  completed: 'ok',
  incomplete: 'bad',
  in_progress: 'warn',
  interrupted: 'bad',
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
