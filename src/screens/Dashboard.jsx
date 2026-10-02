import { useEffect, useState } from 'react'
import AppHeader from '../components/AppHeader'
import Icon from '../components/Icon'
import StatusBar from '../components/StatusBar'
import { fetchEvaluation } from '../api/trainerApi'
import { hackEventLabel } from '../hackTerminal/eventLabels'
import {
  BUILD_METRICS,
  HACK_METRICS,
  METRIC_NAMES,
  SECTION_STATE_LABEL,
  SESSION_STATUS_LABEL,
  SESSION_STATUS_TONE,
  attemptText,
  buildSummary,
  formatDuration,
  formatMetric,
  formatTimestamp,
  hackSummary,
  hasNoSessions,
  isMetricAvailable,
  metricDisplay,
  metricNote,
  metricTooltip,
  panelsOf,
  sectionState,
  splitTimestamp,
} from '../evaluation/evaluationModel'

// The Evaluation page. Everything shown is the backend's recorded data and
// the backend's own metric results (GET /api/evaluation/{id},
// backend/app/evaluation.py) — nothing is computed, seeded or filled in here.
// There is deliberately no overall rating or ranking: the platform defines
// none, and this page reports the seven process metrics only.

const VIEWS = [
  { id: 'overview', label: 'Overview', icon: 'nav-overview', title: 'Evaluation' },
  { id: 'hack', label: 'Hack Mode', icon: 'nav-hack', title: 'Hack Mode Evaluation' },
  { id: 'build', label: 'Build Mode', icon: 'nav-build', title: 'Build Mode Evaluation' },
  { id: 'workflow', label: 'Workflow', icon: 'nav-workflow', title: 'Hack, Build, Test Workflow' },
]

function StateChip({ status }) {
  return (
    <span className={`chip ${SESSION_STATUS_TONE[status] || 'is-muted'}`}>
      {SECTION_STATE_LABEL[status] || status}
    </span>
  )
}

function MetricTile({ code, metric, wide = false }) {
  const { text, unit, tone } = metricDisplay(metric)
  const note = metricNote(metric)
  return (
    <div className={`kpi${wide ? ' is-wide' : ''}`} title={metricTooltip(code, metric)}>
      <div className={`kpi-value${tone === 'na' ? ' is-na' : tone === 'pending' ? ' is-pending' : ''}`}>
        {text}
        {unit ? <span className="kpi-unit">{unit}</span> : null}
      </div>
      <div className="kpi-label">
        <b>{code}</b> · {METRIC_NAMES[code]}
      </div>
      {note ? <p className="kpi-note">{note}</p> : null}
    </div>
  )
}

function MetricCell({ code, metric }) {
  return (
    <td className={isMetricAvailable(metric) ? 'num' : 'num is-na'} title={metricTooltip(code, metric)}>
      {formatMetric(metric)}
    </td>
  )
}

function SectionHead({ title, sessions }) {
  return (
    <div className="eval-section-head">
      <h2 className="section-title">{title}</h2>
      <StateChip status={sectionState(sessions)} />
    </div>
  )
}

function RecentLine({ summary, panel }) {
  return (
    <p className="section-sub">
      Most recent session ({formatTimestamp(summary.session.started_at)}), {panel}
    </p>
  )
}

// --- overview ------------------------------------------------------------------

