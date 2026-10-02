// Student authentication: registration supplies a full name + student number;
// sign-in supplies the student number alone and the backend returns the stored
// participant (backend/app/participants.py). These pin the request contract and
// that the UI cannot send, or even ask for, a name at sign-in.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerParticipant, signInParticipant } from './trainerApi.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

function withFetch(status, body, fn) {
  const saved = { fetch: globalThis.fetch, window: globalThis.window }
  const calls = []
  globalThis.window = { location: { protocol: 'http:', hostname: 'localhost' } }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) })
    return { ok: status < 400, status, json: async () => body }
  }
  return fn(calls).finally(() => {
    globalThis.fetch = saved.fetch
    globalThis.window = saved.window
  })
}

const PARTICIPANT = { participant_id: '2023-123456', full_name: 'Ada Lovelace', registered_at: 'x' }

test('registration sends the full name and the student number', () =>
  withFetch(201, PARTICIPANT, async (calls) => {
    const result = await registerParticipant({ id: '2023-123456', name: 'Ada Lovelace' })
    assert.equal(calls[0].url, 'http://localhost:8000/api/participants')
    assert.deepEqual(calls[0].body, { participant_id: '2023-123456', full_name: 'Ada Lovelace' })
    assert.equal(result.full_name, 'Ada Lovelace')
  }))

test('sign-in sends the student number and nothing else', () =>
  withFetch(200, PARTICIPANT, async (calls) => {
    const result = await signInParticipant({ id: '2023-123456' })
    assert.equal(calls[0].url, 'http://localhost:8000/api/participants/sign-in')
    assert.deepEqual(calls[0].body, { participant_id: '2023-123456' })
    // The name comes back from the backend's stored participant.
    assert.equal(result.full_name, 'Ada Lovelace')
  }))

test('a name handed to sign-in is never put on the wire', () =>
  withFetch(200, PARTICIPANT, async (calls) => {
    await signInParticipant({ id: '2023-123456', name: 'Typed During Registration' })
    assert.deepEqual(Object.keys(calls[0].body), ['participant_id'])
  }))

test('an unregistered student number surfaces the backend 404 as a non-outdated error', () =>
  withFetch(404, { detail: 'no student is registered with that student number' }, async () => {
    await assert.rejects(signInParticipant({ id: 'UNKNOWN-1' }), (error) => {
      assert.equal(error.status, 404)
      assert.equal(error.endpointMissing, false)
      return true
    })
  }))

test('the sign-in screen only collects a name while registering', () => {
  const text = source('../screens/StudentAccess.jsx')
  // The name field sits inside the `registering` branch…
  assert.match(text, /\{registering \? \(\s*<Field[^>]*id="student-name"/)
  // …and the number is the only thing a sign-in hands to `onEnter`.
  assert.match(text, /else onEnter\(\{ id: form\.id \}\)/)
  assert.doesNotMatch(text, /onEnter\(form\)/)
})

test('App signs a student in by number alone and still registers with both', () => {
  const text = source('../App.jsx')
  assert.match(text, /onEnter=\{\(\{ id \}\) =>/)
  assert.match(text, /signInParticipant\(\{ id \}\)/)
  assert.match(text, /registerParticipant\(\{ id, name \}\)/)
  assert.doesNotMatch(text, /signInParticipant\(\{[^}]*name/)
})

test('instructor sign-in does not use the participant API', () => {
  for (const path of ['../screens/InstructorLogin.jsx', '../screens/ProfessorAccess.jsx']) {
    const text = source(path)
    assert.doesNotMatch(text, /signInParticipant|registerParticipant/, `${path} touches student auth`)
  }
  // The instructor handler still asks for an instructor id + password, as before.
  const app = source('../App.jsx')
  assert.match(app, /onSignIn=\{\(\{ id, password \}\) =>/)
  assert.match(app, /Instructor ID and password are required\./)
})
