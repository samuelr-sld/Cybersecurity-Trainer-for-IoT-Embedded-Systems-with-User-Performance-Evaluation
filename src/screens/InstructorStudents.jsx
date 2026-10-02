import { useEffect, useMemo, useState } from 'react'
import AppHeader from '../components/AppHeader'
import Icon from '../components/Icon'
import StatusBar from '../components/StatusBar'
import { listParticipants } from '../api/trainerApi'
import useBackendStatus, { BACKEND_STATUS } from '../hooks/useBackendStatus'
import {
  LIST_FILTERS,
  filterCounts,
  filterRows,
  formatLastActive,
  initials,
  paginate,
  summarize,
  toCsv,
  toRow,
} from '../instructor/participantsModel'

const PAGE_SIZE = 8

// Registered participants from the backend (GET /api/participants) with their
// recorded session counts — the instructor's whole data source. There is no
// per-student completion status in that payload, so none is shown.
function SessionCount({ count }) {
  return count > 0 ? (
    <span className="chip is-info">
      {count} {count === 1 ? 'session' : 'sessions'}
    </span>
  ) : (
    <span className="chip is-muted">Not started</span>
  )
}

function SummaryCard({ label, value, caption }) {
  return (
    <div className="card summary-card">
      <p className="label-caps">{label}</p>
      <p className="summary-value">{value}</p>
      <p className="summary-caption">{caption}</p>
    </div>
  )
}

export default function InstructorStudents({ professorId, onMenu, onSignOut, onView }) {
  const [students, setStudents] = useState([])
  const [loadedAt, setLoadedAt] = useState(0)
  const [listState, setListState] = useState({ loading: true, error: '' })
  // Bumped by REFRESH so the effect below re-reads the list.
  const [reloadKey, setReloadKey] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(1)
  const hub = useBackendStatus()

  useEffect(() => {
    let active = true
    listParticipants()
      .then((body) => {
        if (!active) return
        setStudents(body.participants.map(toRow))
        setLoadedAt(Date.now())
        setListState({ loading: false, error: '' })
      })
      .catch((e) => active && setListState({ loading: false, error: e.message }))
    return () => {
      active = false
    }
  }, [reloadKey])

  function refresh() {
    setListState((state) => ({ ...state, loading: true, error: '' }))
    setReloadKey((n) => n + 1)
  }

  const summary = useMemo(() => summarize(students, loadedAt), [students, loadedAt])
  const counts = useMemo(() => filterCounts(students), [students])
  const filtered = useMemo(() => filterRows(students, { query, filter }), [students, query, filter])
  const { pages, current, slice, start, end } = paginate(filtered, page, PAGE_SIZE)
  const now = new Date(loadedAt || 0)

  function exportCsv() {
    const blob = new Blob([toCsv(students)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'students.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="screen screen-fixed">
      <AppHeader
        title="Students"
        onMenu={onMenu}
        actions={
          <>
            <button type="button" className="btn" onClick={exportCsv} disabled={students.length === 0}>
              <Icon name="download" size={16} />
              Export CSV
            </button>
            <button type="button" className="btn btn-primary" onClick={onSignOut}>
              Sign out
            </button>
          </>
        }
      />

      <main className="instructor-body">
        <section className="summary-row" aria-label="Summary">
          <SummaryCard label="Registered students" value={summary.registered} caption="in the trainer database" />
          <SummaryCard label="Active today" value={summary.activeToday} caption="last 24 hours" />
          <SummaryCard label="With Hack sessions" value={summary.withHack} caption="students" />
          <SummaryCard label="With Build sessions" value={summary.withBuild} caption="students" />
        </section>

        <section className="card student-list" aria-label="All students">
          <div className="student-list-head">
            <h2 className="list-title">All students</h2>
            <div className="student-list-tools">
              <label className="search-field">
                <span className="sr-only">Search students by name or number</span>
                <Icon name="search" size={18} />
                <input
                  value={query}
                  placeholder="Search name or number"
                  onChange={(e) => {
                    setQuery(e.target.value)
                    setPage(1)
                  }}
                />
              </label>
              <button
                type="button"
                className="icon-btn is-quiet"
                aria-label="Refresh student list"
                onClick={refresh}
                disabled={listState.loading}
              >
                <Icon name="refresh" size={16} />
              </button>
            </div>
          </div>

          <div className="filter-chips" role="group" aria-label="Filter students">
            {LIST_FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                className={`filter-chip${filter === f.id ? ' is-on' : ''}`}
                aria-pressed={filter === f.id}
                onClick={() => {
                  setFilter(f.id)
                  setPage(1)
                }}
              >
                {f.label} <span className="filter-count">{counts[f.id]}</span>
              </button>
            ))}
          </div>

          {listState.error ? (
            <div className="notice is-danger" role="alert">
              <Icon name="warning" size={14} />
              Could not load students: {listState.error}
            </div>
          ) : null}

          <div className="table-scroll student-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Student no.</th>
                  <th>Last active</th>
                  <th>Hack Mode</th>
                  <th>Build Mode</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {slice.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <div className="empty-state">
                        {listState.loading
                          ? 'Loading registered students…'
                          : listState.error
                            ? 'The student list is unavailable.'
                            : students.length === 0
                              ? 'No registered students yet.'
                              : 'No students match your search.'}
                      </div>
                    </td>
                  </tr>
                ) : null}
                {slice.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <span className="student-cell">
                        <span className="avatar" aria-hidden="true">
                          {initials(s.name)}
                        </span>
                        {s.name}
                      </span>
                    </td>
                    <td className="num">{s.id}</td>
                    <td>{formatLastActive(s.lastActivity, now)}</td>
                    <td>
                      <SessionCount count={s.hackSessions} />
                    </td>
                    <td>
                      <SessionCount count={s.buildSessions} />
                    </td>
                    <td className="cell-action">
                      <button type="button" className="btn btn-sm" onClick={() => onView(s, filtered)}>
                        Report
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="student-list-foot">
            <p className="muted">
              {filtered.length === 0
                ? 'Showing 0 students'
                : `Showing ${start} to ${end} of ${filtered.length} students`}
            </p>
            <div className="pager">
              <button type="button" className="btn btn-sm" disabled={current === 1} onClick={() => setPage(current - 1)}>
                Previous
              </button>
              <span className="pager-page">
                Page {current} of {pages}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                disabled={current === pages}
                onClick={() => setPage(current + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </section>
      </main>

      <StatusBar>
        <span className="hw-conn">
          HUB:{' '}
          <span className="hub-state">
            {hub === BACKEND_STATUS.ONLINE ? 'ONLINE' : hub === BACKEND_STATUS.OFFLINE ? 'UNREACHABLE' : 'CHECKING'}
          </span>
          <span
            className={`dot ${hub === BACKEND_STATUS.ONLINE ? 'is-ok' : hub === BACKEND_STATUS.OFFLINE ? 'is-bad' : ''}`}
            aria-hidden="true"
          />
        </span>
        <span className="hw-sep" aria-hidden="true">
          |
        </span>
        <span>
          INSTRUCTOR: <span className="mono">{professorId}</span>
        </span>
      </StatusBar>
    </div>
  )
}