function ContextCard({ report }) {
  const panels = panelsOf(report)
  const { participant } = report
  return (
    <section className="card eval-context" aria-label="Student and session context">
      <h2 className="section-title">Student / session context</h2>
      <div className="context-grid">
        <div>
          <p className="label-caps">Student</p>
          <p className="context-value">{participant.full_name}</p>
        </div>
        <div>
          <p className="label-caps">Student number</p>
          <p className="context-value mono">{participant.participant_id}</p>
        </div>
        <div>
          <p className="label-caps">Registered</p>
          <p className="context-value">{formatTimestamp(participant.registered_at)}</p>
        </div>
        <div>
          <p className="label-caps">Report generated</p>
          <p className="context-value">{formatTimestamp(report.generated_at)}</p>
        </div>
        <div>
          <p className="label-caps">Sessions</p>
          <p className="context-value">
            {report.hack.sessions.length} Hack · {report.build.sessions.length} Build
          </p>
        </div>
        <div className="context-wide">
          <p className="label-caps">Panel / module</p>
          <div className="context-chips">
            {panels.length ? (
              panels.map((name) => (
                <span className="tag" key={name}>
                  {name}
                </span>
              ))
            ) : (
              <span className="muted">—</span>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

function ContextBar({ report, onFull }) {
  const { participant } = report
  return (
    <section className="card context-bar" aria-label="Student and session context">
      <span className="eyebrow">Student / session context</span>
      <span className="context-bar-item">
        <span className="label-caps">Student</span> {participant.full_name}
      </span>
      <span className="context-bar-item">
        <span className="label-caps">No.</span> <span className="mono">{participant.participant_id}</span>
      </span>
      <span className="context-bar-item">
        <span className="label-caps">Sessions</span> {report.hack.sessions.length} Hack · {report.build.sessions.length}{' '}
        Build
      </span>
      <button type="button" className="btn btn-sm context-bar-action" onClick={onFull}>
        Full context
      </button>
    </section>
  )
}

function HackSummaryCard({ report, onOpen }) {
  const summary = hackSummary(report)
  return (
    <section className="card eval-card" aria-label="Hack Mode summary">
      <SectionHead title="Hack Mode" sessions={report.hack.sessions} />
      {summary ? (
        <>
          <RecentLine summary={summary} panel={summary.session.panel_name || summary.session.scenario_id} />
          <div className="kpi-grid is-two">
            {HACK_METRICS.map((code) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} />
            ))}
          </div>
        </>
      ) : (
        <p className="eval-empty">No Hack Mode session has been recorded for this student yet.</p>
      )}
      <button type="button" className="btn btn-block" onClick={onOpen}>
        View Hack Mode sessions
      </button>
    </section>
  )
}

function BuildSummaryCard({ report, onOpen }) {
  const summary = buildSummary(report)
  return (
    <section className="card eval-card" aria-label="Build Mode summary">
      <SectionHead title="Build Mode" sessions={report.build.sessions} />
      {summary ? (
        <>
          <RecentLine
            summary={summary}
            panel={summary.session.panel_name || summary.session.panel_id || 'no panel resolved'}
          />
          <div className="kpi-grid is-two">
            {BUILD_METRICS.map((code, i) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} wide={i === 2} />
            ))}
          </div>
        </>
      ) : (
        <p className="eval-empty">No Build Mode session has been recorded for this student yet.</p>
      )}
      <button type="button" className="btn btn-block" onClick={onOpen}>
        View Build Mode sessions
      </button>
    </section>
  )
}

// --- per-mode views -----------------------------------------------------------------

