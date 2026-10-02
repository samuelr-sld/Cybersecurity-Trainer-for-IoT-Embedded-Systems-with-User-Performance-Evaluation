import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  BUILD_METRICS,
  HACK_METRICS,
  METRIC_NAMES,
  SESSION_STATUS_TONE,
  attemptText,
  buildSummary,
  formatDuration,
  formatMetric,
  hackSummary,
  isMetricAvailable,
  latestSession,
  metricDisplay,
  metricNote,
  metricTooltip,
  panelsOf,
  sectionState,
} from './evaluationModel.js'
import { withParticipant } from '../api/trainerApi.js'

const computed = (code, value, unit) => ({ code, status: 'computed', value, unit, detail: 'd' })
const na = (code, unit) => ({ code, status: 'not_applicable', value: null, unit, detail: 'why not' })
const pending = (code, unit) => ({ code, status: 'not_yet_computable', value: null, unit, detail: '' })

// Shaped exactly like GET /api/evaluation/{id} (backend/app/evaluation.py).
const REPORT = {
  participant: { participant_id: 'TEST-STUDENT-01', full_name: 'Test Student One', registered_at: '2026-01-01T00:00:00+00:00' },
  generated_at: '2026-01-01T01:00:00+00:00',
  hack: {
    sessions: [
      {
        session_id: 'h2',
        scenario_id: 'smart-home-mqtt-control',
        panel_id: 'smart-home-mqtt-control',
        panel_name: 'SMART HOME MQTT CONTROL SYSTEM',
        status: 'completed',
        metrics: { ACR: computed('ACR', 100, '%'), RE: computed('RE', 75, '%'), TTE: computed('TTE', 125.4, 's') },
      },
      {
        session_id: 'h1',
        scenario_id: 'smart-home-mqtt-control',
        panel_name: 'SMART HOME MQTT CONTROL SYSTEM',
        status: 'incomplete',
        metrics: { ACR: computed('ACR', 0, '%'), RE: na('RE', '%'), TTE: na('TTE', 's') },
      },
    ],
    activities: [{ scenario_id: 'smart-home-mqtt-control', metrics: { EAC: computed('EAC', 2, 'sessions') } }],
  },
  build: { sessions: [] },
}

test('the page shows exactly the established metric codes', () => {
  assert.deepEqual(HACK_METRICS, ['ACR', 'RE', 'EAC', 'TTE'])
  assert.deepEqual(BUILD_METRICS, ['TTR', 'AID', 'DEI'])
})

test('computed metrics are formatted in their own unit', () => {
  assert.equal(formatMetric(computed('ACR', 60, '%')), '60.0%')
  assert.equal(formatMetric(computed('TTE', 125.4, 's')), '2:05')
  assert.equal(formatMetric(computed('TTE', 3725, 's')), '1:02:05')
  assert.equal(formatMetric(computed('EAC', 2, 'sessions')), '2')
  assert.equal(formatMetric(computed('AID', 0.4, 'attempts/min')), '0.40/min')
})

test('a real computed zero is shown as zero', () => {
  assert.equal(formatMetric(computed('ACR', 0, '%')), '0.0%')
  assert.equal(isMetricAvailable(computed('ACR', 0, '%')), true)
})

test('unavailable metrics are never rendered as a number', () => {
  assert.equal(formatMetric(na('RE', '%')), 'N/A')
  assert.equal(formatMetric(pending('TTE', 's')), 'Pending')
  assert.equal(formatMetric(null), 'No data')
  assert.equal(formatMetric(undefined), 'No data')
  assert.equal(isMetricAvailable(na('RE', '%')), false)
  assert.doesNotMatch(formatMetric(na('RE', '%')), /\d/)
})

test('tooltips carry the definition and the backend reason', () => {
  const tip = metricTooltip('RE', na('RE', '%'))
  assert.match(tip, /reconnaissance/i)
  assert.match(tip, /why not/)
})

test('summaries use the newest session and its activity EAC', () => {
  const hack = hackSummary(REPORT)
  assert.equal(hack.session.session_id, 'h2')
  assert.equal(hack.metrics.ACR.value, 100)
  assert.equal(hack.metrics.EAC.value, 2)
  assert.equal(buildSummary(REPORT), null)
})

test('section states distinguish not started from session outcomes', () => {
  assert.equal(sectionState([]), 'not_started')
  assert.equal(sectionState(undefined), 'not_started')
  assert.equal(sectionState(REPORT.hack.sessions), 'completed')
  assert.equal(sectionState([{ status: 'in_progress' }]), 'in_progress')
})

test('empty report has no summaries and no panels', () => {
  const empty = { ...REPORT, hack: { sessions: [], activities: [] }, build: { sessions: [] } }
  assert.equal(hackSummary(empty), null)
  assert.equal(buildSummary(empty), null)
  assert.deepEqual(panelsOf(empty), [])
  assert.deepEqual(panelsOf(REPORT), ['SMART HOME MQTT CONTROL SYSTEM'])
})

