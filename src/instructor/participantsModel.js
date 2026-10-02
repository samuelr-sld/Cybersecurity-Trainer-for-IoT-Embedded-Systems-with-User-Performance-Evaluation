/**
 * Pure helpers for the instructor's student list (src/screens/InstructorStudents.jsx).
 *
 * Everything here is derived from `GET /api/participants`
 * (backend/app/evaluation_api.py), which returns each registered participant
 * with their Hack and Build session COUNTS and last-activity time. Nothing is
 * invented: the list carries no per-student completion status, so none is
 * shown — a student either has recorded sessions or has not started.
 */

const DAY_MS = 24 * 60 * 60 * 1000

/** One API participant -> the row the list renders. */
export function toRow(participant) {
  return {
    id: participant.participant_id,
    name: participant.full_name,
    hackSessions: participant.hack_session_count,
    buildSessions: participant.build_session_count,
    lastActivity: participant.last_activity,
  }
}

/** "Samuel Rick Salud" -> "SS" (first and last word). */
export function initials(name) {
  const words = String(name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return `${first}${last}`.toUpperCase()
}

export function hasSessions(row) {
  return row.hackSessions + row.buildSessions > 0
}

/**
 * The four summary figures above the list. "Active" means a recorded session
 * in the last 24 hours — the only activity signal the API carries.
 */
export function summarize(rows, now = Date.now()) {
  return {
    registered: rows.length,
    activeToday: rows.filter((row) => {
      const at = row.lastActivity ? new Date(row.lastActivity).getTime() : NaN
      return Number.isFinite(at) && now - at <= DAY_MS
    }).length,
    withHack: rows.filter((row) => row.hackSessions > 0).length,
    withBuild: rows.filter((row) => row.buildSessions > 0).length,
  }
}

export const LIST_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'With sessions' },
  { id: 'not_started', label: 'Not started' },
]

export function matchesFilter(row, filter) {
  if (filter === 'active') return hasSessions(row)
  if (filter === 'not_started') return !hasSessions(row)
  return true
}

/** Rows matching the search text (name or number) and the chosen filter. */
export function filterRows(rows, { query = '', filter = 'all' } = {}) {
  const q = query.trim().toLowerCase()
  return rows.filter(
    (row) =>
      matchesFilter(row, filter) &&
      (!q || row.name.toLowerCase().includes(q) || row.id.toLowerCase().includes(q)),
  )
}

/** Count per filter chip, over the unfiltered rows. */
export function filterCounts(rows) {
  return Object.fromEntries(LIST_FILTERS.map((f) => [f.id, rows.filter((row) => matchesFilter(row, f.id)).length]))
}

/** Page `page` (1-based, clamped) of `rows`, with the 1-based range shown. */
export function paginate(rows, page, pageSize) {
  const pages = Math.max(1, Math.ceil(rows.length / pageSize))
  const current = Math.min(Math.max(1, page), pages)
  const startIndex = (current - 1) * pageSize
  return {
    pages,
    current,
    slice: rows.slice(startIndex, startIndex + pageSize),
    start: rows.length === 0 ? 0 : startIndex + 1,
    end: Math.min(startIndex + pageSize, rows.length),
  }
}

/**
 * "Today, 3:03 AM" / "Yesterday, 9:12 PM" / a full local date-time further
 * back; "Not started" when there is no activity at all.
 */
export function formatLastActive(iso, now = new Date()) {
  if (!iso) return 'Not started'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Not started'
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (date.getTime() >= startOfToday) return `Today, ${time}`
  if (date.getTime() >= startOfToday - DAY_MS) return `Yesterday, ${time}`
  return `${date.toLocaleDateString()}, ${time}`
}

// A spreadsheet treats a cell starting with = + - @ as a formula. Student names
// are typed by students, so an exported name must never be able to become one.
function csvCell(value) {
  let text = value === null || value === undefined ? '' : String(value)
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

const CSV_COLUMNS = ['Student', 'Student number', 'Hack sessions', 'Build sessions', 'Last active (ISO)']

/** The student list as CSV text (header row + one row per student). */
export function toCsv(rows) {
  const lines = [CSV_COLUMNS.map(csvCell).join(',')]
  for (const row of rows) {
    lines.push(
      [row.name, row.id, row.hackSessions, row.buildSessions, row.lastActivity ?? ''].map(csvCell).join(','),
    )
  }
  return lines.join('\r\n')
}
