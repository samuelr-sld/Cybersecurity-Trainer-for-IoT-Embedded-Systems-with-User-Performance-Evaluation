import AppHeader from '../components/AppHeader'
import { STUDENTS } from '../data'

function BarChart({ values, labels }) {
  const max = Math.max(...values, 1)
  return (
    <div className="chart bar-chart" role="img" aria-label="Hack mode metrics per attempt">
      {values.map((v, i) => (
        <div key={labels[i]} className="bar-col">
          <div className="bar" style={{ height: `${(v / max) * 100}%` }} />
          <span>{labels[i]}</span>
        </div>
      ))}
    </div>
  )
}

function LineChart({ values }) {
  const w = 320
  const h = 140
  const max = Math.max(...values, 1)
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * (w - 20) + 10
      const y = h - 16 - (v / max) * (h - 28)
      return `${x},${y}`
    })
    .join(' ')
  return (
    <svg className="chart line-chart" viewBox={`0 0 ${w} ${h}`} aria-label="Time-to-resolution trend">
      <polyline fill="none" stroke="#111" strokeWidth="2" points={pts} />
      {values.map((v, i) => {
        const x = (i / (values.length - 1)) * (w - 20) + 10
        const y = h - 16 - (v / max) * (h - 28)
        return <circle key={i} cx={x} cy={y} r="3" fill="#111" />
      })}
    </svg>
  )
}

function RadarChart({ values }) {
  const labels = ['Attempt Density', 'Debug Eff.', 'Resolution', 'Validation', 'Completion']
  const cx = 110
  const cy = 110
  const r = 78
  const pts = values.map((v, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5
    return [cx + Math.cos(a) * r * v, cy + Math.sin(a) * r * v]
  })
  const poly = pts.map((p) => p.join(',')).join(' ')
  const axes = labels.map((label, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5
    const x = cx + Math.cos(a) * r
    const y = cy + Math.sin(a) * r
    const lx = cx + Math.cos(a) * (r + 18)
    const ly = cy + Math.sin(a) * (r + 16)
    return { label, x, y, lx, ly }
  })
  return (
    <svg className="chart radar" viewBox="0 0 220 220" aria-label="Remediation metrics radar">
      {axes.map((ax) => (
        <line key={ax.label} x1={cx} y1={cy} x2={ax.x} y2={ax.y} stroke="#bbb" />
      ))}
      <polygon points={axes.map((a) => `${a.x},${a.y}`).join(' ')} fill="none" stroke="#999" />
      <polygon points={poly} fill="rgba(80,80,80,0.35)" stroke="#111" />
      {axes.map((ax) => (
        <text key={ax.label} x={ax.lx} y={ax.ly} textAnchor="middle" fontSize="8">
          {ax.label}
        </text>
      ))}
    </svg>
  )
}

const RESULT_CLASS = {
  OK: 'ok',
  SUCCESS: 'ok',
  SECURED: 'ok',
  ERROR: 'bad',
}

export default function Dashboard({ student, role, onBack, onNext, onMenu }) {
  const m = student.metrics
  const labels = ['Recon Eff.', 'Attempt 1', 'Attempt 2', 'Attempt 3', 'Attempt 4']

  function exportReport() {
    const body = [
      `Student: ${student.name}`,
      `ID: ${student.id}`,
      `Scenario: ${student.scenario}`,
      `Attack completion: ${m.attackCompletion}%`,
      `Time-to-exploitation: ${m.timeToExploit}`,
      `Time-to-resolution: ${m.timeToResolution}`,
      `Attempts: ${m.attempts}`,
      `Debug index: ${m.debugIndex}`,
    ].join('\n')
    const blob = new Blob([body], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${student.id}-report.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const nextLabel = role === 'professor' ? '[ NEXT STUDENT ]' : '[ NEXT STUDENT ]'
  const idx = STUDENTS.findIndex((s) => s.id === student.id)
  const canNext = role === 'professor' && idx >= 0 && idx < STUDENTS.length - 1

  return (
    <div className="page">
      <AppHeader
        title="STUDENT PERFORMANCE DASHBOARD – EVALUATE STUDENT"
        right={
          <>
            {student.name} | ID {student.id} | Scenario: {student.scenario}
          </>
        }
        onMenu={onMenu}
      />
      <div className="kpi-row">
        <div className="kpi">
          <strong>{m.attackCompletion}%</strong>
          <span>ATTACK COMPLETION RATE</span>
        </div>
        <div className="kpi">
          <strong>{m.timeToExploit}</strong>
          <span>TIME-TO-EXPLOITATION</span>
        </div>
        <div className="kpi">
          <strong>{m.timeToResolution}</strong>
          <span>TIME-TO-RESOLUTION</span>
        </div>
        <div className="kpi">
          <strong>{m.attempts}</strong>
          <span>EXPLOITATION ATTEMPTS</span>
        </div>
        <div className="kpi">
          <strong>{m.debugIndex}</strong>
          <span>DEBUGGING EFFICIENCY INDEX</span>
        </div>
      </div>
      <div className="dash-row">
        <section className="panel">
          <h3>HACK MODE METRICS (PER ATTEMPT)</h3>
          <BarChart values={m.bars} labels={labels} />
        </section>
        <section className="panel">
          <h3>TIME-TO-RESOLUTION TREND</h3>
          <LineChart values={m.trend} />
        </section>
      </div>
      <div className="dash-row">
        <section className="panel">
          <h3>REMEDIATION METRICS (RADAR)</h3>
          <RadarChart values={m.radar} />
        </section>
        <section className="panel">
          <h3>ACTIVITY / ATTEMPT LOG</h3>
          <table className="log-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>PHASE</th>
                <th>ACTION</th>
                <th>RESULT</th>
              </tr>
            </thead>
            <tbody>
              {student.log.map((row) => (
                <tr key={`${row.time}-${row.action}`}>
                  <td>{row.time}</td>
                  <td>{row.phase}</td>
                  <td>{row.action}</td>
                  <td>
                    <span className={`badge ${RESULT_CLASS[row.result] || ''}`}>{row.result}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          [ BACK TO MENU ]
        </button>
        <button type="button" onClick={exportReport}>
          [ EXPORT REPORT ]
        </button>
        <button type="button" disabled={!canNext} onClick={() => onNext(STUDENTS[idx + 1])}>
          {nextLabel}
        </button>
      </footer>
    </div>
  )
}
