import { useEffect, useState } from 'react'
import AppHeader from '../components/AppHeader'
import InfoBanner from '../components/InfoBanner'
import { fetchEvaluation } from '../api/trainerApi'
import {
  BUILD_METRICS,
  HACK_METRICS,
  SECTION_STATE_LABEL,
  SESSION_STATUS_CLASS,
  SESSION_STATUS_LABEL,
  buildSummary,
  formatDuration,
  formatMetric,
  formatTimestamp,
  hackSummary,
  hasNoSessions,
  isMetricAvailable,
  metricTooltip,
  panelsOf,
  sectionState,
} from '../evaluation/evaluationModel'

// The Evaluation page. Everything shown is the backend's recorded data and
// the backend's own metric results (GET /api/evaluation/{id},
// backend/app/evaluation.py) — nothing is computed, seeded or filled in here.
// There is deliberately no overall score or grade: the platform defines
// none, and this page reports performance metrics only.

function MetricTile({ code, metric }) {
  const available = isMetricAvailable(metric)
  return (
    <div className={`kpi${available ? '' : ' kpi-empty'}`} title={metricTooltip(code, metric)}>
      <strong>{formatMetric(metric)}</strong>
      <span>
        {code} — {metric?.name?.toUpperCase() ?? 'NO DATA'}
      </span>
      {!available && metric?.detail ? <small className="kpi-detail">{metric.detail}</small> : null}
    </div>
  )
}

function StatusBadge({ status }) {
  return (
    <span className={`badge ${SESSION_STATUS_CLASS[status] || ''}`}>
      {SESSION_STATUS_LABEL[status] || status}
    </span>
  )
}

function MetricCell({ code, metric }) {
  return <td title={metricTooltip(code, metric)}>{formatMetric(metric)}</td>
}

function SectionHead({ title, sessions }) {
  const state = sectionState(sessions)
  return (
    <div className="eval-section-head">
      <h3>{title}</h3>
      <span className={`badge ${SESSION_STATUS_CLASS[state] || ''}`}>{SECTION_STATE_LABEL[state] || state}</span>
    </div>
  )
}

function HackSection({ report }) {
  const sessions = report.hack.sessions
  const summary = hackSummary(report)
  return (
    <section className="panel eval-section">
      <SectionHead title="HACK MODE" sessions={sessions} />
      {!summary ? (
        <p className="eval-empty">No Hack Mode session has been recorded for this student yet.</p>
      ) : (
        <>
          <p className="hint">
            Most recent session ({formatTimestamp(summary.session.started_at)}) —{' '}
            {summary.session.panel_name || summary.session.scenario_id}
          </p>
          <div className="kpi-row eval-kpis">
            {HACK_METRICS.map((code) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} />
            ))}
          </div>
          <div className="table-wrap">
            <table className="log-table">
              <thead>
                <tr>
                  <th>STARTED</th>
                  <th>PANEL</th>
                  <th>STATUS</th>
                  <th>DURATION</th>
                  <th>COMMANDS</th>
                  <th>ACR</th>
                  <th>RE</th>
                  <th>TTE</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} title={`Session ${s.session_id}`}>
                    <td>{formatTimestamp(s.started_at)}</td>
                    <td>{s.panel_name || s.scenario_id}</td>
                    <td>
                      <StatusBadge status={s.status} />
                    </td>
                    <td>{formatDuration(s.duration_seconds) ?? '—'}</td>
                    <td>{s.command_count}</td>
                    <MetricCell code="ACR" metric={s.metrics.ACR} />
                    <MetricCell code="RE" metric={s.metrics.RE} />
                    <MetricCell code="TTE" metric={s.metrics.TTE} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.hack.activities.length > 1 ? (
            <p className="hint">
              EAC per activity:{' '}
              {report.hack.activities
                .map((a) => `${a.panel_name || a.scenario_id}: ${formatMetric(a.metrics.EAC)}`)
                .join(' · ')}
            </p>
          ) : null}
        </>
      )}
    </section>
  )
}

function attemptText(bucket) {
  return bucket ? `${bucket.succeeded}/${bucket.total}` : '—'
}