function HackView({ report }) {
  const sessions = report.hack.sessions
  const summary = hackSummary(report)
  return (
    <section className="card eval-card is-fill" aria-label="Hack Mode evaluation">
      <SectionHead title="Hack Mode" sessions={sessions} />
      {!summary ? (
        <p className="eval-empty">No Hack Mode session has been recorded for this student yet.</p>
      ) : (
        <>
          <RecentLine summary={summary} panel={summary.session.panel_name || summary.session.scenario_id} />
          <div className="kpi-grid is-four">
            {HACK_METRICS.map((code) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} />
            ))}
          </div>
          <div className="history-head">
            <h3>Session history</h3>
            <span className="muted">{sessions.length} Hack {sessions.length === 1 ? 'session' : 'sessions'}</span>
          </div>
          <div className="table-scroll history-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Panel</th>
                  <th>Status</th>
                  <th>Duration</th>
                  <th>Commands</th>
                  <th>ACR</th>
                  <th>RE</th>
                  <th>TTE</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} title={`Session ${s.session_id}`}>
                    <td>{formatTimestamp(s.started_at)}</td>
                    <td className="cell-clip">{s.panel_name || s.scenario_id}</td>
                    <td>
                      <span className={`chip ${SESSION_STATUS_TONE[s.status] || 'is-muted'}`}>
                        {SESSION_STATUS_LABEL[s.status] || s.status}
                      </span>
                    </td>
                    <td className="num">{formatDuration(s.duration_seconds) ?? '—'}</td>
                    <td className="num">{s.command_count}</td>
                    <MetricCell code="ACR" metric={s.metrics.ACR} />
                    <MetricCell code="RE" metric={s.metrics.RE} />
                    <MetricCell code="TTE" metric={s.metrics.TTE} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.hack.activities.length > 1 ? (
            <p className="section-sub">
              {METRIC_NAMES.EAC} (EAC) per activity:{' '}
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

function BuildView({ report }) {
  const sessions = report.build.sessions
  const summary = buildSummary(report)
  return (
    <section className="card eval-card is-fill" aria-label="Build Mode evaluation">
      <SectionHead title="Build Mode" sessions={sessions} />
      {!summary ? (
        <p className="eval-empty">No Build Mode session has been recorded for this student yet.</p>
      ) : (
        <>
          <RecentLine
            summary={summary}
            panel={summary.session.panel_name || summary.session.panel_id || 'no panel resolved'}
          />
          <div className="kpi-grid is-three">
            {BUILD_METRICS.map((code) => (
              <MetricTile key={code} code={code} metric={summary.metrics[code]} />
            ))}
          </div>
          <div className="history-head">
            <h3>Session history</h3>
            <span className="muted">{sessions.length} Build {sessions.length === 1 ? 'session' : 'sessions'}</span>
          </div>
          <div className="table-scroll history-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Panel</th>
                  <th>Status</th>
                  <th>Duration</th>
                  <th title="succeeded / total">Compile</th>
                  <th title="succeeded / total">Flash</th>
                  <th title="succeeded / total">Validation</th>
                  <th>TTR</th>
                  <th>AID</th>
                  <th>DEI</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} title={`Session ${s.session_id}`}>
                    <td>{formatTimestamp(s.started_at)}</td>
                    <td className="cell-clip">{s.panel_name || s.panel_id || '—'}</td>
                    <td>
                      <span className={`chip ${SESSION_STATUS_TONE[s.status] || 'is-muted'}`}>
                        {SESSION_STATUS_LABEL[s.status] || s.status}
                      </span>
                    </td>
                    <td className="num">{formatDuration(s.duration_seconds) ?? '—'}</td>
                    <td className="num">{attemptText(s.attempts.compile)}</td>
                    <td className="num">{attemptText(s.attempts.flash)}</td>
                    <td className="num">{attemptText(s.attempts.validation)}</td>
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

// --- workflow -------------------------------------------------------------------------

// Hack -> Build -> Test, from the most recent recorded session of each mode.
// Only recorded fields appear: session start/duration/status, the scenario
// events the Hack session reached, and the Build session's compile / flash /
// validation attempt counts. The backend stores no per-attempt timestamps, so
// none are shown for the Build and Test steps.
function Phase({ index, tone, title, children }) {
  return (
    <li className={`phase is-${tone}`}>
      <span className="phase-node" aria-hidden="true">
        {index}
      </span>
      <h3>{title}</h3>
      {children}
    </li>
  )
}

function Fact({ label, children }) {
  return (
    <div className="phase-fact">
      <dt className="label-caps">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function WorkflowView({ report }) {
  const hack = hackSummary(report)
  const build = buildSummary(report)
  const hackSession = hack?.session
  const buildSession = build?.session
  return (
    <section className="card eval-card" aria-label="Hack, Build, Test workflow">
      <div className="eval-section-head">
        <h2 className="section-title">Hack, Build, Test workflow</h2>
        <div className="legend">
          <span className="legend-item is-hack">Hack Mode</span>
          <span className="legend-item is-build">Build and test</span>
        </div>
      </div>
      <p className="section-sub">
        Built from the most recent recorded Hack and Build session. Build attempts are counted, not timestamped.
      </p>
      <ol className="phases">
        <Phase index={1} tone="hack" title="Hack">
          {hackSession ? (
            <dl>
              <Fact label="Status">
                <span className={`chip ${SESSION_STATUS_TONE[hackSession.status] || 'is-muted'}`}>
                  {SESSION_STATUS_LABEL[hackSession.status] || hackSession.status}
                </span>
              </Fact>
              <Fact label="Started">{formatTimestamp(hackSession.started_at)}</Fact>
              <Fact label="Panel">{hackSession.panel_name || hackSession.scenario_id}</Fact>
              <Fact label="Duration">{formatDuration(hackSession.duration_seconds) ?? '—'}</Fact>
              <Fact label="Commands">{hackSession.command_count}</Fact>
              <Fact label="Events reached">
                {hackSession.events?.length ? (
                  <span className="context-chips">
                    {hackSession.events.map((event) => (
                      <span className="tag" key={event}>
                        {hackEventLabel(event)}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="muted">None recorded</span>
                )}
              </Fact>
            </dl>
          ) : (
            <p className="muted">No Hack Mode session recorded.</p>
          )}
        </Phase>
        <Phase index={2} tone="build" title="Build">
          {buildSession ? (
            <dl>
              <Fact label="Status">
                <span className={`chip ${SESSION_STATUS_TONE[buildSession.status] || 'is-muted'}`}>
                  {SESSION_STATUS_LABEL[buildSession.status] || buildSession.status}
                </span>
              </Fact>
              <Fact label="Started">{formatTimestamp(buildSession.started_at)}</Fact>
              <Fact label="Panel">{buildSession.panel_name || buildSession.panel_id || '—'}</Fact>
              <Fact label="Duration">{formatDuration(buildSession.duration_seconds) ?? '—'}</Fact>
              <Fact label="Compile (succeeded/total)">{attemptText(buildSession.attempts.compile)}</Fact>
              <Fact label="Flash (succeeded/total)">{attemptText(buildSession.attempts.flash)}</Fact>
            </dl>
          ) : (
            <p className="muted">No Build Mode session recorded.</p>
          )}
        </Phase>
        <Phase index={3} tone="build" title="Test">
          {buildSession ? (
            <dl>
              <Fact label="Validation (succeeded/total)">{attemptText(buildSession.attempts.validation)}</Fact>
              <Fact label={`${METRIC_NAMES.TTR} (TTR)`}>
                <span className="phase-metric">
                  {metricDisplay(buildSession.metrics.TTR).text}
                  {metricDisplay(buildSession.metrics.TTR).unit ? (
                    <span className="kpi-unit">{metricDisplay(buildSession.metrics.TTR).unit}</span>
                  ) : null}
                </span>
                <span className="phase-note">{metricNote(buildSession.metrics.TTR)}</span>
              </Fact>
            </dl>
          ) : (
            <p className="muted">No validation recorded.</p>
          )}
        </Phase>
      </ol>
    </section>
  )
}

// --- page --------------------------------------------------------------------------------

export default function Dashboard({ student, role, onBack, onNext, nextStudent, onMenu }) {
  const [state, setState] = useState({ loading: true, error: '', report: null })
  // Bumped by REFRESH; the effect below re-fetches whenever it changes, so a
  // session still running can be re-read without leaving the page.
  const [reloadKey, setReloadKey] = useState(0)
  const [view, setView] = useState('overview')

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
  const current = VIEWS.find((v) => v.id === view) || VIEWS[0]
  const generated = splitTimestamp(report?.generated_at)

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
    <div className="screen screen-fixed">
      <AppHeader
        title={current.title}
        onMenu={onMenu}
        actions={
          <>
            <button
              type="button"
              className="icon-btn is-quiet"
              aria-label="Refresh evaluation"
              title="Re-read the evaluation from the backend"
              onClick={refresh}
              disabled={loading}
            >
              <Icon name="refresh" size={16} />
            </button>
            {role === 'professor' ? (
              <button type="button" className="btn" disabled={!nextStudent} onClick={() => onNext(nextStudent)}>
                Next student
              </button>
            ) : null}
            <button type="button" className="btn btn-primary" onClick={exportReport} disabled={!report}>
              <Icon name="download" size={16} />
              Export report
            </button>
          </>
        }
      />

      <div className="screen-body">
        <nav className="card eval-nav" aria-label="Evaluation sections">
          <p className="nav-group" style={{ marginTop: 6 }}>
            Report
          </p>
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              className={`nav-item${v.id === view ? ' is-on' : ''}`}
              aria-current={v.id === view ? 'page' : undefined}
              onClick={() => setView(v.id)}
            >
              <Icon name={v.icon} size={18} />
              {v.label}
            </button>
          ))}
          <div className="eval-nav-foot">
            <button type="button" className="nav-item" onClick={onBack}>
              <Icon name="arrow-left" size={16} />
              {role === 'professor' ? 'Back to students' : 'Back to menu'}
            </button>
            <div className="generated-card">
              <p className="label-caps">Report generated</p>
              <p className="mono">
                {generated ? (
                  <>
                    <span>{generated.date}</span>
                    <span>{generated.time}</span>
                  </>
                ) : (
                  '—'
                )}
              </p>
            </div>
          </div>
        </nav>

        <main className="eval-main">
          {loading && !report ? <p className="eval-empty">Loading evaluation…</p> : null}
          {error ? (
            <div className="notice is-danger" role="alert">
              <Icon name="warning" size={14} />
              Could not load evaluation: {error}
            </div>
          ) : null}
          {report ? (
            <>
              {view === 'overview' ? <ContextCard report={report} /> : <ContextBar report={report} onFull={() => setView('overview')} />}
              {hasNoSessions(report) ? (
                <div className="notice">
                  <Icon name="info" size={14} />
                  No evaluation data yet. Metrics appear here once this student completes a Hack Mode or Build Mode
                  session.
                </div>
              ) : null}
              {view === 'overview' ? (
                <div className="eval-cards">
                  <HackSummaryCard report={report} onOpen={() => setView('hack')} />
                  <BuildSummaryCard report={report} onOpen={() => setView('build')} />
                </div>
              ) : null}
              {view === 'hack' ? <HackView report={report} /> : null}
              {view === 'build' ? <BuildView report={report} /> : null}
              {view === 'workflow' ? <WorkflowView report={report} /> : null}
              <p className="eval-legend">
                Metrics are computed by the backend from recorded session activity. N/A means a metric does not apply
                to that session; Pending means it cannot be computed yet. Hover a value for its definition.
              </p>
            </>
          ) : null}
        </main>
      </div>

      <StatusBar>
        <span>
          STUDENT: <span className="text-strong">{student.name}</span>
        </span>
        <span className="hw-sep" aria-hidden="true">
          |
        </span>
        <span>
          NO.: <span className="mono">{student.id}</span>
        </span>
      </StatusBar>
    </div>
  )
}
