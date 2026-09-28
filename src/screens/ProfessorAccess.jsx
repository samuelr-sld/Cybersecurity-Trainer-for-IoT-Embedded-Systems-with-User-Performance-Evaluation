import { useEffect, useMemo, useState } from 'react'
import AppHeader from '../components/AppHeader'
import InfoBanner from '../components/InfoBanner'
import { listParticipants } from '../api/trainerApi'
import { formatTimestamp } from '../evaluation/evaluationModel'

const PAGE_SIZE = 5

// Registered participants from the backend (GET /api/participants), with
// their recorded session counts — not seeded demo data.
function toRow(p) {
  return {
    id: p.participant_id,
    name: p.full_name,
    hackSessions: p.hack_session_count,
    buildSessions: p.build_session_count,
    lastActivity: p.last_activity,
  }
}

export default function ProfessorAccess({
  signedIn,
  onBack,
  onSignIn,
  onView,
  error,
  onMenu,
}) {
  const [id, setId] = useState('')
  const [password, setPassword] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [remember, setRemember] = useState(false)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [students, setStudents] = useState([])
  const [listState, setListState] = useState({ loading: true, error: '' })

  useEffect(() => {
    let active = true
    listParticipants()
      .then((body) => {
        if (!active) return
        setStudents(body.participants.map(toRow))
        setListState({ loading: false, error: '' })
      })
      .catch((e) => active && setListState({ loading: false, error: e.message }))
    return () => {
      active = false
    }
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return students
    return students.filter(
      (s) => s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
    )
  }, [query, students])

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const current = Math.min(page, pages)
  const slice = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE)
  const start = filtered.length === 0 ? 0 : (current - 1) * PAGE_SIZE + 1
  const end = Math.min(current * PAGE_SIZE, filtered.length)

  return (
    <div className="page">
      <AppHeader
        title="IoT CYBERSECURITY SANDBOX"
        right={
          <>
            <span className="dot ok" /> PI5 HOST ONLINE <span className="pipe">|</span> MQTT
            BROKER: RUNNING
          </>
        }
        onMenu={onMenu}
      />
      <div className="subnav">
        <button type="button" className="text-link" onClick={onBack}>
          ← BACK TO ROLE SELECTION
        </button>
        <h2>PROFESSOR ACCESS</h2>
        <span />
      </div>
      <main className="page-body prof-layout">
        <form
          className="auth-card prof-signin"
          onSubmit={(e) => {
            e.preventDefault()
            onSignIn({ id, password, remember })
          }}
        >
          <h3>PROFESSOR SIGN IN</h3>
          <label>
            PROFESSOR ID
            <input value={id} onChange={(e) => setId(e.target.value)} placeholder="Enter professor ID" />
          </label>
          <label>
            PASSWORD
            <div className="pass-row">
              <input
                type={showPass ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
              />
              <button type="button" className="icon-btn" onClick={() => setShowPass((v) => !v)}>
                {showPass ? 'Hide' : 'Show'}
              </button>
            </div>
          </label>
          <label className="check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Remember me
          </label>
          {error ? <p className="form-error">{error}</p> : null}
          <button type="submit" className="btn-solid">
            {signedIn ? 'SIGNED IN' : 'SIGN IN'}
          </button>
          <p className="hint">
            Prototype: any Professor ID and password are accepted. Session resets on refresh.
          </p>
        </form>

        <section className="eval-list">
          <div className="eval-head">
            <h3>STUDENT EVALUATION LIST</h3>
            <input
              className="search"
              value={query}
              placeholder="Search student..."
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(1)
              }}
            />
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>STUDENT NAME</th>
                  <th>STUDENT ID</th>
                  <th>HACK SESSIONS</th>
                  <th>BUILD SESSIONS</th>
                  <th>LAST ACTIVITY</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {slice.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      {listState.loading
                        ? 'Loading registered students…'
                        : listState.error || 'No registered students yet.'}
                    </td>
                  </tr>
                ) : null}
                {slice.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td>{s.id}</td>
                    <td>{s.hackSessions}</td>
                    <td>{s.buildSessions}</td>
                    <td>{s.lastActivity ? formatTimestamp(s.lastActivity) : 'Not started'}</td>
                    <td>
                      <button type="button" className="btn-outline sm" onClick={() => onView(s, filtered)}>
                        VIEW
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <div className="pager-btns">
              <button type="button" disabled={current === 1} onClick={() => setPage(1)}>
                First
              </button>
              <button type="button" disabled={current === 1} onClick={() => setPage(current - 1)}>
                Prev
              </button>
              {Array.from({ length: Math.min(3, pages) }, (_, i) => {
                const n = Math.min(current, pages - 2) > 1 ? Math.min(current, pages - 2) + i : i + 1
                if (n > pages) return null
                return (
                  <button
                    type="button"
                    key={n}
                    className={n === current ? 'active' : ''}
                    onClick={() => setPage(n)}
                  >
                    {n}
                  </button>
                )
              })}
              <span>…</span>
              <button type="button" onClick={() => setPage(pages)}>
                {pages}
              </button>
              <button type="button" disabled={current === pages} onClick={() => setPage(current + 1)}>
                Next
              </button>
              <button type="button" disabled={current === pages} onClick={() => setPage(pages)}>
                Last
              </button>
            </div>
            <p>
              Showing {start} to {end} of {filtered.length} students.
            </p>
          </div>
        </section>
      </main>
      <InfoBanner>Select a student and click VIEW to open their performance dashboard.</InfoBanner>
    </div>
  )
}