function BuildSection({ report }) {
  const sessions = report.build.sessions
  const summary = buildSummary(report)
  return (
    <section className="panel eval-section">
      <SectionHead title="BUILD MODE" sessions={sessions} />
      {!summary ? (
        <p className="eval-empty">No Build Mode session has been recorded for this student yet.</p>
      ) : (
        <>
          <p className="hint">
            Most recent session ({formatTimestamp(summary.session.started_at)}) —{' '}
            {summary.session.panel_name || summary.session.panel_id || 'no panel resolved'}
          </p>
          <div className="kpi-row eval-kpis">
            {BUILD_METRICS.map((code) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} />
            ))}
          </div>
          <div className="table-wrap">
            <table className="log-table">
              <thead>
                <tr>
                  <th>STARTED</th>
                  <th>PANEL</th>
                  <th>STATUS</th>
                  <th>DURATION</th>
                  <th title="succeeded / total">COMPILE</th>
                  <th title="succeeded / total">FLASH</th>
                  <th title="succeeded / total">VALIDATION</th>
                  <th>TTR</th>
                  <th>AID</th>
                  <th>DEI</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} title={`Session ${s.session_id}`}>
                    <td>{formatTimestamp(s.started_at)}</td>
                    <td>{s.panel_name || s.panel_id || '—'}</td>
                    <td>
                      <StatusBadge status={s.status} />
                    </td>
                    <td>{formatDuration(s.duration_seconds) ?? '—'}</td>
                    <td>{attemptText(s.attempts.compile)}</td>
                    <td>{attemptText(s.attempts.flash)}</td>
                    <td>{attemptText(s.attempts.validation)}</td>
                    <MetricCell code="TTR" metric={s.metrics.TTR} />
                    <MetricCell code="AID" metric={s.metrics.AID} />
                    <MetricCell code="DEI" metric={s.metrics.DEI} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function ContextPanel({ report }) {
  const panels = panelsOf(report)
  const { participant } = report
  return (
    <section className="panel eval-context">
      <h3>STUDENT / SESSION CONTEXT</h3>
      <dl>
        <dt>STUDENT</dt>
        <dd>{participant.full_name}</dd>
        <dt>STUDENT NUMBER</dt>
        <dd>{participant.participant_id}</dd>
        <dt>REGISTERED</dt>
        <dd>{formatTimestamp(participant.registered_at)}</dd>
        <dt>PANEL / MODULE</dt>
        <dd>{panels.length ? panels.join(', ') : '—'}</dd>
        <dt>SESSIONS</dt>
        <dd>
          {report.hack.sessions.length} Hack · {report.build.sessions.length} Build
        </dd>
        <dt>REPORT GENERATED</dt>
        <dd>{formatTimestamp(report.generated_at)}</dd>
      </dl>
    </section>
  )
}

export default function Dashboard({ student, role, onBack, onNext, nextStudent, onMenu }) {
  const [state, setState] = useState({ loading: true, error: '', report: null })
  // Bumped by REFRESH; the effect below re-fetches whenever it changes, so a
  // session still running can be re-read without leaving the page.
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let active = true
    fetchEvaluation(student.id)
      .then((report) => active && setState({ loading: false, error: '', report }))
      .catch((error) => active && setState({ loading: false, error: error.message, report: null }))
    return () => {
      active = false
    }
  }, [student.id, reloadKey])

  function refresh() {
    setState((s) => ({ ...s, loading: true, error: '' }))
    setReloadKey((n) => n + 1)
  }

  const { loading, error, report } = state

  function exportReport() {
    if (!report) return
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${student.id}-evaluation.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="page">
      <AppHeader
        title="STUDENT PERFORMANCE DASHBOARD – EVALUATE STUDENT"
        right={
          <>
            {student.name} | ID {student.id}
          </>
        }
        onMenu={onMenu}
      />
      <main className="eval-body">
        {loading && !report ? <p className="eval-empty">Loading evaluation…</p> : null}
        {error ? <p className="form-error">Could not load evaluation: {error}</p> : null}
        {report ? (
          <>
            <ContextPanel report={report} />
            {hasNoSessions(report) ? (
              <p className="eval-empty">
                No evaluation data yet. Metrics appear here once this student completes a Hack Mode or
                Build Mode session.
              </p>
            ) : null}
            <HackSection report={report} />
            <BuildSection report={report} />
          </>
        ) : null}
      </main>
      <InfoBanner>
        Performance metrics computed by the backend from recorded session activity. N/A means the
        metric does not apply to that session; Pending means it cannot be computed yet. Hover a value
        for its definition and source.
      </InfoBanner>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          [ BACK TO MENU ]
        </button>
        <button type="button" onClick={refresh} disabled={loading}>
          [ REFRESH ]
        </button>
        <button type="button" onClick={exportReport} disabled={!report}>
          [ EXPORT REPORT ]
        </button>
        {role === 'professor' ? (
          <button type="button" disabled={!nextStudent} onClick={() => onNext(nextStudent)}>
            [ NEXT STUDENT ]
          </button>
        ) : null}
      </footer>
    </div>
  )
}
