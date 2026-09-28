// Regression guards for the Evaluation page's data path. A newly registered
// student once saw the seeded demo student's metrics under their own name:
// the old App.jsx resolved an unknown id to `{ ...STUDENTS[0], ...user }`.
// These tests pin that the production path can only render the backend's
// report for the signed-in participant, or an honest empty state / error.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { hasNoSessions } from './evaluationModel.js'
import { ApiError, fetchEvaluation, signInParticipant } from '../api/trainerApi.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

// Every module on the student → Evaluation path.
const EVALUATION_PATH = [
  '../App.jsx',
  '../screens/Dashboard.jsx',
  '../screens/StudentAccess.jsx',
  '../screens/ProfessorAccess.jsx',
  '../screens/MainMenu.jsx',
  '../api/trainerApi.js',
  './evaluationModel.js',
]

test('no module on the evaluation path imports the seeded demo data', () => {
  for (const path of EVALUATION_PATH) {
    const text = source(path)
    assert.doesNotMatch(text, /from\s+['"][./]*\/?data(\.js)?['"]/, `${path} imports src/data.js`)
    assert.doesNotMatch(text, /\bSTUDENTS\b/, `${path} references the demo STUDENTS list`)
  }
})

test('Dashboard renders only the fetched report for the student it was given', () => {
  const text = source('../screens/Dashboard.jsx')
  assert.match(text, /fetchEvaluation\(student\.id\)/)
  // The report state starts empty and is only ever set from the API result.
  assert.match(text, /useState\(\{ loading: true, error: '', report: null \}\)/)
  assert.doesNotMatch(text, /localStorage|sessionStorage/)
})

test('hasNoSessions is true only when neither mode has a recorded session', () => {
  const empty = { hack: { sessions: [], activities: [] }, build: { sessions: [] } }
  assert.equal(hasNoSessions(empty), true)
  assert.equal(hasNoSessions({ ...empty, hack: { sessions: [{}], activities: [] } }), false)
  assert.equal(hasNoSessions({ ...empty, build: { sessions: [{}] } }), false)
})

function withFetch(status, body, fn) {
  const saved = { fetch: globalThis.fetch, window: globalThis.window }
  const calls = []
  globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' } }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: status < 400, status, json: async () => body }
  }
  return fn(calls).finally(() => {
    globalThis.fetch = saved.fetch
    globalThis.window = saved.window
  })
}

test('fetchEvaluation requests exactly the given participant id', () =>
  withFetch(200, { participant: { participant_id: 'EVAL LIVE/001' } }, async (calls) => {
    await fetchEvaluation('EVAL LIVE/001')
    assert.equal(calls[0].url, 'http://localhost:8000/api/evaluation/EVAL%20LIVE%2F001')
  }))

test('a backend without the API is reported as outdated, not as an unknown student', () =>
  withFetch(404, { detail: 'Not Found' }, async () => {
    await assert.rejects(signInParticipant({ id: 'x', name: 'y' }), (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.endpointMissing, true)
      assert.match(error.message, /restart the backend/)
      return true
    })
  }))

test("the endpoint's own 404 keeps its meaning", () =>
  withFetch(404, { detail: 'no such registered participant' }, async () => {
    await assert.rejects(fetchEvaluation('nobody'), (error) => {
      assert.equal(error.endpointMissing, false)
      assert.equal(error.message, 'no such registered participant')
      return true
    })
  }))