test('durations format and absent durations stay absent', () => {
  assert.equal(formatDuration(59), '0:59')
  assert.equal(formatDuration(null), null)
})

test('mode sockets carry the participant only when there is one', () => {
  assert.equal(withParticipant('ws://h:8000/ws/hack', 'TEST-STUDENT-01'), 'ws://h:8000/ws/hack?participant=TEST-STUDENT-01')
  assert.equal(withParticipant('ws://h:8000/ws/hack', undefined), 'ws://h:8000/ws/hack')
  assert.equal(withParticipant('ws://h/x?a=1', 'A B'), 'ws://h/x?a=1&participant=A%20B')
})

test('the Evaluation and professor screens read no seeded demo data', () => {
  for (const file of [
    '../screens/Dashboard.jsx',
    '../screens/ProfessorAccess.jsx',
    '../screens/InstructorLogin.jsx',
    '../screens/InstructorStudents.jsx',
  ]) {
    // Comments are stripped: they may explain that no score exists.
    const source = readFileSync(new URL(file, import.meta.url), 'utf8').replace(/^\s*\/\/.*$/gm, '')
    assert.doesNotMatch(source, /from '\.\.\/data'/)
    assert.doesNotMatch(source, /STUDENTS/)
    assert.doesNotMatch(source, /\bscore\b|\bgrade\b/i)
  }
})

test('every established metric has a name, and only those seven', () => {
  assert.deepEqual(Object.keys(METRIC_NAMES), [...HACK_METRICS, ...BUILD_METRICS])
  assert.equal(METRIC_NAMES.ACR, 'Attack Completion Rate')
  assert.equal(METRIC_NAMES.EAC, 'Exploit Action Completeness')
  assert.equal(METRIC_NAMES.AID, 'Attempt-to-Iteration Depth')
})

test('metric tiles split the figure from its unit without changing the number', () => {
  assert.deepEqual(metricDisplay(computed('ACR', 75, '%')), { text: '75.0%', unit: null, tone: 'value' })
  assert.deepEqual(metricDisplay(computed('TTE', 272, 's')), { text: '4:32', unit: 'm:ss', tone: 'value' })
  assert.deepEqual(metricDisplay(computed('TTR', 3725, 's')), { text: '1:02:05', unit: 'h:mm:ss', tone: 'value' })
  assert.deepEqual(metricDisplay(computed('AID', 0, 'attempts/min')), { text: '0.00', unit: 'attempts/min', tone: 'value' })
  assert.deepEqual(metricDisplay(computed('EAC', 2, 'sessions')), { text: '2', unit: 'sessions', tone: 'value' })
  assert.equal(metricDisplay(computed('EAC', 1, 'sessions')).unit, 'session')
})

test('an unavailable metric never gets a number or a unit', () => {
  assert.deepEqual(metricDisplay(na('RE', '%')), { text: 'N/A', unit: null, tone: 'na' })
  assert.deepEqual(metricDisplay(pending('TTE', 's')), { text: 'Pending', unit: null, tone: 'pending' })
  assert.deepEqual(metricDisplay(null), { text: 'No data', unit: null, tone: 'na' })
})

test('metric notes say why there is no number, using the backend reason', () => {
  assert.equal(metricNote(na('RE', '%')), 'Not evaluated. Why not.')
  assert.equal(metricNote(pending('TTE', 's')), 'Not yet computable.')
  assert.equal(metricNote(computed('ACR', 60, '%')), 'D.')
  assert.equal(metricNote({ status: 'computed', value: 1, detail: '3/4 declared objectives completed' }), '3/4 declared objectives completed.')
  assert.match(metricNote(null), /no data/i)
})

test('attempt buckets read succeeded/total', () => {
  assert.equal(attemptText({ succeeded: 1, total: 3 }), '1/3')
  assert.equal(attemptText(undefined), '—')
})

test('the latest session is the most recently started across both modes', () => {
  const report = {
    hack: { sessions: [{ session_id: 'h', started_at: '2026-10-02T01:00:00+00:00' }] },
    build: { sessions: [{ session_id: 'b', started_at: '2026-10-02T02:00:00+00:00' }] },
  }
  assert.equal(latestSession(report).mode, 'build')
  assert.equal(latestSession({ ...report, build: { sessions: [] } }).mode, 'hack')
  assert.equal(latestSession({ ...report, hack: { sessions: [] } }).session.session_id, 'b')
  assert.equal(latestSession({ hack: { sessions: [] }, build: { sessions: [] } }), null)
  assert.equal(latestSession(undefined), null)
})

test('every session status has a chip tone, and colour is never the only cue', () => {
  for (const status of ['completed', 'incomplete', 'in_progress', 'interrupted', 'not_started']) {
    assert.match(SESSION_STATUS_TONE[status], /^is-/)
  }
})
