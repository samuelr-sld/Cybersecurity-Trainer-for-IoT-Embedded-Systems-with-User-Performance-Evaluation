import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  BUILD_METRICS,
  HACK_METRICS,
  buildSummary,
  formatDuration,
  formatMetric,
  hackSummary,
  isMetricAvailable,
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
  for (const file of ['../screens/Dashboard.jsx', '../screens/ProfessorAccess.jsx']) {
    // Comments are stripped: they may explain that no score exists.
    const source = readFileSync(new URL(file, import.meta.url), 'utf8').replace(/^\s*\/\/.*$/gm, '')
    assert.doesNotMatch(source, /from '\.\.\/data'/)
    assert.doesNotMatch(source, /STUDENTS/)
    assert.doesNotMatch(source, /\bscore\b|\bgrade\b/i)
  }
})
